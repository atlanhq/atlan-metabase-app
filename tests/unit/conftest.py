"""Shared pytest fixtures and configuration for unit tests."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _disable_dapr_health_check():
    """Disable DAPR health check for all unit tests.

    The SDK's DaprClient constructor calls DaprHealth.wait_until_ready() which
    blocks for 60 seconds trying to reach the DAPR sidecar at localhost:3500.
    Unit tests don't need DAPR, so we patch it out entirely.
    """
    with patch("dapr.clients.health.DaprHealth.wait_until_ready"):
        yield
