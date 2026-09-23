"""Metabase dashboards extraction — list and per-dashboard detail."""

from typing import Dict, List, Optional

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls
from app.errors import MetabaseSourceUnavailableError
from app.extracts.responses import json_or_raise
from app.residuals import record_residual_failure

logger = get_logger(__name__)


async def fetch_dashboards_summaries(
    client: MetabaseApiClient, output_path: str
) -> List[Dict]:
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
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        List of raw dashboard summary dicts.  Returns ``[]`` on failure — the
        typed failure is caught here and recorded as a residual rather than
        propagated (see ``app/residuals.py``).
    """
    url = MetabaseUrls.dashboard(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    try:
        records = json_or_raise(response, endpoint="/api/dashboard")
    except MetabaseSourceUnavailableError as exc:
        logger.warning(
            "Failed to fetch dashboards: %s",
            exc.http_status or "No response",
            exc_info=True,
        )
        record_residual_failure(
            output_path,
            "dashboards_fetch_failed",
            endpoint=exc.endpoint,
            http_status=exc.http_status,
        )
        return []
    logger.info("Fetched %d dashboard summaries", len(records))
    return records


async def fetch_dashboards_details(
    client: MetabaseApiClient, summaries: List[Dict], output_path: str
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

    Records for which no ``id`` is present, or where the individual detail
    fetch fails, are skipped — a failed fetch is recorded as a residual
    (``<output_path>/residual/failures.jsonl``) rather than aborting the
    whole batch over one bad dashboard.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summaries: Filtered dashboard summary records (output of the filter
            stage) — each must contain an ``id`` field.
        output_path: Task-local staging directory, forwarded to
            :func:`fetch_dashboard_details` for residual recording.

    Returns:
        List of raw dashboard detail dicts (one per successfully fetched ID).
    """
    records = []
    for summary in summaries:
        detail = await _fetch_dashboard_detail(client, summary, output_path)
        if detail is not None:
            records.append(detail)
    logger.info("Fetched %d dashboard detail records", len(records))
    return records


async def fetch_dashboard_details(
    client: MetabaseApiClient, dashboard_id: int, output_path: str
) -> Optional[Dict]:
    """Fetch detail for a single dashboard by ID.

    Convenience wrapper used by the activity layer when iterating one record
    at a time (``raw-input-paginate: 1`` pattern).

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        dashboard_id: Integer ID of the dashboard to fetch.
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        Raw dashboard detail dict, or ``None`` if the fetch failed.
    """
    url = MetabaseUrls.dashboard_detail(client.host, client.port, dashboard_id)
    response = await client.execute_http_get_request(url=url, timeout=60)
    try:
        return json_or_raise(response, endpoint="/api/dashboard")
    except MetabaseSourceUnavailableError as exc:
        logger.warning(
            "Failed to fetch dashboard detail for id=%s: %s",
            dashboard_id,
            exc.http_status or "No response",
            exc_info=True,
        )
        record_residual_failure(
            output_path,
            "dashboard_detail_fetch_failed",
            endpoint=exc.endpoint,
            record_id=dashboard_id,
            http_status=exc.http_status,
        )
        return None


async def _fetch_dashboard_detail(
    client: MetabaseApiClient, summary: Dict, output_path: str
) -> Optional[Dict]:
    """Fetch single dashboard detail for a given summary record.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summary: Dashboard summary dict containing at minimum an ``id`` field.
        output_path: Task-local staging directory, forwarded to
            :func:`fetch_dashboard_details` for residual recording.

    Returns:
        Raw dashboard detail dict, or ``None`` if ``id`` is missing or the
        fetch failed.
    """
    dashboard_id = summary.get("id")
    if not dashboard_id:
        return None
    return await fetch_dashboard_details(client, dashboard_id, output_path)
