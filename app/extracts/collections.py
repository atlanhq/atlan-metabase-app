"""Metabase collections extraction."""

from typing import Dict, List

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls
from app.residuals import record_residual_failure

logger = get_logger(__name__)


async def fetch_collections_summaries(
    client: MetabaseApiClient, output_path: str
) -> List[Dict]:
    """Fetch all collections from Metabase API.

    Calls ``GET /api/collection`` which returns a flat JSON array of collection
    objects.  Each collection record includes at minimum:

    - ``id`` — integer or ``"root"`` for the root collection
    - ``name`` — display name
    - ``personal_owner_id`` — present when this is a personal collection
    - ``location`` — slash-separated path string, e.g. ``"/1/4/"``
    - ``archived`` — boolean

    The filter stage downstream uses ``collection_id`` on dashboards/questions
    to determine which objects belong to included collections.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        List of raw collection dicts.  Returns ``[]`` on failure — collections
        are foundational to every downstream stage, so a hard failure here is
        recorded as a residual rather than raised (see module docstring in
        ``app/residuals.py``).
    """
    url = MetabaseUrls.collection(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        status = response.status_code if response else "No response"
        logger.warning("Failed to fetch collections: %s", status)
        record_residual_failure(
            output_path,
            "collections_fetch_failed",
            endpoint="/api/collection",
            http_status=status if isinstance(status, int) else None,
        )
        # conformance: ignore[E020] tolerated failure recorded to residual/failures.jsonl (see app/residuals.py) instead of aborting the workflow.
        return []
    records = response.json()
    logger.info("Fetched %d collections", len(records))
    return records
