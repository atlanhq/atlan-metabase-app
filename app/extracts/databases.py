"""Metabase databases extraction — list and per-database metadata."""

from typing import Dict, List, Optional

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls
from app.residuals import record_residual_failure

logger = get_logger(__name__)


async def fetch_databases_summaries(
    client: MetabaseApiClient, output_path: str
) -> List[Dict]:
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
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        List of raw database dicts (unwrapped from the ``data`` key).  Returns
        ``[]`` on failure — the failure is recorded as a residual rather than
        raised (see ``app/residuals.py``).
    """
    url = MetabaseUrls.database(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        status = response.status_code if response else "No response"
        logger.warning("Failed to fetch databases: %s", status)
        record_residual_failure(
            output_path,
            "databases_fetch_failed",
            endpoint="/api/database",
            http_status=status if isinstance(status, int) else None,
        )
        # conformance: ignore[E020] tolerated failure recorded to residual/failures.jsonl (see app/residuals.py) instead of aborting the workflow.
        return []
    records = response.json().get("data", [])
    logger.info("Fetched %d databases", len(records))
    return records


async def fetch_databases_details(
    client: MetabaseApiClient, summaries: List[Dict], output_path: str
) -> List[Dict]:
    """Fetch schema/table metadata for each database.

    For each summary record, calls ``GET /api/database/<id>/metadata`` and
    returns the enriched response.  The metadata payload adds:

    - ``tables`` — list of table objects, each with ``id``, ``name``,
      ``schema``, and a ``fields`` list.  Each field includes ``id``,
      ``name``, ``base_type``, ``semantic_type``, and ``description``.
    - Engine and connection ``details`` (same as summary but may be more
      complete).

    Records for which no ``id`` is present, or where the individual metadata
    fetch fails, are skipped — a failed fetch is recorded as a residual
    (``<output_path>/residual/failures.jsonl``) rather than aborting the
    whole batch over one bad database.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summaries: Database summary records — each must contain an ``id`` field.
        output_path: Task-local staging directory, forwarded to
            :func:`fetch_database_metadata` for residual recording.

    Returns:
        List of raw database metadata dicts (one per successfully fetched ID).
    """
    records = []
    for summary in summaries:
        detail = await _fetch_database_metadata(client, summary, output_path)
        if detail is not None:
            records.append(detail)
    logger.info("Fetched %d database metadata records", len(records))
    return records


async def fetch_database_metadata(
    client: MetabaseApiClient, database_id: int, output_path: str
) -> Optional[Dict]:
    """Fetch metadata for a single database by ID.

    Convenience wrapper used by the activity layer when iterating one record
    at a time (``raw-input-paginate: 1`` pattern).

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        database_id: Integer ID of the database to fetch metadata for.
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        Raw database metadata dict, or ``None`` if the fetch failed.
    """
    url = MetabaseUrls.database_metadata(client.host, client.port, database_id)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        status = response.status_code if response else "No response"
        logger.warning(
            "Failed to fetch database metadata for id=%s: %s", database_id, status
        )
        record_residual_failure(
            output_path,
            "database_metadata_fetch_failed",
            endpoint="/api/database",
            record_id=database_id,
            http_status=status if isinstance(status, int) else None,
        )
        # conformance: ignore[E020] tolerated failure recorded to residual/failures.jsonl (see app/residuals.py) instead of aborting the batch.
        return None
    return response.json()


async def _fetch_database_metadata(
    client: MetabaseApiClient, summary: Dict, output_path: str
) -> Optional[Dict]:
    """Fetch database metadata for a given summary record.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        summary: Database summary dict containing at minimum an ``id`` field.
        output_path: Task-local staging directory, forwarded to
            :func:`fetch_database_metadata` for residual recording.

    Returns:
        Raw database metadata dict, or ``None`` if ``id`` is missing or the
        fetch failed.
    """
    database_id = summary.get("id")
    if not database_id:
        return None
    return await fetch_database_metadata(client, database_id, output_path)
