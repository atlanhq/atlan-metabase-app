"""Fixtures for integration tests.

Tests run entirely in-process:
  - Temporal starts as an embedded dev server via the SDK's
    ``embedded_runtime()``.
  - Secret / state / storage infrastructure is mocked.
  - **Metabase runs as a session-scoped Docker container** brought up via
    testcontainers; a minimal seed (one collection + one MBQL question
    against Metabase's built-in sample database) is applied via the
    Metabase HTTP API before tests start.

Pattern mirrors ``atlan-mysql-app/tests/integration/conftest.py`` which
boots a ``MySqlContainer`` and seeds it from ``fixtures/seed.sql``. The
Metabase docker image is pinned to the same version the full-DAG e2e
overlay uses (``.github/e2e/e2e-full-docker-compose.yaml``) — bump them
together.

Escape hatch: when ``E2E_METABASE_HOST`` is set, the container fixture is
bypassed and tests run against the preconfigured external Metabase (same
shape as mysql-app's ``MYSQL_HOST`` short-circuit). Useful for local
debugging against a known-good tenant when Docker is unavailable.

Run tests with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import orjson
import pytest
import pytest_asyncio
from application_sdk.dev import embedded_runtime
from application_sdk.execution._temporal.backend import TemporalExecutorBackend
from application_sdk.execution._temporal.converter import create_data_converter_for_app
from application_sdk.execution._temporal.worker import create_worker
from application_sdk.infrastructure.context import (
    InfrastructureContext,
    set_infrastructure,
)
from application_sdk.observability.logger_adaptor import get_logger
from application_sdk.observability.observability import AtlanObservability
from application_sdk.storage import create_local_store, create_memory_store
from application_sdk.testing.mocks import MockSecretStore, MockStateStore
from temporalio.client import Client

# Trigger MetabaseApp registration before create_worker is called.
from app.connector import MetabaseApp  # noqa: F401

# Pre-wire a memory store as the deployment objectstore so the periodic
# observability flush does not keep retrying and spamming warnings in tests.
AtlanObservability._deployment_store = create_memory_store()

logger = get_logger("integration")

_TASK_QUEUE = "metabase-queue"
_CREDENTIAL_KEY = "metabase"

# Pin matches .github/e2e/e2e-full-docker-compose.yaml — keep in sync.
_METABASE_IMAGE = "metabase/metabase:v0.61.2.3"
_METABASE_PORT = 3000
_METABASE_BOOT_TIMEOUT_S = 240  # JVM cold-start + initial migration
_METABASE_BOOT_POLL_S = 2

# Seed admin — same convention as the e2e compose overlay so anyone
# debugging against a running container has a single set of credentials
# in their head.
_ADMIN_EMAIL = "e2e@atlan.com"
_ADMIN_PASSWORD = "AtlanMetabaseE2E!1"


class AppExecutor:
    """Compatibility shim wrapping TemporalExecutorBackend for integration tests."""

    def __init__(self, backend: TemporalExecutorBackend) -> None:
        self._backend = backend

    async def execute_app(
        self,
        app_cls: Any,
        input_data: Any,
        *,
        execution_id_prefix: str = "",
    ) -> Any:
        from application_sdk.app.context import AppContext
        from application_sdk.execution.retry import RetryPolicy

        app_name = getattr(app_cls, "_app_name", execution_id_prefix or "app")
        context = AppContext(
            app_name=app_name,
            app_version="0.0.0",
            run_id=execution_id_prefix or app_name,
        )
        return await self._backend.execute(
            app_cls,
            input_data,
            context=context,
            retry_policy=RetryPolicy(),
        )


# ---------------------------------------------------------------------------
# Docker availability — graceful skip when Docker is unreachable.
# ---------------------------------------------------------------------------


def _metabase_host_preconfigured() -> bool:
    """An external Metabase has been pointed at via env vars.

    Mirrors ``_mysql_host_preconfigured`` in ``atlan-mysql-app/tests/integration/conftest.py``.
    """
    return bool(os.environ.get("E2E_METABASE_HOST"))


def _docker_available() -> bool:
    """Docker daemon reachable from this process."""
    try:
        import docker  # type: ignore[import-not-found]

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001 — any failure means "no Docker"
        logger.debug("Docker daemon not reachable", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Metabase container fixture
# ---------------------------------------------------------------------------


def _wait_for_metabase_ready(base_url: str) -> None:
    """Poll ``/api/health`` until Metabase reports ``status=ok``.

    Metabase's first boot runs database migrations against the built-in H2
    metadata DB before the API serves traffic — typically 30-60 s on
    CI-class hardware, slower on cold runners.
    """
    deadline = time.monotonic() + _METABASE_BOOT_TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/health", timeout=5.0)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(_METABASE_BOOT_POLL_S)
    raise TimeoutError(
        f"Metabase did not become healthy at {base_url} within "
        f"{_METABASE_BOOT_TIMEOUT_S}s (last error: {last_err!r})"
    )


def _seed_metabase(base_url: str) -> None:
    """Bootstrap admin + create the minimum content the workflow needs.

    Integration tests assert workflow *shape* (extract completes, output
    files exist, attributes present) — not asset volume. So we only need:

    - 1 admin user (Metabase requires ``/api/setup`` before any other call)
    - 1 collection (so ``METABASECOLLECTION/result-0.json`` has ≥1 record)
    - 1 MBQL question referencing the built-in sample database (so
      ``METABASEQUESTION`` is populated; QI input keys are unit-tested
      separately, so an MBQL — not native-SQL — question is sufficient)

    Idempotent: a second call against an already-set-up Metabase short-
    circuits at the setup-token fetch (Metabase returns no token once
    setup is complete) and returns cleanly. The fixture is session-
    scoped so this only runs once per pytest invocation anyway.
    """
    client = httpx.Client(base_url=base_url, timeout=30.0)
    try:
        # /api/session/properties returns ``setup-token`` until setup runs.
        props = client.get("/api/session/properties").json()
        setup_token = props.get("setup-token")
        if not setup_token:
            logger.info("Metabase already initialized; skipping admin setup")
            return

        logger.info("Bootstrapping Metabase admin user")
        setup_resp = client.post(
            "/api/setup",
            json={
                "token": setup_token,
                "user": {
                    "first_name": "E2E",
                    "last_name": "Admin",
                    "email": _ADMIN_EMAIL,
                    "password": _ADMIN_PASSWORD,
                    "site_name": "Atlan Integration Tests",
                },
                "prefs": {
                    "site_name": "Atlan Integration Tests",
                    "allow_tracking": False,
                },
                # Don't add a real DB here — the built-in sample database
                # is auto-loaded and is all the workflow needs.
                "database": None,
            },
        )
        setup_resp.raise_for_status()
        session_id = setup_resp.json()["id"]
        headers = {"X-Metabase-Session": session_id}

        # Find the sample database (Metabase auto-loads one on first boot).
        dbs = client.get("/api/database", headers=headers).json()
        # /api/database returns either a list (older versions) or
        # ``{"data": [...]}`` (newer versions) — handle both.
        db_list = dbs if isinstance(dbs, list) else dbs.get("data", [])
        sample = next(
            (d for d in db_list if d.get("is_sample") or "sample" in d.get("name", "").lower()),
            None,
        )

        logger.info("Creating integration-tests collection")
        col = client.post(
            "/api/collection",
            headers=headers,
            json={
                "name": "Integration Tests",
                "color": "#509EE3",
                "description": "Created by tests/integration/conftest.py",
            },
        )
        col.raise_for_status()
        collection_id = col.json()["id"]

        if sample:
            # Find the first table in the sample DB to reference in a card.
            db_id = sample["id"]
            meta = client.get(
                f"/api/database/{db_id}/metadata", headers=headers
            ).json()
            tables = meta.get("tables") or []
            if tables:
                table = tables[0]
                logger.info("Creating MBQL question referencing %s", table["name"])
                client.post(
                    "/api/card",
                    headers=headers,
                    json={
                        "name": "Integration Smoke Question",
                        "dataset_query": {
                            "type": "query",
                            "database": db_id,
                            "query": {"source-table": table["id"]},
                        },
                        "display": "table",
                        "visualization_settings": {},
                        "collection_id": collection_id,
                    },
                ).raise_for_status()
        else:
            logger.warning(
                "No sample database found — METABASEQUESTION will be empty; "
                "shape assertions still hold"
            )
    finally:
        client.close()


@pytest.fixture(scope="session")
def metabase_credentials() -> dict[str, Any]:
    """Bring up Metabase and return the credential bundle the workflow uses.

    Priority:
        1. ``E2E_METABASE_HOST`` env var set → use that (preconfigured).
        2. Docker available → start ``metabase/metabase`` testcontainer +
           seed it.
        3. Neither → ``pytest.skip`` the whole integration suite at
           session-collection time (mirrors mysql-app behavior).

    Returns ``{host, port, username, password}`` ready for
    ``parse_metabase_credentials``. ``host`` carries the protocol prefix
    because ``MetabaseCredential.host`` is documented to.
    """
    if _metabase_host_preconfigured():
        host = os.environ["E2E_METABASE_HOST"]
        port = int(os.environ.get("E2E_METABASE_PORT", "443") or "443")
        logger.info("Using preconfigured Metabase at %s:%d", host, port)
        return {
            "host": host,
            "port": port,
            "username": os.environ.get("E2E_METABASE_USERNAME", ""),
            "password": os.environ.get("E2E_METABASE_PASSWORD", ""),
        }

    if not _docker_available():
        pytest.skip(
            "integration tests need Docker (for the Metabase testcontainer) "
            "or E2E_METABASE_HOST + creds for an external Metabase",
            allow_module_level=True,
        )

    # Import here so the dependency only loads when actually needed —
    # avoids a hard testcontainers requirement on unit-test-only runs
    # that don't import this fixture.
    from testcontainers.core.container import DockerContainer

    logger.info("Starting Metabase container (%s)", _METABASE_IMAGE)
    container = (
        DockerContainer(_METABASE_IMAGE)
        .with_exposed_ports(_METABASE_PORT)
        # Keep memory in line with the e2e compose overlay so the boot
        # profile is reproducible.
        .with_env("JAVA_OPTS", "-Xmx1500m")
        .with_env("MB_CHECK_FOR_UPDATES", "false")
        .with_env("MB_ANON_TRACKING_ENABLED", "false")
    )
    container.start()
    try:
        ip = container.get_container_host_ip()
        mapped_port = int(container.get_exposed_port(_METABASE_PORT))
        base_url = f"http://{ip}:{mapped_port}"
        logger.info("Waiting for Metabase at %s", base_url)
        _wait_for_metabase_ready(base_url)
        _seed_metabase(base_url)
        logger.info("Metabase container ready and seeded at %s", base_url)
        yield {
            "host": f"http://{ip}",
            "port": mapped_port,
            "username": _ADMIN_EMAIL,
            "password": _ADMIN_PASSWORD,
        }
    finally:
        logger.info("Stopping Metabase container")
        container.stop()


# ---------------------------------------------------------------------------
# Infrastructure fixture — wires mock secret / state / storage
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def store_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Root directory for the session-scoped LocalStore.

    RETAINED-tier files survive here after cleanup_storage runs, because
    cleanup_storage skips RETAINED refs. Tests can resolve a durable
    FileReference to a local path via ``store_root / ref.storage_path``.
    """
    return tmp_path_factory.mktemp("sdk-store")


