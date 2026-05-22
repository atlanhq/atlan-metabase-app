"""Shared pytest fixtures and configuration for unit tests."""

from unittest.mock import patch

import pytest


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
