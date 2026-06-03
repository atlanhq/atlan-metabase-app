"""Fixtures for integration tests — Metabase testcontainer + embedded Temporal.

Tests run entirely in-process:
  - Temporal starts as an embedded dev server via the SDK's
    ``embedded_runtime()``.
  - State / storage infrastructure is mocked.
  - **Metabase runs as a session-scoped Docker testcontainer** brought up
    via testcontainers; a minimal seed (2 collections + 2 questions + 2
    dashboards) is applied via the Metabase HTTP API before tests start.
    The seed shares code with the e2e compose overlay's one-shot service
    (``tests/e2e/seed_metabase.py``) — same shape, just different counts.

Pattern mirrors ``atlan-mysql-app/tests/integration/conftest.py`` which
boots a ``MySqlContainer`` and seeds it from ``fixtures/seed.sql``. The
Metabase image is pinned to the version the full-DAG e2e overlay uses
(``.github/e2e/e2e-full-docker-compose.yaml``) — bump them together.

The workflow input passes credentials INLINE (``credentials=[...]``)
rather than via a ``CredentialRef`` so the test bypasses secret-store
resolution entirely — keeps the integration assertion focused on the
extraction workflow itself, independent of credential plumbing (which is
unit-tested in ``tests/unit/test_credentials.py``).

Integration tests ALWAYS use a local testcontainer — there's no external-
Metabase escape hatch. If Docker isn't available the suite skips with
a clear message; that's the only mode aside from container-backed.

Run with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# SDK-affecting env vars MUST be set BEFORE any application_sdk import — the
# SDK reads them at module load to populate APPLICATION_NAME / DEPLOYMENT_NAME
# module-level constants. Matches atlan-mysql-app's conftest pattern.
# ---------------------------------------------------------------------------
os.environ.setdefault("ATLAN_APPLICATION_NAME", "metabase")
os.environ.setdefault("ATLAN_DEPLOYMENT_NAME", "ci")
# Preserve workflow artifacts (raw + transformed) under the LocalStore so
# tests can assert against them. Without this the SDK's cleanup interceptor
# deletes FileReference-tracked files after each workflow completes.
os.environ.setdefault("APPLICATION_SDK_ENABLE_CLEANUP_INTERCEPTOR", "false")

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

# Pin matches .github/e2e/e2e-full-docker-compose.yaml — keep in sync.
_METABASE_IMAGE = "metabase/metabase:v0.61.2.3"
_METABASE_PORT = 3000

# Same admin convention as the e2e compose overlay.
_ADMIN_EMAIL = "e2e@atlan.com"
_ADMIN_PASSWORD = "AtlanMetabaseE2E!1"

# Integration count profile: 2 / 2 / 2. Light enough to keep boot+seed
# under ~25 s on CI; rich enough that the connector emits ≥1 record per
# typename and BIProcess (dashboard→question pairings).
_INTEGRATION_N_COLLECTIONS = 2
_INTEGRATION_N_QUESTIONS = 2
_INTEGRATION_N_DASHBOARDS = 2


class AppExecutor:
    """Compatibility shim wrapping TemporalExecutorBackend for integration tests.

    Critical detail for multi-entry-point apps (like MetabaseApp, which has
    both ``extract-metadata`` and ``extract-lineage``): the underlying
    ``TemporalExecutorBackend.execute`` derives the workflow name from
    ``f"{app_name}:{entry_point}"`` when ``entry_point`` is passed, but
    falls back to just ``app_name`` when it isn't. Single-entry-point
    apps (mysql) happen to register a workflow at the bare ``app_name``,
    so omitting ``entry_point`` works there. Multi-entry-point apps don't —
    the bare name is never registered, so submissions to it sit in the
    Temporal queue forever with no listener. Always pass ``entry_point``.
    """

    def __init__(self, backend: TemporalExecutorBackend) -> None:
        self._backend = backend

    async def execute_app(
        self,
        app_cls: Any,
        input_data: Any,
        *,
        execution_id_prefix: str = "",
        entry_point: str | None = None,
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
            entry_point=entry_point,
        )


# ---------------------------------------------------------------------------
# Docker availability — graceful skip when Docker is unreachable.
# ---------------------------------------------------------------------------


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


@pytest.fixture(scope="session")
def metabase_credentials() -> dict[str, Any]:
    """Bring up Metabase as a testcontainer and return the credential bundle.

    Starts ``metabase/metabase`` via testcontainers, applies the shared
    light seed from ``tests/e2e/seed_metabase.py`` with counts 2/2/2,
    yields ``{host, port, username, password}`` for the workflow to
    authenticate against. ``host`` carries the protocol prefix because
    ``MetabaseCredential.host`` is documented to.

    Skips the integration suite when Docker is unreachable.
    """
    if not _docker_available():
        pytest.skip(
            "integration tests need Docker for the Metabase testcontainer",
            allow_module_level=True,
        )

    # Imports gated inside the fixture so unit-only runs don't need them.
    import asyncio

    from testcontainers.core.container import DockerContainer

    from tests.e2e.seed_metabase import seed_metabase

    logger.info("Starting Metabase container (%s)", _METABASE_IMAGE)
    boot_start = time.monotonic()
    container = (
        DockerContainer(_METABASE_IMAGE)
        .with_exposed_ports(_METABASE_PORT)
        .with_env("JAVA_OPTS", "-Xmx1500m")
        .with_env("MB_CHECK_FOR_UPDATES", "false")
        .with_env("MB_ANON_TRACKING_ENABLED", "false")
    )
    container.start()
    try:
        ip = container.get_container_host_ip()
        mapped_port = int(container.get_exposed_port(_METABASE_PORT))
        base_url = f"http://{ip}:{mapped_port}"
        logger.info("Metabase container up; seeding at %s", base_url)

        # The seed function does its own /api/health wait internally.
        asyncio.run(
            seed_metabase(
                base_url,
                admin_email=_ADMIN_EMAIL,
                admin_password=_ADMIN_PASSWORD,
                n_collections=_INTEGRATION_N_COLLECTIONS,
                n_questions=_INTEGRATION_N_QUESTIONS,
                n_dashboards=_INTEGRATION_N_DASHBOARDS,
                source=None,  # integration uses sample DB only (no lineage)
            )
        )
        logger.info(
            "Metabase boot + seed complete in %.1fs",
            time.monotonic() - boot_start,
        )
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
    metabase_credentials: dict[str, Any],  # noqa: ARG001 — gates on container readiness
) -> InfrastructureContext:
    """Wire mock infrastructure for the session using a LocalStore.

    MockSecretStore is empty — tests pass credentials inline through the
    workflow input, not via CredentialRef + secret-store lookup. Keeps
    integration scope on the extraction workflow, not credential plumbing
    (which has its own unit coverage).
    """
    ctx = InfrastructureContext(
        state_store=MockStateStore(),
        secret_store=MockSecretStore({}),
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
