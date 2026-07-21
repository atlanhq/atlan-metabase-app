"""Shared pytest fixtures and configuration for unit tests."""

from unittest.mock import patch

import pytest


class _InlineTaskContext:
    """Minimal ``TaskExecutionContext`` stand-in for unit tests.

    In production, @task methods run inside the SDK's Temporal activity wrapper,
    which sets ``app._task_context``. Unit tests call @task methods directly, so
    that context is absent and ``self.run_in_thread`` / ``self.heartbeat`` would
    raise ``AppContextError``. This stub supplies one that runs blocking work
    inline (no real thread) so the file-I/O side effects the tests assert on
    still happen, while the real ``self.run_in_thread`` call path (including its
    context guard) is exercised.
    """

    async def run_in_thread(self, func, *args, **kwargs):
        return func(*args, **kwargs)

    def heartbeat(self, *details):
        pass

    def get_last_heartbeat_details(self):
        return ()

    def get_heartbeat_details(self, cls):
        return None


@pytest.fixture(autouse=True)
def _task_execution_context(monkeypatch):
    """Give every app instance a task context so @task methods can be invoked
    directly in unit tests (see ``_InlineTaskContext``)."""
    from application_sdk.app.base import App

    monkeypatch.setattr(App, "_task_context", _InlineTaskContext())


@pytest.fixture(autouse=True)
def _disable_dapr_health_check():
    """Disable DAPR health check for all unit tests when DAPR is present.

    On v2 SDK the ``DaprClient`` constructor called
    ``DaprHealth.wait_until_ready()``, which blocks for 60 seconds trying to
    reach the DAPR sidecar at localhost:3500. v3 SDK replaces this with
    ``httpx-retries`` and no longer imports ``dapr``, so the patch becomes a
    no-op when the package is absent.
    """
    try:
        import dapr.clients.health  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        yield
        return
    with patch("dapr.clients.health.DaprHealth.wait_until_ready"):
        yield
