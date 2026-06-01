"""Fixtures for integration tests.

Tests run entirely in-process: Temporal starts as an embedded dev server
via the SDK's ``embedded_runtime()``, and secret / state / storage
infrastructure is mocked — no external services required.

The shape mirrors ``atlan-openapi-app/tests/integration/conftest.py``;
metabase-specific deltas are the credentials fixture (the connector needs
``E2E_METABASE_*`` env vars wired into the MockSecretStore so the workflow
can reach a real Metabase server) and the task queue name.

Environment variables:
    E2E_METABASE_HOST      — Metabase URL with protocol (e.g. https://acme.metabaseapp.com)
    E2E_METABASE_PORT      — port (default 443)
    E2E_METABASE_USERNAME  — Metabase username / email
    E2E_METABASE_PASSWORD  — Metabase password

Run tests with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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
from application_sdk.observability.observability import AtlanObservability
from application_sdk.storage import create_local_store, create_memory_store
from application_sdk.testing.mocks import MockSecretStore, MockStateStore
from temporalio.client import Client

# Trigger MetabaseApp registration before create_worker is called.
from app.connector import MetabaseApp  # noqa: F401

# Pre-wire a memory store as the deployment objectstore so the periodic
# observability flush does not keep retrying and spamming warnings in tests.
AtlanObservability._deployment_store = create_memory_store()

_TASK_QUEUE = "metabase-queue"
_CREDENTIAL_KEY = "metabase"


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
# Infrastructure fixture — wires mock secret / state / storage (no Dapr)
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
def infrastructure(store_root: Path) -> InfrastructureContext:
    """Wire mock infrastructure for the session using a LocalStore.

    Seeds MockSecretStore with the Metabase credential bundle from env
    vars so workflow tasks resolving ``CredentialRef(name="metabase")`` see
    a populated credential without needing a real Dapr secret store.
    """
    host = os.environ.get("E2E_METABASE_HOST", "")
    port = int(os.environ.get("E2E_METABASE_PORT", "443") or "443")
    username = os.environ.get("E2E_METABASE_USERNAME", "")
    password = os.environ.get("E2E_METABASE_PASSWORD", "")

    secrets: dict[str, str] = {}
    if host and username and password:
        secrets[_CREDENTIAL_KEY] = orjson.dumps(
            {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
            }
        ).decode()

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


# ---------------------------------------------------------------------------
# Convenience — module-level skip when Metabase credentials are absent.
# ---------------------------------------------------------------------------
# Tests import this and call it at module level so the whole file gracefully
# skips locally when ``tests/.env`` is not populated, instead of erroring with
# an auth failure deep inside the workflow.


def require_metabase_env() -> None:
    """Skip the calling module if any Metabase env var is missing."""
    required = (
        "E2E_METABASE_HOST",
        "E2E_METABASE_USERNAME",
        "E2E_METABASE_PASSWORD",
    )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(
            f"Metabase integration tests need {', '.join(missing)}",
            allow_module_level=True,
        )
