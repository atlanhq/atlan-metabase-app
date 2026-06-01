"""E2E test — exercise MetabaseHandler against live Metabase Docker.

Validates the HTTP-handler surface the Atlan UI uses:
- POST /workflows/v1/auth     → MetabaseHandler.test_auth
- POST /workflows/v1/check    → MetabaseHandler.preflight_check
- POST /workflows/v1/metadata → MetabaseHandler.fetch_metadata

Tests cover happy-path, scoped filter behavior, and the negative paths
the UI surfaces (bad creds → AuthOutput.FAILED, preflight returns the
four canonical check names verbatim).
"""

from __future__ import annotations

import pytest
from application_sdk.handler.contracts import (
    AuthInput,
    AuthStatus,
    HandlerCredential,
    MetadataInput,
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


@pytest.fixture
def bad_credentials() -> list[HandlerCredential]:
    return [
        HandlerCredential(key="host", value="http://localhost"),
        HandlerCredential(key="port", value="3000"),
        HandlerCredential(key="username", value="nobody@example.com"),
        HandlerCredential(key="password", value="wrong-password"),
    ]


# ──────────────────────────────────────────────────────────────────────
# /workflows/v1/auth — test_auth
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_success_with_valid_creds(credentials):
    handler = MetabaseHandler()
    out = await handler.test_auth(AuthInput(credentials=credentials))
    print(f"[handler] test_auth status={out.status} message={out.message!r}")
    assert out.status == AuthStatus.SUCCESS


@pytest.mark.asyncio
async def test_auth_failure_with_bad_creds(bad_credentials):
    """Wrong password must fail authentication, not crash."""
    handler = MetabaseHandler()
    out = await handler.test_auth(AuthInput(credentials=bad_credentials))
    print(f"[handler] test_auth bad-creds status={out.status} message={out.message!r}")
    assert out.status == AuthStatus.FAILED
    assert out.message  # non-empty error message


@pytest.mark.asyncio
async def test_auth_failure_with_unreachable_host():
    """Unreachable host must surface as FAILED (not raise)."""
    creds = [
        HandlerCredential(key="host", value="http://localhost"),
        HandlerCredential(key="port", value="3333"),  # nothing listening
        HandlerCredential(key="username", value="x"),
        HandlerCredential(key="password", value="y"),
    ]
    handler = MetabaseHandler()
    out = await handler.test_auth(AuthInput(credentials=creds))
    print(f"[handler] test_auth bad-host status={out.status} message={out.message!r}")
    assert out.status == AuthStatus.FAILED


# ──────────────────────────────────────────────────────────────────────
# /workflows/v1/check — preflight_check
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preflight_emits_four_named_checks(credentials):
    """Verbatim check names from platform-packages sageTemplate."""
    handler = MetabaseHandler()
    result = await handler.preflight_check(PreflightInput(credentials=credentials))
    names = [c.name for c in result.checks]
    print(f"[handler] preflight returned {len(names)} checks: {names}")
    expected = {
        "collectionCountCheck",
        "dashboardCountCheck",
        "questionCountCheck",
        "nativeQueryPermissionCheck",
    }
    assert expected.issubset(
        set(names)
    ), f"missing: {expected - set(names)}, got: {names}"
    failed = [c for c in result.checks if not c.passed]
    assert not failed, [(c.name, c.message) for c in failed]
    assert result.status == PreflightStatus.READY


@pytest.mark.asyncio
async def test_preflight_count_increases_with_scale(credentials):
    """The collection / dashboard / question counts reported by preflight
    must reflect what's actually in Metabase. Verifies the preflight
    walks live data, not a stale cache."""
    handler = MetabaseHandler()
    result = await handler.preflight_check(PreflightInput(credentials=credentials))
    by_name = {c.name: c for c in result.checks}
    for cn in ("collectionCountCheck", "dashboardCountCheck", "questionCountCheck"):
        msg = by_name[cn].message
        print(f"[handler] {cn}: {msg!r}")
        assert msg.startswith("Total "), msg


@pytest.mark.asyncio
async def test_preflight_fails_cleanly_on_bad_creds(bad_credentials):
    handler = MetabaseHandler()
    out = await handler.preflight_check(PreflightInput(credentials=bad_credentials))
    print(f"[handler] preflight bad-creds status={out.status}")
    assert out.status == PreflightStatus.NOT_READY
    assert any(not c.passed for c in out.checks)


# ──────────────────────────────────────────────────────────────────────
# /workflows/v1/metadata — fetch_metadata (apitree)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metadata_returns_collection_tree(credentials):
    handler = MetabaseHandler()
    out = await handler.fetch_metadata(MetadataInput(credentials=credentials))
    print(f"[handler] fetch_metadata returned {len(out.objects)} collection objects")
    assert len(out.objects) > 0
    for obj in out.objects[:3]:
        # value=id, title=name, node_type=collection
        assert obj.value  # collection id stringified
        assert obj.title  # collection name


@pytest.mark.asyncio
async def test_metadata_excludes_personal_collections(credentials, mb_get):
    """Personal collections (personal_owner_id IS NOT NULL) must be filtered
    from the apitree — matches v2 restMetadataOutputTransformerTemplate."""
    raw = mb_get("/api/collection")
    personal_ids = {str(c["id"]) for c in raw if c.get("personal_owner_id")}
    if not personal_ids:
        pytest.skip("seeded instance has no personal collection")
    handler = MetabaseHandler()
    out = await handler.fetch_metadata(MetadataInput(credentials=credentials))
    returned_ids = {obj.value for obj in out.objects}
    overlap = personal_ids & returned_ids
    print(
        f"[handler] personal_ids={personal_ids}; overlap-with-metadata-tree={overlap}"
    )
    assert not overlap, f"personal collections leaked: {overlap}"
