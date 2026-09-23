"""Metabase collections extraction."""

from typing import Dict, List

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls
from app.errors import MetabaseSourceUnavailableError
from app.extracts.responses import json_or_raise
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
        caught here and recorded as a residual rather than propagated (see
        module docstring in ``app/residuals.py``).
    """
    url = MetabaseUrls.collection(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    try:
        records = json_or_raise(response, endpoint="/api/collection")
    except MetabaseSourceUnavailableError as exc:
        logger.warning(
            "Failed to fetch collections: %s",
            exc.http_status or "No response",
            exc_info=True,
        )
        record_residual_failure(
            output_path,
            "collections_fetch_failed",
            endpoint=exc.endpoint,
            http_status=exc.http_status,
        )
        return []
    logger.info("Fetched %d collections", len(records))
    return records
