"""Metabase dashboards extraction — list and per-dashboard detail."""

from typing import Dict, List, Optional

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls

logger = get_logger(__name__)


async def fetch_dashboards_summaries(client: MetabaseApiClient) -> List[Dict]:
    """Fetch all dashboards from the Metabase API.

    Calls ``GET /api/dashboard`` which returns a flat JSON array of dashboard
    summary objects.  Each summary includes at minimum:

    - ``id`` — integer dashboard identifier
    - ``name`` — display name
    - ``collection_id`` — integer or ``None`` (root collection) used by
      the filter stage to scope dashboards to selected collections
    - ``archived`` — boolean

    The summary list does **not** include ``ordered_cards`` (card/question
    linkage).  That data is only present in the individual detail response
    fetched by :func:`fetch_dashboards_details`.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.

    Returns:
        List of raw dashboard summary dicts.  Returns ``[]`` on any failure.
    """
    url = MetabaseUrls.dashboard(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        logger.warning(
            "Failed to fetch dashboards: %s",
            response.status_code if response else "No response",
        )
        return []
    records = response.json()
    logger.info("Fetched %d dashboard summaries", len(records))
    return records


async def fetch_dashboards_details(
    client: MetabaseApiClient, summaries: List[Dict]
) -> List[Dict]:
    """Fetch full detail for each filtered dashboard.

    For each summary record, calls ``GET /api/dashboard/<id>`` and returns
    the enriched response.  The detail payload adds:

    - ``ordered_cards`` — list of card-slot objects; each has a ``card`` sub-
      object (with ``id``, ``name``, ``dataset_query``, …) and a ``card_id``
      field.  The process stage uses this to build ``cards_dashboard_map``
      linking question IDs to the dashboards they appear in.
    - ``cards_count`` (derived in process stage from ``len(ordered_cards)``)
    - Additional metadata fields (description, creator, timestamps, etc.)

    Records for which no ``id`` is present, or where the API call fails, are
    silently skipped.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summaries: Filtered dashboard summary records (output of the filter
            stage) — each must contain an ``id`` field.

    Returns:
        List of raw dashboard detail dicts (one per successfully fetched ID).
    """
    records = []
    for summary in summaries:
        detail = await _fetch_dashboard_detail(client, summary)
        if detail is not None:
            records.append(detail)
    logger.info("Fetched %d dashboard detail records", len(records))
    return records


async def fetch_dashboard_details(
    client: MetabaseApiClient, dashboard_id: int
) -> Optional[Dict]:
    """Fetch detail for a single dashboard by ID.

    Convenience wrapper used by the activity layer when iterating one record
    at a time (``raw-input-paginate: 1`` pattern).

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        dashboard_id: Integer ID of the dashboard to fetch.

    Returns:
        Raw dashboard detail dict, or ``None`` if the fetch failed.
    """
    url = MetabaseUrls.dashboard_detail(client.host, client.port, dashboard_id)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        logger.warning(
            "Failed to fetch dashboard detail for id=%s: %s",
            dashboard_id,
            response.status_code if response else "No response",
        )
        return None
    return response.json()


async def _fetch_dashboard_detail(
    client: MetabaseApiClient, summary: Dict
) -> Optional[Dict]:
    """Fetch single dashboard detail for a given summary record.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summary: Dashboard summary dict containing at minimum an ``id`` field.

    Returns:
        Raw dashboard detail dict, or ``None`` if ``id`` is missing or the
        fetch failed.
    """
    dashboard_id = summary.get("id")
    if not dashboard_id:
        return None
    return await fetch_dashboard_details(client, dashboard_id)
