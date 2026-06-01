"""Metabase questions (cards) extraction — list and per-question native query."""

from typing import Dict, List, Optional

from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls

logger = get_logger(__name__)


async def fetch_questions_summaries(client: MetabaseApiClient) -> List[Dict]:
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

    Returns:
        List of raw question/card dicts.  Returns ``[]`` on any failure.
    """
    url = MetabaseUrls.card(client.host, client.port)
    response = await client.execute_http_get_request(url=url, timeout=60)
    if response is None or not response.is_success:
        logger.warning(
            "Failed to fetch questions: %s",
            response.status_code if response else "No response",
        )
        return []
    records = response.json()
    logger.info("Fetched %d question summaries", len(records))
    return records


async def fetch_question_queries(
    client: MetabaseApiClient, questions: List[Dict]
) -> List[Dict]:
    """Fetch the native SQL query for each question via POST to
    ``/api/dataset/native``.

    Behaviour mirrors the legacy ``extract-queries`` step:

    - Questions without a ``dataset_query`` field are silently skipped
      (``ignore=True`` equivalent).
    - API failures for individual questions are silently skipped
      (``FailureHandler.NONE`` equivalent).
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

    Returns:
        List of dicts with shape ``{"question_id": int, "query": str,
        "params": ...}``.  Only questions for which a non-empty query was
        successfully retrieved are included.
    """
    records = []
    for question in questions:
        result = await _fetch_question_query(client, question)
        if result is not None:
            records.append(result)
    logger.info("Fetched %d question query records", len(records))
    return records


async def fetch_question_queries_single(
    client: MetabaseApiClient, question_id: int, dataset_query: Dict
) -> Optional[Dict]:
    """Fetch the native SQL query for a single question.

    Convenience wrapper used by the activity layer when iterating one record
    at a time (``raw-input-paginate: 1`` pattern).

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        question_id: Integer ID of the question.
        dataset_query: The ``dataset_query`` dict from the question record.

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
        return None


async def _fetch_question_query(
    client: MetabaseApiClient, question: Dict
) -> Optional[Dict]:
    """Fetch native SQL query for a single question dict.

    Args:
        client: Authenticated ``MetabaseApiClient`` instance.
        question: Question summary dict containing at minimum ``id`` and
            optionally ``dataset_query``.

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
    return await fetch_question_queries_single(client, int(question_id), dataset_query)
