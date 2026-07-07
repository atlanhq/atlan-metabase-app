"""Metabase questions (cards) extraction — list and per-question native query."""

from typing import Dict, List, Optional

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls
from app.residuals import record_residual_failure

logger = get_logger(__name__)


async def fetch_questions_summaries(
    client: MetabaseApiClient, output_path: str
) -> List[Dict]:
    """Fetch all questions/cards from the Metabase API.

    Calls ``GET /api/card`` which returns a flat JSON array of card (question)
    summary objects.  Key fields per record:

    - ``id`` — integer question identifier
    - ``name`` — display name
    - ``collection_id`` — integer or ``None`` (root) — used by the filter
      stage to scope questions to selected collections
    - ``database_id`` — integer referencing the source database; used by the
      process stage to look up engine/connection details for lineage
    - ``dataset_query`` — dict with ``type`` (``"native"`` or ``"query"``) and
      query body.  For native questions the ``native`` sub-key holds
      ``{"query": "<SQL>", "template-tags": {...}}``; for MBQL questions it
      holds a structured query.  This field is consumed by
      :func:`fetch_question_queries` to POST to ``/api/dataset/native``.
    - ``archived`` — boolean

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        List of raw question/card dicts.  Returns ``[]`` on failure — the
        failure is recorded as a residual rather than raised (see
        ``app/residuals.py``).
    """
    url = MetabaseUrls.card(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        status = response.status_code if response else "No response"
        logger.warning("Failed to fetch questions: %s", status)
        record_residual_failure(
            output_path,
            "questions_fetch_failed",
            endpoint="/api/card",
            http_status=status if isinstance(status, int) else None,
        )
        # conformance: ignore[E020] tolerated failure recorded to residual/failures.jsonl (see app/residuals.py) instead of aborting the workflow.
        return []
    records = response.json()
    logger.info("Fetched %d question summaries", len(records))
    return records


async def fetch_question_queries(
    client: MetabaseApiClient, questions: List[Dict], output_path: str
) -> List[Dict]:
    """Fetch the native SQL query for each question via POST to
    ``/api/dataset/native``.

    Behaviour mirrors the legacy ``extract-queries`` step:

    - Questions without a ``dataset_query`` field are silently skipped
      (``ignore=True`` equivalent).
    - API failures for individual questions are skipped (``FailureHandler.NONE``
      equivalent) and recorded to ``<output_path>/residual/failures.jsonl``.
    - A result record is only appended when the response contains a non-empty
      ``query`` string.

    The response from ``POST /api/dataset/native`` contains a ``query`` string
    (the resolved native SQL) and optionally a ``params`` value.  The process
    stage reads these to build ``questions_query_map`` keyed by ``question_id``.

    Note on ``dataset_query`` shape: the field is sent verbatim as the POST
    body merged with ``{"question_id": <id>}``.  For MBQL questions Metabase
    translates the structured query to SQL server-side and returns the native
    form.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        questions: Filtered question summary records — each must contain at
            minimum ``id`` and optionally ``dataset_query``.
        output_path: Task-local staging directory, forwarded to
            :func:`fetch_question_queries_single` for residual recording.

    Returns:
        List of dicts with shape ``{"question_id": int, "query": str,
        "params": ...}``.  Only questions for which a non-empty query was
        successfully retrieved are included.
    """
    records = []
    for question in questions:
        result = await _fetch_question_query(client, question, output_path)
        if result is not None:
            records.append(result)
    logger.info("Fetched %d question query records", len(records))
    return records


async def fetch_question_queries_single(
    client: MetabaseApiClient, question_id: int, dataset_query: Dict, output_path: str
) -> Optional[Dict]:
    """Fetch the native SQL query for a single question.

    Convenience wrapper used by the activity layer when iterating one record
    at a time (``raw-input-paginate: 1`` pattern).

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        question_id: Integer ID of the question.
        dataset_query: The ``dataset_query`` dict from the question record.
        output_path: Task-local staging directory — a failure is recorded to
            ``<output_path>/residual/failures.jsonl`` for later review.

    Returns:
        Dict with shape ``{"question_id": int, "query": str, "params": ...}``,
        or ``None`` if the fetch failed or returned no query.
    """
    url = MetabaseUrls.dataset_native(client.host, client.port)
    try:
        response = await client.execute_http_post_request(
            url=url,
            json_data={"question_id": question_id, **dataset_query},
            timeout=60,
        )
        if response is None or not response.is_success:
            status = response.status_code if response else "No response"
            record_residual_failure(
                output_path,
                "question_query_fetch_failed",
                endpoint="/api/dataset/native",
                record_id=question_id,
                http_status=status if isinstance(status, int) else None,
            )
            # conformance: ignore[E020] deliberate best-effort per-question skip (FailureHandler.NONE equivalent), recorded to residual/failures.jsonl — one bad question must not abort the whole batch.
            return None
        data = response.json()
        query = data.get("query")
        if not query:
            return None
        return {
            "question_id": question_id,
            "query": query,
            "params": data.get("params"),
        }
    except Exception:
        logger.warning(
            "fetch_question_query: skipping question_id=%s after error",
            question_id,
            exc_info=True,
        )
        record_residual_failure(
            output_path,
            "question_query_fetch_errored",
            endpoint="/api/dataset/native",
            record_id=question_id,
        )
        return None


async def _fetch_question_query(
    client: MetabaseApiClient, question: Dict, output_path: str
) -> Optional[Dict]:
    """Fetch native SQL query for a single question dict.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        question: Question summary dict containing at minimum ``id`` and
            optionally ``dataset_query``.
        output_path: Task-local staging directory, forwarded to
            :func:`fetch_question_queries_single` for residual recording.

    Returns:
        Dict with ``question_id``, ``query``, and ``params``, or ``None`` if
        the question has no ``dataset_query`` or the fetch failed/returned no
        query.
    """
    dataset_query = question.get("dataset_query")
    if not dataset_query:
        # Skip questions with no dataset_query (ignore=True equivalent)
        return None
    question_id = question.get("id")
    if question_id is None:
        return None
    return await fetch_question_queries_single(
        client, int(question_id), dataset_query, output_path
    )