@pytest.fixture(scope="session")
def infrastructure(
    store_root: Path,
    metabase_credentials: dict[str, Any],
) -> InfrastructureContext:
    """Wire mock infrastructure for the session using a LocalStore.

    Seeds MockSecretStore with the Metabase credential bundle from the
    testcontainer (or preconfigured external host) so workflow tasks
    resolving ``CredentialRef(name="metabase")`` see a populated
    credential without needing a real Dapr secret store.
    """
    secrets = {
        _CREDENTIAL_KEY: orjson.dumps(metabase_credentials).decode(),
    }
    ctx = InfrastructureContext(
        state_store=MockStateStore(),
        secret_store=MockSecretStore(secrets),
        storage=create_local_store(store_root),
    )
    set_infrastructure(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Embedded Temporal runtime
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def embedded_temporal():
    """Boot an in-process Temporal dev server for the test session."""
    async with embedded_runtime(log_level="error") as rt:
        yield rt


# ---------------------------------------------------------------------------
# Temporal client and in-process worker fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def temporal_client(embedded_temporal) -> Client:
    """Connect to the embedded Temporal dev server."""
    data_converter = create_data_converter_for_app(MetabaseApp)
    return await Client.connect(embedded_temporal.host, data_converter=data_converter)


@pytest_asyncio.fixture(scope="session")
async def metabase_worker(
    temporal_client: Client,
    infrastructure: InfrastructureContext,  # noqa: ARG001 — ensures infra is wired first
) -> Any:
    """Start the MetabaseApp worker in-process."""
    w = create_worker(temporal_client, task_queue=_TASK_QUEUE)
    async with w:
        yield


@pytest.fixture(scope="session")
def metabase_executor(
    temporal_client: Client,
    metabase_worker: Any,  # noqa: ARG001 — ensures worker is running
) -> AppExecutor:
    """Executor for MetabaseApp integration tests."""
    backend = TemporalExecutorBackend(
        client=temporal_client,
        task_queue=_TASK_QUEUE,
    )
    return AppExecutor(backend=backend)
