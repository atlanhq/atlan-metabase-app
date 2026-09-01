"""Fixtures for integration tests — Metabase testcontainer + the SDK integration kit.

Tests run entirely in-process. The embedded Temporal dev server, mocked
state/secret/storage infrastructure, the in-process worker and the executor shim
all come from :mod:`application_sdk.testing.integration.fixtures`; the only
genuinely per-connector parts left here are the App class and the fixture that
brings up the source, supplied by overriding ``integration_app_cls`` and
``integration_source``.

**Metabase runs as a session-scoped Docker testcontainer** brought up via
testcontainers; a minimal seed (2 collections + 2 questions + 2 dashboards) is
applied via the Metabase HTTP API before tests start. The seed shares code with
the e2e compose overlay's one-shot service (``tests/e2e/seed_metabase.py``) —
same shape, just different counts. The Metabase image is pinned to the version
the full-DAG e2e overlay uses (``.github/e2e/e2e-full-docker-compose.yaml``) —
bump them together.

The workflow input passes credentials INLINE (``credentials=[...]``) rather than
via a ``CredentialRef``, so the test bypasses secret-store resolution entirely —
keeps the integration assertion focused on the extraction workflow itself,
independent of credential plumbing (which is unit-tested in
``tests/unit/test_credentials.py``).

Integration tests ALWAYS use a local testcontainer — there's no external-Metabase
escape hatch. If Docker isn't available the suite skips with a clear message.

Run with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# SDK-affecting env vars MUST be set BEFORE any application_sdk import — the
# SDK snapshots them into module-level constants on first import. The fixtures
# module verifies this ordering and raises IntegrationEnvOrderingError if wrong.
# ---------------------------------------------------------------------------
import os

os.environ.setdefault("ATLAN_APPLICATION_NAME", "metabase")
os.environ.setdefault("ATLAN_DEPLOYMENT_NAME", "ci")

import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from application_sdk.observability.logger_adaptor import get_logger  # noqa: E402
from application_sdk.testing.integration.fixtures import *  # noqa: E402, F403
from application_sdk.testing.integration.fixtures import AppExecutor  # noqa: E402

from app.connector import MetabaseApp  # noqa: E402

logger = get_logger("integration")

_METABASE_IMAGE = "metabase/metabase:v0.61.2.3"
_METABASE_PORT = 3000

_ADMIN_EMAIL = os.environ.get("MB_E2E_USERNAME", "e2e@example.com")
_ADMIN_PASSWORD = os.environ.get("MB_E2E_PASSWORD", "e2etestpw123")

# Integration count profile: 2 / 2 / 2. Light enough to keep boot+seed under
# ~25 s on CI; rich enough that the connector emits >=1 record per typename and
# BIProcess (dashboard->question pairings).
_INTEGRATION_N_COLLECTIONS = 2
_INTEGRATION_N_QUESTIONS = 2
_INTEGRATION_N_DASHBOARDS = 2


# ---------------------------------------------------------------------------
# SDK integration fixtures — embedded Temporal, mock infra, worker, executor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def integration_app_cls() -> type[MetabaseApp]:
    return MetabaseApp


@pytest.fixture(scope="session")
def integration_source(metabase_credentials: dict[str, Any]) -> dict[str, Any]:
    return metabase_credentials


@pytest.fixture(scope="session")
def metabase_executor(executor: AppExecutor) -> AppExecutor:
    """Alias keeping the existing test signatures intact."""
    return executor


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
def metabase_credentials() -> Iterator[dict[str, Any]]:
    """Bring up Metabase as a testcontainer and return the credential bundle.

    Starts ``metabase/metabase`` via testcontainers, applies the shared light
    seed from ``tests/e2e/seed_metabase.py`` with counts 2/2/2, yields
    ``{host, port, username, password}`` for the workflow to authenticate
    against. ``host`` carries the protocol prefix because
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
