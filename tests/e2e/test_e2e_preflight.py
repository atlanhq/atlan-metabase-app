"""E2E test: 4 named preflight checks against a real Metabase Docker instance.

Asserts that ``MetabaseHandler.preflight_check`` emits checks with the
verbatim names declared in platform-packages' sageTemplate
(``collectionCountCheck``, ``dashboardCountCheck``, ``questionCountCheck``,
``nativeQueryPermissionCheck``) and that all four pass against the seeded
state.
"""

from __future__ import annotations

import pytest
from application_sdk.handler.contracts import (
    HandlerCredential,
    PreflightInput,
    PreflightStatus,
)

from app.handler import MetabaseHandler

pytestmark = pytest.mark.e2e


@pytest.fixture
def credentials(metabase_admin) -> list[HandlerCredential]:
    email, password = metabase_admin
    return [
        HandlerCredential(key="host", value="http://localhost"),
        HandlerCredential(key="port", value="3000"),
        HandlerCredential(key="username", value=email),
        HandlerCredential(key="password", value=password),
    ]


@pytest.mark.asyncio
async def test_preflight_emits_four_named_checks(credentials):
    """All 4 sageTemplate-named checks must be present and pass on seeded data."""
    handler = MetabaseHandler()
    result = await handler.preflight_check(
        PreflightInput(credentials=credentials, metadata={})
    )

    names = [c.name for c in result.checks]
    assert "collectionCountCheck" in names, names
    assert "dashboardCountCheck" in names, names
    assert "questionCountCheck" in names, names
    assert "nativeQueryPermissionCheck" in names, names

    failed = [c for c in result.checks if not c.passed]
    assert not failed, (
        f"unexpected preflight failures: {[(c.name, c.message) for c in failed]}"
    )
    assert result.status == PreflightStatus.READY
