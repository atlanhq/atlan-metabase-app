import json
import os
from typing import Any, Dict, Type

from application_sdk.activities.common.models import ActivityStatistics
from application_sdk.activities.common.utils import auto_heartbeater
from application_sdk.activities.metadata_extraction.base import (
    BaseMetadataExtractionActivities,
    BaseMetadataExtractionActivitiesState,
)
from application_sdk.observability.logger_adaptor import get_logger
from temporalio import activity

from app.client import MetabaseApiClient
from app.extracts.collections import fetch_collections_summaries
from app.extracts.dashboards import fetch_dashboards_details, fetch_dashboards_summaries
from app.extracts.databases import fetch_databases_details, fetch_databases_summaries
from app.extracts.filter import (
    build_accepted_collection_ids,
    filter_collections,
    filter_dashboards,
    filter_questions,
)
from app.extracts.process import (
    generate_collections_map,
    generate_databases_map,
    generate_questions_query_map,
    process_assets,
)
from app.extracts.questions import fetch_question_queries, fetch_questions_summaries
from app.handler import MetabaseHandler
from app.transformers import MetabaseTransformer

logger = get_logger(__name__)
activity.logger = logger


class MetabaseMetadataExtractionActivities(BaseMetadataExtractionActivities):
    """Temporal activities for Metabase metadata extraction."""

    def __init__(
        self,
        client_class: Type[MetabaseApiClient] | None = None,
        handler_class: Type[MetabaseHandler] | None = None,
        transformer_class: Type[MetabaseTransformer] | None = None,
    ):
        """Initialize with optional custom client, handler, and transformer classes.

        Args:
            client_class: Custom ``MetabaseApiClient`` subclass, or ``None`` for default.
            handler_class: Custom ``MetabaseHandler`` subclass, or ``None`` for default.
            transformer_class: Custom transformer class, or ``None`` for default.
        """
        super().__init__(
            client_class=client_class or MetabaseApiClient,
            handler_class=handler_class or MetabaseHandler,
            transformer_class=transformer_class or MetabaseTransformer,
        )

    # =========================================================================
    # SECTION 1 — STATE HELPERS
    # =========================================================================

    async def _set_state(self, workflow_args: Dict[str, Any]) -> None:
        """Initialize workflow state with Metabase client, handler, and transformer.

        Args:
            workflow_args: Workflow arguments dict containing ``credential_guid``,
                ``metadata``, and ``connection`` information.
        """
        from application_sdk.activities.common.utils import get_workflow_id

        workflow_id = get_workflow_id()
        if not self._state.get(workflow_id):
            self._state[workflow_id] = BaseMetadataExtractionActivitiesState()

        await super()._set_state(workflow_args)

        logger.info(
            "MetabaseMetadataExtractionActivities: state initialised for workflow %s",
            workflow_id,
        )

    # =========================================================================
    # SECTION 2 — EXTRACTION ACTIVITIES
    # =========================================================================

    @auto_heartbeater
    @activity.defn
    async def extract_collections(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch all Metabase collections and write to raw/collections/.

        Calls ``GET /api/collection`` and writes NDJSON to
        ``<output_path>/raw/collections/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with collection record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "extract_collections: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "extract_collections: Metabase client not found in state"
                )

            records = await fetch_collections_summaries(client)

            _write_ndjson(os.path.join(output_path, "raw", "collections"), records)

            logger.info("extract_collections: wrote %d records", len(records))
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="collections",
            )

        except Exception as e:
            logger.error("extract_collections failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def extract_dashboards(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch all Metabase dashboard summaries and write to raw/dashboards/.

        Calls ``GET /api/dashboard`` and writes NDJSON to
        ``<output_path>/raw/dashboards/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with dashboard record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "extract_dashboards: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "extract_dashboards: Metabase client not found in state"
                )

            records = await fetch_dashboards_summaries(client)

            _write_ndjson(os.path.join(output_path, "raw", "dashboards"), records)

            logger.info("extract_dashboards: wrote %d records", len(records))
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="dashboards",
            )

        except Exception as e:
            logger.error("extract_dashboards failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def extract_questions(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch all Metabase question (card) summaries and write to raw/questions/.

        Calls ``GET /api/card`` and writes NDJSON to
        ``<output_path>/raw/questions/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with question record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "extract_questions: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "extract_questions: Metabase client not found in state"
                )

            records = await fetch_questions_summaries(client)

            _write_ndjson(os.path.join(output_path, "raw", "questions"), records)

            logger.info("extract_questions: wrote %d records", len(records))
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="questions",
            )

        except Exception as e:
            logger.error("extract_questions failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def extract_databases(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch all Metabase database summaries and write to raw/databases/.

        Calls ``GET /api/database`` (unwraps the ``data`` envelope) and writes
        NDJSON to ``<output_path>/raw/databases/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with database record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "extract_databases: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "extract_databases: Metabase client not found in state"
                )

            records = await fetch_databases_summaries(client)

            _write_ndjson(os.path.join(output_path, "raw", "databases"), records)

            logger.info("extract_databases: wrote %d records", len(records))
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="databases",
            )

        except Exception as e:
            logger.error("extract_databases failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def filter_data(self, workflow_args: Dict[str, Any]) -> ActivityStatistics:
        """Filter raw extracted Metabase data by include/exclude collection IDs.

        Reads raw NDJSON files written by the extraction stage, applies
        collection-scoped include/exclude filters, and writes the filtered
        records to new NDJSON files.

        Data paths (relative to ``workflow_args["output_path"]``):

        - **Read from:**
          ``raw/collections/``, ``raw/dashboards/``, ``raw/questions/``,
          ``raw/databases/``
        - **Write to:**
          ``raw/collections_filtered/``, ``raw/dashboards_filtered/``,
          ``raw/questions_filtered/``, ``raw/databases_filtered/``

        Filter logic (mirrors legacy
        ``marketplace_scripts/metabase/filter.py``):

        1. Apply include/exclude filter to collections; write survivors.
        2. Build a set of accepted collection IDs from the surviving
           collections.
        3. Keep only dashboards and questions whose ``collection_id`` is in
           the accepted set (``None`` / falsy → treated as ``"root"``).
        4. Pass databases through unfiltered.

        Args:
            workflow_args: Standard Temporal workflow args dict.  The
                following keys are used:

                - ``output_path`` (str): Base output directory for this
                  workflow run.
                - ``metadata`` (dict, optional): Workflow metadata block.
                  May contain ``"include-collections"`` and
                  ``"exclude-collections"`` keys (JSON strings or dicts).

        Returns:
            :class:`~application_sdk.activities.common.models.ActivityStatistics`
            with total record counts across all four entity types.

        Raises:
            ValueError: If ``output_path`` is not present in *workflow_args*.
            Exception: Any I/O error encountered while reading or writing
                NDJSON files is propagated to the Temporal activity layer.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "filter_data: 'output_path' is required in workflow_args"
                )

            # ------------------------------------------------------------------
            # 1. Resolve include / exclude filter parameters from workflow args.
            # ------------------------------------------------------------------
            metadata: Dict[str, Any] = workflow_args.get("metadata", {}) or {}
            include_collections_raw = metadata.get("include-collections", {})
            exclude_collections_raw = metadata.get("exclude-collections", {})

            logger.info(
                "filter_data: include_collections=%s, exclude_collections=%s",
                include_collections_raw,
                exclude_collections_raw,
            )

            # ------------------------------------------------------------------
            # 2. Load raw collections and apply the include/exclude filter.
            # ------------------------------------------------------------------
            raw_collections_dir = os.path.join(output_path, "raw", "collections")
            raw_collections = _read_ndjson_dir(raw_collections_dir)

            filtered_collections = filter_collections(
                raw_collections,
                include_collections=include_collections_raw,
                exclude_collections=exclude_collections_raw,
            )
            accepted_ids = build_accepted_collection_ids(filtered_collections)

            logger.info(
                "filter_data: accepted collection ids = %s",
                sorted(accepted_ids) if accepted_ids else "(all)",
            )

            # ------------------------------------------------------------------
            # 3. Load and filter dashboards.
            # ------------------------------------------------------------------
            raw_dashboards_dir = os.path.join(output_path, "raw", "dashboards")
            raw_dashboards = _read_ndjson_dir(raw_dashboards_dir)
            filtered_dashboards = filter_dashboards(raw_dashboards, accepted_ids)

            # ------------------------------------------------------------------
            # 4. Load and filter questions (cards).
            # ------------------------------------------------------------------
            raw_questions_dir = os.path.join(output_path, "raw", "questions")
            raw_questions = _read_ndjson_dir(raw_questions_dir)
            filtered_questions = filter_questions(raw_questions, accepted_ids)

            # ------------------------------------------------------------------
            # 5. Load databases — passed through unfiltered.
            # ------------------------------------------------------------------
            raw_databases_dir = os.path.join(output_path, "raw", "databases")
            raw_databases = _read_ndjson_dir(raw_databases_dir)

            # ------------------------------------------------------------------
            # 6. Write filtered results as NDJSON files.
            # ------------------------------------------------------------------
            _write_ndjson(
                os.path.join(output_path, "raw", "collections_filtered"),
                filtered_collections,
            )
            _write_ndjson(
                os.path.join(output_path, "raw", "dashboards_filtered"),
                filtered_dashboards,
            )
            _write_ndjson(
                os.path.join(output_path, "raw", "questions_filtered"),
                filtered_questions,
            )
            _write_ndjson(
                os.path.join(output_path, "raw", "databases_filtered"),
                raw_databases,
            )

            # ------------------------------------------------------------------
            # 7. Build and return activity statistics.
            # ------------------------------------------------------------------
            total_records = (
                len(filtered_collections)
                + len(filtered_dashboards)
                + len(filtered_questions)
                + len(raw_databases)
            )

            logger.info(
                "filter_data complete: collections=%d, dashboards=%d, "
                "questions=%d, databases=%d, total=%d",
                len(filtered_collections),
                len(filtered_dashboards),
                len(filtered_questions),
                len(raw_databases),
                total_records,
            )

            return ActivityStatistics(
                total_record_count=total_records,
                chunk_count=4,
                typename="filter_data",
            )

        except Exception as e:
            logger.error("filter_data failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def extract_individual_dashboards(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch full detail for each filtered dashboard and write to raw/dashboard_details/.

        Reads ``raw/dashboards_filtered/``, calls ``GET /api/dashboard/<id>``
        for each, and writes the enriched records (with ``ordered_cards``) to
        ``<output_path>/raw/dashboard_details/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with dashboard detail record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "extract_individual_dashboards: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "extract_individual_dashboards: Metabase client not found in state"
                )

            filtered_dashboards = _read_ndjson_dir(
                os.path.join(output_path, "raw", "dashboards_filtered")
            )

            logger.info(
                "extract_individual_dashboards: fetching details for %d dashboards",
                len(filtered_dashboards),
            )

            records = await fetch_dashboards_details(client, filtered_dashboards)

            _write_ndjson(
                os.path.join(output_path, "raw", "dashboard_details"), records
            )

            logger.info(
                "extract_individual_dashboards: wrote %d detail records", len(records)
            )
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="dashboard_details",
            )

        except Exception as e:
            logger.error("extract_individual_dashboards failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def extract_individual_databases(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch schema/table metadata for each database and write to raw/database_metadata/.

        Reads ``raw/databases_filtered/``, calls
        ``GET /api/database/<id>/metadata`` for each, and writes the enriched
        records to ``<output_path>/raw/database_metadata/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with database metadata record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "extract_individual_databases: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "extract_individual_databases: Metabase client not found in state"
                )

            database_summaries = _read_ndjson_dir(
                os.path.join(output_path, "raw", "databases_filtered")
            )

            logger.info(
                "extract_individual_databases: fetching metadata for %d databases",
                len(database_summaries),
            )

            records = await fetch_databases_details(client, database_summaries)

            _write_ndjson(
                os.path.join(output_path, "raw", "database_metadata"), records
            )

            logger.info(
                "extract_individual_databases: wrote %d metadata records", len(records)
            )
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="database_metadata",
            )

        except Exception as e:
            logger.error("extract_individual_databases failed: %s", str(e), exc_info=e)
            raise

    @auto_heartbeater
    @activity.defn
    async def fetch_question_queries_activity(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Fetch native SQL queries for each filtered question.

        Reads ``raw/questions_filtered/``, calls
        ``POST /api/dataset/native`` for each question (silently skips
        failures), and writes results to
        ``<output_path>/raw/question_queries/result-0.json``.

        Args:
            workflow_args: Standard workflow args dict with ``output_path``.

        Returns:
            :class:`ActivityStatistics` with question-query record count.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "fetch_question_queries_activity: 'output_path' is required in workflow_args"
                )

            state = await self._get_state(workflow_args)
            client: MetabaseApiClient = state.client  # type: ignore[assignment]
            if not client:
                raise ValueError(
                    "fetch_question_queries_activity: Metabase client not found in state"
                )

            filtered_questions = _read_ndjson_dir(
                os.path.join(output_path, "raw", "questions_filtered")
            )

            logger.info(
                "fetch_question_queries_activity: fetching queries for %d questions",
                len(filtered_questions),
            )

            records = await fetch_question_queries(client, filtered_questions)

            _write_ndjson(os.path.join(output_path, "raw", "question_queries"), records)

            logger.info(
                "fetch_question_queries_activity: wrote %d query records", len(records)
            )
            return ActivityStatistics(
                total_record_count=len(records),
                chunk_count=1,
                typename="question_queries",
            )

        except Exception as e:
            logger.error(
                "fetch_question_queries_activity failed: %s", str(e), exc_info=e
            )
            raise

    @auto_heartbeater
    @activity.defn
    async def process_metabaseprocess(
        self, workflow_args: Dict[str, Any]
    ) -> ActivityStatistics:
        """Process and enrich all extracted Metabase assets.

        Implements the ``[process]`` step from the pipeline map, which runs
        ``marketplace_scripts.metabase.main``.  This is pure Python enrichment
        with no dependency on the Gudusoft SQL parser.

        Data paths (relative to ``workflow_args["output_path"]``):

        - **Read from:**

          - ``raw/collections_filtered/``  — output of the ``filter_data`` activity
          - ``raw/databases_filtered/``    — detailed database metadata
            (from ``extract-detailed``)
          - ``raw/question_queries/``      — native SQL per question
            (from ``extract-queries``)
          - ``raw/dashboard_details/``     — per-dashboard detail with ``ordered_cards``
            (from ``extract-detailed``)
          - ``raw/questions_filtered/``    — filtered question summaries

        - **Write to:**

          - ``processed/collections/``
          - ``processed/dashboards/``
          - ``processed/questions/``
          - ``processed/questions_dashboards/``

        What this activity does (mirrors ``main.py`` exactly):

        1. :func:`~app.extracts.process.generate_collections_map` — annotates
           each collection with ``metabase_host`` and ``sourceURL``, builds a
           lookup dict keyed by collection id.
        2. :func:`~app.extracts.process.generate_databases_map` — annotates each
           detailed database record with ``metabase_host`` and ``sourceURL``,
           builds a lookup dict keyed by database id.
        3. :func:`~app.extracts.process.generate_questions_query_map` — builds a
           ``{question_id: {query, params}}`` lookup from the question-queries
           extraction output.
        4. :func:`~app.extracts.process.process_assets` — enriches dashboards
           (sets collection reference, question count, sourceURL; builds
           ``cards_dashboard_map``); enriches questions (sets query object with
           engine/db/schema, collection, dashboards list); emits
           ``questions-dashboards`` BIProcess lineage records.

        The enriched ``questions`` output (with the ``query`` object) is what
        the downstream Argo ``parse-queries`` step reads to extract SQL for the
        Gudusoft parser.

        Args:
            workflow_args: Standard Temporal workflow args dict.  The following
                keys are used:

                - ``output_path`` (str): Base output directory for this workflow run.
                - ``credentials`` (dict, optional): May contain ``"host"`` key
                  for the Metabase host URL.  Falls back to an empty string if
                  not present.

        Returns:
            :class:`~application_sdk.activities.common.models.ActivityStatistics`
            with total record counts across all four output entity types.

        Raises:
            ValueError: If ``output_path`` is not present in *workflow_args*.
            Exception: Any I/O error encountered while reading or writing
                NDJSON files is propagated to the Temporal activity layer.
        """
        try:
            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "process_metabaseprocess: 'output_path' is required in workflow_args"
                )

            # ------------------------------------------------------------------
            # 1. Resolve metabase_host from credentials stored on workflow_args.
            #    The host is the same credential that is stored on the client
            #    (MetabaseCredentials.host) and was originally passed as
            #    ``--metabase-host`` in the legacy Argo step.
            # ------------------------------------------------------------------
            credentials: Dict[str, Any] = workflow_args.get("credentials", {}) or {}
            metabase_host: str = credentials.get("host", "")
            if not metabase_host:
                logger.warning(
                    "process_metabaseprocess: 'credentials.host' is empty; "
                    "sourceURL fields will be empty strings"
                )

            # ------------------------------------------------------------------
            # 2. Read filtered collections.
            # ------------------------------------------------------------------
            filtered_collections = _read_ndjson_dir(
                os.path.join(output_path, "raw", "collections_filtered")
            )
            logger.info(
                "process_metabaseprocess: loaded %d filtered collections",
                len(filtered_collections),
            )

            # ------------------------------------------------------------------
            # 3. Read detailed database metadata.
            # ------------------------------------------------------------------
            database_details = _read_ndjson_dir(
                os.path.join(output_path, "raw", "databases_filtered")
            )
            logger.info(
                "process_metabaseprocess: loaded %d database detail records",
                len(database_details),
            )

            # ------------------------------------------------------------------
            # 4. Read question-query records.
            # ------------------------------------------------------------------
            question_queries = _read_ndjson_dir(
                os.path.join(output_path, "raw", "question_queries")
            )
            logger.info(
                "process_metabaseprocess: loaded %d question-query records",
                len(question_queries),
            )

            # ------------------------------------------------------------------
            # 5. Read dashboard detail records (include ordered_cards).
            # ------------------------------------------------------------------
            dashboard_details = _read_ndjson_dir(
                os.path.join(output_path, "raw", "dashboard_details")
            )
            logger.info(
                "process_metabaseprocess: loaded %d dashboard detail records",
                len(dashboard_details),
            )

            # ------------------------------------------------------------------
            # 6. Read filtered questions.
            # ------------------------------------------------------------------
            filtered_questions = _read_ndjson_dir(
                os.path.join(output_path, "raw", "questions_filtered")
            )
            logger.info(
                "process_metabaseprocess: loaded %d filtered questions",
                len(filtered_questions),
            )

            # ------------------------------------------------------------------
            # 7. Build lookup maps.
            # ------------------------------------------------------------------
            collections_map = generate_collections_map(
                filtered_collections, metabase_host
            )
            logger.info(
                "process_metabaseprocess: collections map keys = %s",
                sorted(str(k) for k in collections_map.keys()),
            )

            databases_map = generate_databases_map(database_details, metabase_host)
            questions_query_map = generate_questions_query_map(question_queries)

            # ------------------------------------------------------------------
            # 8. Enrich all assets.
            # ------------------------------------------------------------------
            (
                enriched_dashboards,
                enriched_questions,
                questions_dashboards_lineage,
            ) = process_assets(
                collections_map=collections_map,
                databases_map=databases_map,
                questions_query_map=questions_query_map,
                dashboard_details=dashboard_details,
                filtered_questions=filtered_questions,
                metabase_host=metabase_host,
            )

            # ------------------------------------------------------------------
            # 9. Write processed outputs as NDJSON.
            # ------------------------------------------------------------------
            _write_ndjson(
                os.path.join(output_path, "processed", "collections"),
                filtered_collections,
            )
            _write_ndjson(
                os.path.join(output_path, "processed", "dashboards"),
                enriched_dashboards,
            )
            _write_ndjson(
                os.path.join(output_path, "processed", "questions"),
                enriched_questions,
            )
            _write_ndjson(
                os.path.join(output_path, "processed", "questions_dashboards"),
                questions_dashboards_lineage,
            )

            # ------------------------------------------------------------------
            # 10. Build and return activity statistics.
            # ------------------------------------------------------------------
            total_records = (
                len(filtered_collections)
                + len(enriched_dashboards)
                + len(enriched_questions)
                + len(questions_dashboards_lineage)
            )

            logger.info(
                "process_metabaseprocess complete: collections=%d, dashboards=%d, "
                "questions=%d, questions_dashboards=%d, total=%d",
                len(filtered_collections),
                len(enriched_dashboards),
                len(enriched_questions),
                len(questions_dashboards_lineage),
                total_records,
            )

            return ActivityStatistics(
                total_record_count=total_records,
                chunk_count=4,
                typename="process_metabaseprocess",
            )

        except Exception as e:
            logger.error("process_metabaseprocess failed: %s", str(e), exc_info=e)
            raise


# =============================================================================
# MODULE-LEVEL I/O HELPERS
# =============================================================================


def _read_ndjson_dir(directory: str) -> list:
    """Read all ``*.json`` NDJSON files from *directory* into a list of dicts.

    Each line in every matching file is parsed as a separate JSON object.
    Missing directories are treated as empty (returns ``[]``) so that
    optional entity types do not cause the activity to fail.

    Args:
        directory: Absolute or relative path to the directory containing
            ``*.json`` NDJSON files.

    Returns:
        List of parsed dicts, in file-then-line order.
    """
    records = []
    if not os.path.isdir(directory):
        logger.warning(
            "_read_ndjson_dir: directory does not exist, returning empty list: %s",
            directory,
        )
        return records

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "_read_ndjson_dir: skipping malformed line in %s: %s",
                            filepath,
                            exc,
                        )
        except OSError as exc:
            logger.warning(
                "_read_ndjson_dir: could not read file %s: %s", filepath, exc
            )

    logger.debug("_read_ndjson_dir: loaded %d records from %s", len(records), directory)
    return records


def _write_ndjson(directory: str, records: list) -> None:
    """Write *records* as NDJSON (one JSON object per line) to *directory*.

    Creates the directory if it does not exist.  All records are written to a
    single file named ``result-0.json``, matching the naming convention used
    by the legacy ``ChunkedOutputHandler``.

    Args:
        directory: Target directory path (created if missing).
        records: List of dicts to serialise.  An empty list results in an
            empty file being created (downstream consumers should tolerate
            empty files).
    """
    os.makedirs(directory, exist_ok=True)
    output_file = os.path.join(directory, "result-0.json")
    with open(output_file, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.debug("_write_ndjson: wrote %d records to %s", len(records), output_file)
