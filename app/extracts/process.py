"""Metabase process (enrichment) functions.

Pure Python enrichment — no Temporal, no I/O, no external SQL parser dependency.
These functions are called by the ``process_metabaseprocess`` Temporal activity in
``app/activities/metadata_extraction.py`` to enrich the filtered/detailed data that
was produced by the extraction and filter stages.

Legacy reference:
    marketplace_scripts/marketplace_scripts/metabase/main.py
"""

from typing import Any, Dict, List, Optional, Tuple

from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Engine name mapping (mirrors main.py metabase_atlan_source_engine_map)
# ---------------------------------------------------------------------------

METABASE_ATLAN_SOURCE_ENGINE_MAP: Dict[str, str] = {
    # snowflake
    "snowflake": "snowflake",
    # bigquery
    "bigquery-cloud-sdk": "bigquery",
    "bigquery": "bigquery",
    # postgres
    "postgres": "postgres",
    # h2
    "h2": "h2",
    # mysql
    "mysql": "mysql",
}


def safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts, returning *default* if any key is absent or None.

    Mirrors ``marketplace_scripts.utils.safe_get`` so that ``process.py`` has no
    dependency on the legacy package.

    Args:
        obj: Starting mapping to traverse.
        *keys: Key path to follow.
        default: Value to return when traversal fails or the final value is None.

    Returns:
        The value at the end of the key chain, or *default*.
    """
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return default
        if obj is None:
            return default
    return obj if obj is not None else default


# ---------------------------------------------------------------------------
# Public enrichment functions
# ---------------------------------------------------------------------------


def generate_collections_map(
    collections: List[Dict],
    metabase_host: str,
) -> Dict[Any, Dict]:
    """Annotate collections with ``metabase_host`` / ``sourceURL`` and build lookup dict.

    Mirrors ``generate_collections_map()`` in ``main.py``:

    - Sets ``metabase_host`` on each collection record.
    - Builds ``sourceURL`` as ``<host>/collection/<id>``.
    - Returns a dict keyed by collection ``id`` (preserving the original type —
      integer or the string ``"root"``).

    Note: Unlike the legacy code which also writes the enriched records via
    ``ChunkedOutputHandler``, this function only returns the enriched list and
    lookup dict.  Writing to disk is the responsibility of the calling activity.

    Args:
        collections: List of filtered collection dicts.  Each must have an
            ``"id"`` field (int or the string ``"root"``).
        metabase_host: Full Metabase host URL including protocol
            (e.g. ``https://myinstance.metabaseapp.com``).  Comes from the
            credential stored on the client.

    Returns:
        Dict mapping collection ``id`` → enriched collection dict.
        Also mutates each collection in-place (same objects are in the list).
    """
    collections_map: Dict[Any, Dict] = {}
    for collection in collections:
        collection["metabase_host"] = metabase_host
        collection["sourceURL"] = str(
            metabase_host + "/collection/" + str(collection["id"])
        )
        collections_map[collection["id"]] = collection
    logger.info(
        "generate_collections_map: built map with %d entries", len(collections_map)
    )
    return collections_map


def generate_databases_map(
    database_details: List[Dict],
    metabase_host: str,
) -> Dict[Any, Dict]:
    """Annotate databases with ``metabase_host`` / ``sourceURL`` and build lookup dict.

    Mirrors ``generate_databases_map()`` in ``main.py``:

    - Sets ``metabase_host`` on each database record.
    - Builds ``sourceURL`` as ``<host>/browse/<id>``.
    - Returns a dict keyed by database ``id``.

    Args:
        database_details: List of detailed database metadata dicts (from the
            ``database-metadata/`` extraction stage).  Each must have an ``"id"``
            field (int).
        metabase_host: Full Metabase host URL including protocol.

    Returns:
        Dict mapping database ``id`` → enriched database dict.
    """
    databases_map: Dict[Any, Dict] = {}
    for database in database_details:
        database["metabase_host"] = metabase_host
        database["sourceURL"] = str(metabase_host + "/browse/" + str(database["id"]))
        databases_map[database["id"]] = database
    logger.info("generate_databases_map: built map with %d entries", len(databases_map))
    return databases_map


def generate_questions_query_map(
    question_queries: List[Dict],
) -> Dict[Any, Dict]:
    """Build ``question_id → {query, params}`` lookup from extracted question queries.

    Mirrors ``generate_questions_query_map()`` in ``main.py``:

    - Reads each record from ``question-queries/``.
    - If the ``query`` value is not a plain string (e.g. it is a dict because the
      Metabase API returned a structured object), replaces it with ``""`` so that
      the downstream SQL parser does not break.
      See ticket PES-3766 comment in the legacy code.
    - Returns a dict keyed by ``question_id``.

    Args:
        question_queries: List of question-query records.  Each must have at
            minimum a ``"question_id"`` field.  The ``"query"`` and ``"params"``
            fields are optional.

    Returns:
        Dict mapping ``question_id`` → ``{"query": str, "params": ...}``.
    """
    questions_query_map: Dict[Any, Dict] = {}
    for record in question_queries:
        question_id = record["question_id"]
        raw_query = record.get("query", "")
        # If query is not a string (e.g. a dict), the downstream SQL parser breaks.
        # Coerce to empty string — mirrors PES-3766 fix in main.py.
        query_str: str = raw_query if isinstance(raw_query, str) else ""
        questions_query_map[question_id] = {
            "query": query_str,
            "params": record.get("params", ""),
        }
    logger.info(
        "generate_questions_query_map: built map with %d entries",
        len(questions_query_map),
    )
    return questions_query_map


def process_assets(
    collections_map: Dict[Any, Dict],
    databases_map: Dict[Any, Dict],
    questions_query_map: Dict[Any, Dict],
    dashboard_details: List[Dict],
    filtered_questions: List[Dict],
    metabase_host: str,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Process and enrich all Metabase assets.

    Mirrors ``filter_assets(questions_query_map)`` in ``main.py`` — the function
    name in the legacy code is misleading; it actually *enriches* (not just
    filters) the dashboard-detailed and questions data.

    Step 1 — Enrich dashboards:
      - Reads each dashboard from ``dashboard-detailed/``.
      - Skips dashboards whose ``collection_id`` is not in ``collections_map``
        (already filtered, but the legacy code re-checks as a safety guard).
      - Sets ``metabase_host``, ``sourceURL``, ``collection`` (full dict), and
        ``cards_count`` (len of ``ordered_cards``).
      - Pops ``ordered_cards`` from the dashboard dict and builds
        ``cards_dashboard_map``: ``{card_id: [dashboard_id, ...]}``.
      - Adds the enriched dashboard to the output list and to a local
        ``dashboards`` lookup dict.

    Step 2 — Enrich questions:
      - Reads each question from the filtered questions list.
      - Skips questions whose ``collection_id`` is not in ``collections_map``.
      - Sets ``metabase_host``, ``sourceURL``, ``collection`` (full dict).
      - Looks up the query object from ``questions_query_map`` and the database
        from ``databases_map``; skips questions where either is missing.
      - Builds a ``query_object`` dict with ``query``, ``params``,
        ``default_database_name`` (from ``database.details.db``),
        ``default_schema_name`` (from ``database.details.schema``), and
        ``engine`` (Atlan-mapped engine name).  The ``query_object`` is only
        set if the database is known AND (the dataset_query type is not
        ``"native"`` OR the database has a ``"details"`` key).
      - Attaches the list of dashboards the question appears on.
      - Emits a ``questions-dashboards`` lineage record when the question
        appears on at least one dashboard.

    Args:
        collections_map: Output of :func:`generate_collections_map`.
        databases_map: Output of :func:`generate_databases_map`.
        questions_query_map: Output of :func:`generate_questions_query_map`.
        dashboard_details: List of detailed dashboard dicts (from
            ``dashboard-detailed/`` extraction stage).  Each must contain
            ``id``, ``collection_id``, and ``ordered_cards``.
        filtered_questions: List of filtered question dicts (from the filter
            stage).  Each must contain ``id``, ``collection_id``,
            ``database_id``, and ``dataset_query``.
        metabase_host: Full Metabase host URL including protocol.

    Returns:
        Four-tuple:
        - ``enriched_dashboards`` (List[Dict]) — dashboards with collection,
          sourceURL, and question count; ``ordered_cards`` removed.
        - ``enriched_questions`` (List[Dict]) — questions with query object,
          collection, dashboards list, and sourceURL.
        - ``questions_dashboards_lineage`` (List[Dict]) — BIProcess records
          ``{question_id, question_name, dashboards: [{id, name}]}``.
          Only questions that appear on at least one dashboard are included.

    Note:
        The first element of the tuple is kept for symmetry but is the same
        list as ``enriched_dashboards``; callers can unpack all four values.
    """
    # ------------------------------------------------------------------
    # Local lookup dicts (mirror SqliteDict usage in legacy main.py)
    # ------------------------------------------------------------------
    dashboards: Dict[Any, Dict] = {}
    cards_dashboard_map: Dict[Any, List[Any]] = {}

    # ------------------------------------------------------------------
    # Step 1: Enrich dashboards
    # ------------------------------------------------------------------
    enriched_dashboards: List[Dict] = []

    for dashboard in dashboard_details:
        collection_id = dashboard.get("collection_id") or "root"
        if collection_id not in collections_map:
            # Safety guard — dashboard is outside the accepted collections scope.
            logger.debug(
                "process_assets: skipping dashboard id=%s (collection_id=%s not in collections_map)",
                dashboard.get("id"),
                collection_id,
            )
            continue

        dashboard["metabase_host"] = metabase_host
        dashboard["sourceURL"] = str(
            metabase_host + "/dashboard/" + str(dashboard["id"])
        )
        dashboard["collection"] = collections_map[collection_id]

        # Metabase v0.49 renamed ``ordered_cards`` → ``dashcards``. Accept
        # either so the connector works across server versions; v2 only
        # ever saw the legacy field name.
        cards_list = dashboard.pop("dashcards", None)
        if cards_list is None:
            cards_list = dashboard.pop("ordered_cards", [])
        else:
            # Pop the legacy field too if both exist, to keep the
            # enriched dashboard dict clean.
            dashboard.pop("ordered_cards", None)

        dashboard["cards_count"] = len(cards_list)

        for card_data in cards_list:
            if not card_data.get("card_id"):
                continue
            card = card_data.get("card")
            if card is None:
                continue
            card_id = card["id"]
            if card_id not in cards_dashboard_map:
                cards_dashboard_map[card_id] = []
            cards_dashboard_map[card_id].append(dashboard["id"])

        dashboards[dashboard["id"]] = dashboard
        enriched_dashboards.append(dashboard)

    logger.info("process_assets: enriched %d dashboards", len(enriched_dashboards))

    # ------------------------------------------------------------------
    # Step 2: Enrich questions
    # ------------------------------------------------------------------
    enriched_questions: List[Dict] = []
    questions_dashboards_lineage: List[Dict] = []

    for question in filtered_questions:
        collection_id = question.get("collection_id") or "root"
        if collection_id not in collections_map:
            logger.debug(
                "process_assets: skipping question id=%s (collection_id=%s not in collections_map)",
                question.get("id"),
                collection_id,
            )
            continue

        question["metabase_host"] = metabase_host
        question["sourceURL"] = str(metabase_host + "/question/" + str(question["id"]))

        # Resolve query object and database
        query: Optional[Dict] = questions_query_map.get(question["id"])
        database: Optional[Dict] = databases_map.get(question.get("database_id"))

        if query is None or database is None:
            logger.warning(
                "process_assets: missing query or database for question id=%s name=%s — skipping",
                question.get("id"),
                question.get("name"),
            )
            continue

        # Build query_object (mirrors main.py logic exactly)
        dataset_query = question.get("dataset_query", {})
        query_object: Dict[str, Any] = {}
        atlan_compatible_engine = METABASE_ATLAN_SOURCE_ENGINE_MAP.get(
            database.get("engine", ""), database.get("engine", "")
        )
        if database and (
            dataset_query.get("type") != "native" or "details" in database
        ):
            query_object = {
                **query,
                **{
                    "default_database_name": safe_get(database, "details", "db"),
                    "default_schema_name": safe_get(database, "details", "schema"),
                    "engine": atlan_compatible_engine,
                },
            }

        question["collection"] = collections_map[collection_id]
        question["query"] = query_object
        question["dashboards"] = []

        # Attach the dashboards this question appears on
        if question["id"] in cards_dashboard_map:
            for dashboard_id in cards_dashboard_map[question["id"]]:
                dashboard_obj = dashboards.get(dashboard_id)
                if dashboard_obj is not None:
                    question["dashboards"].append(dashboard_obj)

        # Emit lineage record if the question appears on at least one dashboard
        if question["dashboards"]:
            questions_dashboards_lineage.append(
                {
                    "question_id": question["id"],
                    "question_name": question["name"],
                    "dashboards": [
                        {"id": d["id"], "name": d["name"]}
                        for d in question["dashboards"]
                    ],
                }
            )

        enriched_questions.append(question)

    logger.info(
        "process_assets: enriched %d questions, %d questions-dashboards lineage records",
        len(enriched_questions),
        len(questions_dashboards_lineage),
    )

    return enriched_dashboards, enriched_questions, questions_dashboards_lineage
