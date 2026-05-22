"""Metabase databases extraction — list and per-database metadata."""

from typing import Dict, List, Optional

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls

logger = get_logger(__name__)


async def fetch_databases_summaries(client: MetabaseApiClient) -> List[Dict]:
    """Fetch all databases from the Metabase API.

    Calls ``GET /api/database`` whose response body is wrapped in a top-level
    ``{"data": [...]}`` envelope (unlike the other list endpoints which return
    a bare array).  This method unwraps the ``data`` key automatically.

    Key fields per database record:

    - ``id`` — integer database identifier
    - ``name`` — display name
    - ``engine`` — string identifying the underlying database type, e.g.
      ``"snowflake"``, ``"bigquery-cloud-sdk"``, ``"postgres"``, ``"mysql"``,
      ``"h2"``.  The process stage maps these to Atlan-compatible engine names.
    - ``details`` — dict with engine-specific connection parameters; notably
      ``db`` (default database name) and ``schema`` (default schema), used by
      the process stage to build the lineage ``query_object``.

    Databases are passed through the filter stage **unfiltered** — all
    databases are always included regardless of collection selection.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.

    Returns:
        List of raw database dicts (unwrapped from the ``data`` key).
        Returns ``[]`` on any failure.
    """
    url = MetabaseUrls.database(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        logger.warning(
            "Failed to fetch databases: %s",
            response.status_code if response else "No response",
        )
        return []
    records = response.json().get("data", [])
    logger.info("Fetched %d databases", len(records))
    return records


async def fetch_databases_details(
    client: MetabaseApiClient, summaries: List[Dict]
) -> List[Dict]:
    """Fetch schema/table metadata for each database.

    For each summary record, calls ``GET /api/database/<id>/metadata`` and
    returns the enriched response.  The metadata payload adds:

    - ``tables`` — list of table objects, each with ``id``, ``name``,
      ``schema``, and a ``fields`` list.  Each field includes ``id``,
      ``name``, ``base_type``, ``semantic_type``, and ``description``.
    - Engine and connection ``details`` (same as summary but may be more
      complete).

    Records for which no ``id`` is present, or where the API call fails, are
    silently skipped.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summaries: Database summary records — each must contain an ``id`` field.

    Returns:
        List of raw database metadata dicts (one per successfully fetched ID).
    """
    records = []
    for summary in summaries:
        detail = await _fetch_database_metadata(client, summary)
        if detail is not None:
            records.append(detail)
    logger.info("Fetched %d database metadata records", len(records))
    return records


async def fetch_database_metadata(
    client: MetabaseApiClient, database_id: int
) -> Optional[Dict]:
    """Fetch metadata for a single database by ID.

    Convenience wrapper used by the activity layer when iterating one record
    at a time (``raw-input-paginate: 1`` pattern).

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        database_id: Integer ID of the database to fetch metadata for.

    Returns:
        Raw database metadata dict, or ``None`` if the fetch failed.
    """
    url = MetabaseUrls.database_metadata(client.host, client.port, database_id)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        logger.warning(
            "Failed to fetch database metadata for id=%s: %s",
            database_id,
            response.status_code if response else "No response",
        )
        return None
    return response.json()


async def _fetch_database_metadata(
    client: MetabaseApiClient, summary: Dict
) -> Optional[Dict]:
    """Fetch database metadata for a given summary record.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summary: Database summary dict containing at minimum an ``id`` field.

    Returns:
        Raw database metadata dict, or ``None`` if ``id`` is missing or the
        fetch failed.
    """
    database_id = summary.get("id")
    if not database_id:
        return None
    return await fetch_database_metadata(client, database_id)
