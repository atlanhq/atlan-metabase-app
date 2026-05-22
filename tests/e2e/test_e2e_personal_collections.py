"""E2E test: personal collections must be excluded from /workflows/v1/metadata.

Matches v2 ``restMetadataOutputTransformerTemplate`` behavior — the metadata
tree the platform builds for the user-facing apitree widget must NOT include
personal collections (collections where ``personal_owner_id IS NOT NULL``).
"""

from __future__ import annotations

import pytest
from application_sdk.handler.contracts import HandlerCredential, MetadataInput

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
async def test_fetch_metadata_excludes_personal_collections(credentials, mb_get):
    """Admin user has an auto-created personal collection — it must be filtered."""
    # First, confirm via direct Metabase API that a personal collection exists
    # on the seeded instance. If none exist, this test is vacuous and we skip.
    all_collections = mb_get("/api/collection")
    personal = [c for c in all_collections if c.get("personal_owner_id")]
    if not personal:
        pytest.skip("no personal collection present on seeded instance")
    personal_ids = {str(c["id"]) for c in personal}

    handler = MetabaseHandler()
    result = await handler.fetch_metadata(
        MetadataInput(credentials=credentials, metadata={})
    )
    returned_ids = {obj.value for obj in result.objects}

    overlap = personal_ids & returned_ids
    assert not overlap, f"personal collections leaked into metadata tree: {overlap}"
