"""Metabase v3 connector — single ``run()`` orchestrator.

Architecture mirrors ``atlan-openapi-app``: one ``App`` subclass with one
``async def run()`` override and a flat fan-out of ``@task`` methods.

The platform dispatches Metabase as **one** workflow instance with
extract→publish as nested DAG nodes (not two separate workflow submissions),
so a single entrypoint matches the platform shape exactly. The previous two-``@entrypoint`` shape (extract_metadata,
transform_metadata) is replaced by inline orchestration inside ``run()``.

The ``MetabaseHandler`` (imported below to register it for the SDK)
serves the platform endpoints: ``/workflows/v1/auth``,
``/workflows/v1/check``, ``/workflows/v1/metadata``.
"""

from __future__ import annotations

import os
import time
from typing import Any

import orjson
from application_sdk.app import App, entrypoint, task
from application_sdk.contracts.storage import DownloadInput, UploadInput
from application_sdk.contracts.types import FileReference, StorageTier
from application_sdk.observability.logger_adaptor import get_logger

from app.api_types import (
    BIProcessLineageRecord,
    CollectionRecord,
    DashboardRecord,
    QuestionRecord,
)
from app.asset_mapper import (
    map_bi_process,
    map_collection,
    map_dashboard,
    map_question,
    serialize_entity,
)
from app.client import MetabaseApiClient, build_client
from app.contracts import (
    TRANSFORM_ASSET_TYPES,
    TYPENAME_TO_PROCESS_DIR,
    BuildLineageInput,
    BuildLineageOutput,
    FetchDetailInput,
    FetchInput,
    FetchOutput,
    FilterInput,
    FilterOutput,
    MetabaseInput,
    MetabaseLineageInput,
    MetabaseLineageOutput,
    MetabaseOutput,
    ProcessInput,
    ProcessOutput,
    TransformTaskInput,
    TransformTaskOutput,
)
from app.credentials import build_credential_ref, parse_metabase_credentials
from app.errors import (
    MetabaseCredentialInputError,
    MissingOutputPathInputError,
    MissingTypenameInputError,
)
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
from app.handler import MetabaseHandler  # noqa: F401 — registers handler
from app.lineage.ars_builder import build_column_process, build_process, process_hash
from app.lineage.qi_reader import _question_name as _qi_question_name
from app.lineage.qi_reader import iter_qi_records, parse_qi_record
from app.paths import (
    PROCESSED_DIR,
    TRANSFORMED_DIR,
    default_output_path,
    processed_file,
    raw_file,
)
from app.residuals import RESIDUAL_DIR
from app.utils import read_jsonl, write_jsonl

logger = get_logger(__name__)


def _ref(local_path: str) -> FileReference:
    return FileReference(local_path=local_path, tier=StorageTier.RETAINED)


def _map_one_record(
    typename: str, raw: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any] | None:
    """Dispatch a raw record through the right typed mapper and serialize it.

    Returns ``None`` only when ``typename`` is unrecognized — callers log
    and skip. Per-record exceptions bubble up so a single bad record fails
    the activity loudly rather than silently dropping data.
    """
    if typename == "METABASECOLLECTION":
        return serialize_entity(map_collection(CollectionRecord.from_dict(raw), **ctx))
    if typename == "METABASEDASHBOARD":
        return serialize_entity(map_dashboard(DashboardRecord.from_dict(raw), **ctx))
    if typename == "METABASEQUESTION":
        asset, extras = map_question(QuestionRecord.from_dict(raw), **ctx)
        return serialize_entity(asset, extras)
    if typename == "BIPROCESS":
        return serialize_entity(
            map_bi_process(BIProcessLineageRecord.from_dict(raw), **ctx)
        )
    logger.warning("transform_data: unknown typename=%s; skipping record", typename)
    return None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class MetabaseApp(App):
    """Metabase connector — extracts collections, dashboards, questions and
    databases via the REST API, then transforms them into Atlas JSON.

    Single ``run()`` orchestrator. ``run()`` dispatches every step as a
    ``@task`` (Temporal activity), so each step has its own retry policy,
    heartbeat, and timeout. Credentials are resolved once and threaded
    through every task input.
    """

    name = "metabase"
    passthrough_modules = {"app.lineage"}

    # ------------------------------------------------------------------
    # Client + credential resolution
    # ------------------------------------------------------------------
    # NOTE: We intentionally do NOT cache the client in ``app_state``. Each
    # @task is dispatched as its own Temporal activity, so ``app_state`` only
    # spans a single activity execution — there is no cross-activity reuse to
    # be had. Building per-task is simple, deterministic, and avoids the
    # ``AppContextError`` raised when ``get_app_state`` is invoked outside the
    # activity context.

    async def _build_client(self, input: Any) -> MetabaseApiClient:
        """Build a Metabase client from the credential ref or inline creds.

        Resolves credentials via the SDK typed pathway:

        - ``input.credential_ref`` (``CredentialRef``) — pulled from secret
          store via ``self.context.resolve_credential_raw``.
        - ``input.inline_credentials`` — local-dev fallback.

        Raises:
            ValueError: When both fields are absent or empty.
        """
        raw_creds: dict[str, Any] = {}
        cred_ref = getattr(input, "credential_ref", None)
        if cred_ref is not None:
            raw_creds = await self.context.resolve_credential_raw(cred_ref)
        else:
            inline = getattr(input, "inline_credentials", {}) or {}
            if not inline:
                raise MetabaseCredentialInputError(
                    message="_build_client: no credential_ref or inline_credentials",
                    field="credentials",
                )
            raw_creds = inline

        credential = parse_metabase_credentials(raw_creds)
        return await build_client(credential)

    # ------------------------------------------------------------------
    # EXTRACTION @tasks
    # ------------------------------------------------------------------

    @task(timeout_seconds=600)
    async def extract_collections(self, input: FetchInput) -> FetchOutput:
        """Fetch all collections → ``raw/collections/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_collections_summaries(client, input.output_path)
        out = raw_file(input.output_path, "collections")
        write_jsonl(out, records)
        logger.info("extract_collections: wrote %d records", len(records))
        return FetchOutput(
            typename="collections", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def extract_dashboards(self, input: FetchInput) -> FetchOutput:
        """Fetch dashboard summaries → ``raw/dashboards/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_dashboards_summaries(client, input.output_path)
        out = raw_file(input.output_path, "dashboards")
        write_jsonl(out, records)
        logger.info("extract_dashboards: wrote %d records", len(records))
        return FetchOutput(
            typename="dashboards", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def extract_questions(self, input: FetchInput) -> FetchOutput:
        """Fetch question (card) summaries → ``raw/questions/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_questions_summaries(client, input.output_path)
        out = raw_file(input.output_path, "questions")
        write_jsonl(out, records)
        logger.info("extract_questions: wrote %d records", len(records))
        return FetchOutput(
            typename="questions", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def extract_databases(self, input: FetchInput) -> FetchOutput:
        """Fetch database summaries → ``raw/databases/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_databases_summaries(client, input.output_path)
        out = raw_file(input.output_path, "databases")
        write_jsonl(out, records)
        logger.info("extract_databases: wrote %d records", len(records))
        return FetchOutput(
            typename="databases", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def filter_data(self, input: FilterInput) -> FilterOutput:
        """Apply include/exclude filters to the four raw files."""
        raw_collections = read_jsonl(
            input.collections_file.local_path if input.collections_file else ""
        )
        raw_dashboards = read_jsonl(
            input.dashboards_file.local_path if input.dashboards_file else ""
        )
        raw_questions = read_jsonl(
            input.questions_file.local_path if input.questions_file else ""
        )
        raw_databases = read_jsonl(
            input.databases_file.local_path if input.databases_file else ""
        )

        logger.info(
            "filter_data: include=%s, exclude=%s",
            input.include_collections,
            input.exclude_collections,
        )

        filtered_collections = filter_collections(
            raw_collections,
            include_collections=input.include_collections,
            exclude_collections=input.exclude_collections,
        )
        accepted_ids = build_accepted_collection_ids(filtered_collections)
        filtered_dashboards = filter_dashboards(raw_dashboards, accepted_ids)
        filtered_questions = filter_questions(raw_questions, accepted_ids)

        c_out = raw_file(input.output_path, "collections_filtered")
        d_out = raw_file(input.output_path, "dashboards_filtered")
        q_out = raw_file(input.output_path, "questions_filtered")
        db_out = raw_file(input.output_path, "databases_filtered")

        write_jsonl(c_out, filtered_collections)
        write_jsonl(d_out, filtered_dashboards)
        write_jsonl(q_out, filtered_questions)
        # Databases pass through unfiltered (matches v2).
        write_jsonl(db_out, raw_databases)

        total = (
            len(filtered_collections)
            + len(filtered_dashboards)
            + len(filtered_questions)
            + len(raw_databases)
        )
        logger.info(
            "filter_data: collections=%d, dashboards=%d, questions=%d, databases=%d",
            len(filtered_collections),
            len(filtered_dashboards),
            len(filtered_questions),
            len(raw_databases),
        )

        return FilterOutput(
            collections_filtered_file=_ref(c_out),
            dashboards_filtered_file=_ref(d_out),
            questions_filtered_file=_ref(q_out),
            databases_filtered_file=_ref(db_out),
            total_records=total,
        )

    @task(
        timeout_seconds=3600, heartbeat_timeout_seconds=120, auto_heartbeat_seconds=30
    )
    async def extract_individual_dashboards(
        self, input: FetchDetailInput
    ) -> FetchOutput:
        """Fetch per-dashboard detail (incl. ``ordered_cards``)."""
        client = await self._build_client(input)
        filtered_dashboards = read_jsonl(
            input.source_file.local_path if input.source_file else ""
        )
        logger.info(
            "extract_individual_dashboards: fetching detail for %d dashboards",
            len(filtered_dashboards),
        )
        records = await fetch_dashboards_details(
            client, filtered_dashboards, input.output_path
        )
        out = raw_file(input.output_path, "dashboard_details")
        write_jsonl(out, records)
        logger.info("extract_individual_dashboards: wrote %d records", len(records))
        return FetchOutput(
            typename="dashboard_details",
            record_count=len(records),
            output_file=_ref(out),
        )

    @task(
        timeout_seconds=3600, heartbeat_timeout_seconds=120, auto_heartbeat_seconds=30
    )
    async def extract_individual_databases(
        self, input: FetchDetailInput
    ) -> FetchOutput:
        """Fetch per-database schema/table metadata."""
        client = await self._build_client(input)
        databases = read_jsonl(
            input.source_file.local_path if input.source_file else ""
        )
        logger.info(
            "extract_individual_databases: fetching metadata for %d databases",
            len(databases),
        )
        records = await fetch_databases_details(client, databases, input.output_path)
        out = raw_file(input.output_path, "database_metadata")
        write_jsonl(out, records)
        logger.info("extract_individual_databases: wrote %d records", len(records))
        return FetchOutput(
            typename="database_metadata",
            record_count=len(records),
            output_file=_ref(out),
        )

    @task(
        timeout_seconds=3600, heartbeat_timeout_seconds=120, auto_heartbeat_seconds=30
    )
    async def fetch_question_queries_activity(
        self, input: FetchDetailInput
    ) -> FetchOutput:
        """Fetch the native SQL string for each filtered question."""
        client = await self._build_client(input)
        questions = read_jsonl(
            input.source_file.local_path if input.source_file else ""
        )
        logger.info(
            "fetch_question_queries_activity: fetching queries for %d questions",
            len(questions),
        )
        records = await fetch_question_queries(client, questions, input.output_path)
        out = raw_file(input.output_path, "question_queries")
        write_jsonl(out, records)
        logger.info("fetch_question_queries_activity: wrote %d records", len(records))
        return FetchOutput(
            typename="question_queries",
            record_count=len(records),
            output_file=_ref(out),
        )

    @task(timeout_seconds=1800)
    async def process_metabaseprocess(self, input: ProcessInput) -> ProcessOutput:
        """Enrich filtered records into the four ``processed/*`` JSONL outputs."""
        filtered_collections = read_jsonl(
            input.collections_filtered_file.local_path
            if input.collections_filtered_file
            else ""
        )
        database_details = read_jsonl(
            input.databases_filtered_file.local_path
            if input.databases_filtered_file
            else ""
        )
        question_queries = read_jsonl(
            input.question_queries_file.local_path
            if input.question_queries_file
            else ""
        )
        dashboard_details = read_jsonl(
            input.dashboard_details_file.local_path
            if input.dashboard_details_file
            else ""
        )
        filtered_questions = read_jsonl(
            input.questions_filtered_file.local_path
            if input.questions_filtered_file
            else ""
        )

        # Resolve metabase_host via the same credential path every other
        # @task uses. The host is needed for sourceURL fields on enriched
        # assets; threading it as a separate field would diverge from the
        # CredentialRef pipeline.
        client = await self._build_client(input)
        metabase_host = client.host or ""
        if not metabase_host:
            logger.warning(
                "process_metabaseprocess: metabase_host is empty; sourceURL "
                "fields will be empty"
            )

        collections_map = generate_collections_map(filtered_collections, metabase_host)
        databases_map = generate_databases_map(database_details, metabase_host)
        questions_query_map = generate_questions_query_map(question_queries)

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
            connection_qualified_name=input.connection_qualified_name,
        )

        c_out = processed_file(input.output_path, "collections")
        d_out = processed_file(input.output_path, "dashboards")
        q_out = processed_file(input.output_path, "questions")
        qd_out = processed_file(input.output_path, "questions_dashboards")

        write_jsonl(c_out, filtered_collections)
        write_jsonl(d_out, enriched_dashboards)
        write_jsonl(q_out, enriched_questions)
        write_jsonl(qd_out, questions_dashboards_lineage)

        total = (
            len(filtered_collections)
            + len(enriched_dashboards)
            + len(enriched_questions)
            + len(questions_dashboards_lineage)
        )
        logger.info(
            "process_metabaseprocess: collections=%d, dashboards=%d, "
            "questions=%d, questions_dashboards=%d",
            len(filtered_collections),
            len(enriched_dashboards),
            len(enriched_questions),
            len(questions_dashboards_lineage),
        )

        return ProcessOutput(
            collections_processed_file=_ref(c_out),
            dashboards_processed_file=_ref(d_out),
            questions_processed_file=_ref(q_out),
            questions_dashboards_processed_file=_ref(qd_out),
            total_records=total,
        )

    # ------------------------------------------------------------------
    # TRANSFORM @task — called once per asset typename from run()
    # ------------------------------------------------------------------
    # SQL lineage (Process / ColumnProcess) is NOT produced here. It is
    # produced by the QueryIntelligence app downstream, which consumes
    # ``attributes.metabaseQuery`` (the SQL string), and
    # ``attributes.metabaseSourceDatabaseName`` /
    # ``attributes.metabaseSourceSchemaName`` (the catalog/schema scope)
    # from the transformed MetabaseQuestion output. See the ``extraNodes``
    # block in ``contract/app.pkl``.

    @task(timeout_seconds=1800)
    async def transform_data(self, input: TransformTaskInput) -> TransformTaskOutput:
        """Transform processed JSONL data into Atlas JSON for one asset typename.

        Reads ``<processed_data_path>/processed/<subdir>/result-0.json``,
        runs each record through a typed pyatlan_v9 asset mapper, and
        writes the serialized entities to
        ``<output_path>/transformed/<typename>/result-<chunk>.json``.

        Asset-mapper migration: this used to consume YAML + Daft via
        ``MetabaseTransformer``. The new path is pure Python — each
        ``app/asset_mapper.py`` function is type-checked end-to-end, so
        attribute drift (the v2 BIProcess.name failure mode) is now a
        compile-time error.
        """
        typename = (input.typename or "").upper()
        if not typename:
            raise MissingTypenameInputError(
                message="transform_data: 'typename' is required",
                field="typename",
            )
        if not input.output_path:
            raise MissingOutputPathInputError(
                message="transform_data: 'output_path' is required",
                field="output_path",
            )

        subdir = TYPENAME_TO_PROCESS_DIR.get(typename, input.typename.lower())
        processed_root = input.processed_data_path or input.output_path
        input_file = os.path.join(
            processed_root, PROCESSED_DIR, subdir, "result-0.json"
        )

        logger.info("transform_data: typename=%s, input_file=%s", typename, input_file)

        records = read_jsonl(input_file)
        if not records:
            logger.info("transform_data: no records found for %s", typename)
            return TransformTaskOutput(typename=typename, record_count=0)

        out_dir = os.path.join(input.output_path, TRANSFORMED_DIR, typename)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"result-{input.chunk_start}.json")

        ctx = dict(
            connection_qualified_name=input.connection_qualified_name,
            connection_name=input.connection_name,
            connector_name="metabase",
            workflow_id=input.workflow_id,
            workflow_run_id=input.workflow_id,
            last_sync_run_at_ms=int(time.time() * 1000),
            tenant_id="default",
        )

        count = 0
        with open(out_file, "wb") as fh:
            for raw in records:
                entity = _map_one_record(typename, raw, ctx)
                if entity is None:
                    continue
                fh.write(orjson.dumps(entity) + b"\n")
                count += 1

        logger.info("transform_data complete: typename=%s, records=%d", typename, count)
        return TransformTaskOutput(typename=typename, record_count=count)

    # ==================================================================
    # @entrypoint methods
    # ==================================================================
    # Two entrypoints — declared in contract/app.pkl as separate DAG nodes:
    #   1. extract_metadata — Collection/Dashboard/Question/BIProcess
    #   2. extract_lineage  — Process/ColumnProcess (cross-connector, runs
    #                         after the platform's QueryIntelligenceNode)

    @entrypoint
    async def extract_metadata(  # type: ignore[override]
        self, input: MetabaseInput
    ) -> MetabaseOutput:
        """End-to-end Metabase metadata extraction + transform.

        Orchestration:
          1. Validate + resolve output path
          2. Resolve credential routing (CredentialRef vs inline)
          3. Extract: collections, dashboards, questions, databases
          4. Filter: apply include/exclude (collection-level; cascades to
             dashboards/questions; databases pass through)
          5. Detail fetch: per-dashboard, per-database, per-question SQL
          6. Enrich: inject sourceURL, source DB/schema, BIProcess records
          7. Transform: fan out across the 4 owned asset typenames
          8. Upload ``transformed/`` tree to object store
          9. Compute lineage-publish state prefixes for the downstream
             LineagePublishNode (no upload yet — that's extract_lineage's job)
         10. Return MetabaseOutput
        """
        output_path = input.output_path or default_output_path(input.workflow_id)
        logger.info("MetabaseApp.run: output_path=%s", output_path)

        # Resolve credentials ONCE and thread through every @task input.
        cred_ref, inline_creds = build_credential_ref(input)

        fetch_input = FetchInput(
            output_path=output_path,
            credential_ref=cred_ref,
            inline_credentials=inline_creds,
        )

        # --- 3. Extract -------------------------------------------------
        collections = await self.extract_collections(fetch_input)
        dashboards = await self.extract_dashboards(fetch_input)
        questions = await self.extract_questions(fetch_input)
        databases = await self.extract_databases(fetch_input)

        # --- 4. Filter --------------------------------------------------
        filtered = await self.filter_data(
            FilterInput(
                output_path=output_path,
                include_collections=input.include_collections,
                exclude_collections=input.exclude_collections,
                collections_file=collections.output_file,
                dashboards_file=dashboards.output_file,
                questions_file=questions.output_file,
                databases_file=databases.output_file,
                credential_ref=cred_ref,
                inline_credentials=inline_creds,
            )
        )

        # --- 5. Detail fetch -------------------------------------------
        dashboard_details = await self.extract_individual_dashboards(
            FetchDetailInput(
                output_path=output_path,
                source_file=filtered.dashboards_filtered_file,
                credential_ref=cred_ref,
                inline_credentials=inline_creds,
            )
        )
        # extract_individual_databases is run for parity with the v2
        # marketplace-scripts pipeline (and to keep ``raw/database_metadata/``
        # available in the artifact bundle for diagnostics). Its result file
        # is not consumed downstream — the QueryIntelligence app resolves
        # source tables against Atlan-known assets directly, not against
        # raw Metabase database metadata.
        await self.extract_individual_databases(
            FetchDetailInput(
                output_path=output_path,
                source_file=filtered.databases_filtered_file,
                credential_ref=cred_ref,
                inline_credentials=inline_creds,
            )
        )
        question_queries = await self.fetch_question_queries_activity(
            FetchDetailInput(
                output_path=output_path,
                source_file=filtered.questions_filtered_file,
                credential_ref=cred_ref,
                inline_credentials=inline_creds,
            )
        )

        # --- 6. Enrich --------------------------------------------------
        # Output (processed/*) is read by transform_data via its
        # processed_data_path argument; the FetchOutput returned here is
        # informational.
        await self.process_metabaseprocess(
            ProcessInput(
                output_path=output_path,
                collections_filtered_file=filtered.collections_filtered_file,
                databases_filtered_file=filtered.databases_filtered_file,
                question_queries_file=question_queries.output_file,
                dashboard_details_file=dashboard_details.output_file,
                questions_filtered_file=filtered.questions_filtered_file,
                credential_ref=cred_ref,
                inline_credentials=inline_creds,
                connection_qualified_name=input.connection.attributes.qualified_name,
            )
        )

        # SQL lineage (Process / ColumnProcess) is produced downstream by
        # the QueryIntelligenceNode in the DAG — see contract/app.pkl
        # ``extraNodes``. The QI node consumes ``attributes.metabaseQuery``
        # (the SQL string), ``attributes.metabaseSourceDatabaseName``, and
        # ``attributes.metabaseSourceSchemaName`` from this app's
        # transformed MetabaseQuestion output to resolve table refs against
        # source assets already crawled by their owning connector.

        # --- 7. Transform (fan-out) ------------------------------------
        connection_qn = input.connection.attributes.qualified_name
        connection_name = input.connection.attributes.name
        processed_data_path = input.processed_data_path or output_path
        total_transformed = 0
        for typename in TRANSFORM_ASSET_TYPES:
            stats = await self.transform_data(
                TransformTaskInput(
                    workflow_id=input.workflow_id,
                    output_path=output_path,
                    processed_data_path=processed_data_path,
                    connection_qualified_name=connection_qn,
                    connection_name=connection_name,
                    typename=typename,
                    chunk_start=input.chunk_start,
                )
            )
            total_transformed += stats.record_count

        # --- 8. Upload transformed/ tree -------------------------------
        transformed_dir = os.path.join(output_path, TRANSFORMED_DIR)
        transformed_data_prefix = ""
        if os.path.isdir(transformed_dir):
            upload = await self.upload(
                UploadInput(local_path=transformed_dir, tier=StorageTier.RETAINED)
            )
            transformed_data_prefix = upload.ref.storage_path or ""

        # Upload residual/ (tolerated-failure records — see app/residuals.py)
        # so they survive pod teardown instead of being stranded on ephemeral
        # local disk. Only present when at least one failure was recorded.
        residual_dir = os.path.join(output_path, RESIDUAL_DIR)
        residual_failures_file = None
        if os.path.isdir(residual_dir):
            residual_upload = await self.upload(
                UploadInput(local_path=residual_dir, tier=StorageTier.RETAINED)
            )
            residual_failures_file = residual_upload.ref

        # --- 9. Compute lineage path prefixes for downstream nodes ----
        # The QueryIntelligenceNode writes to `view_lineage_output_prefix`;
        # extract_lineage reads from it. LineagePublishNode reads its blue-
        # green + current-state buckets from these.
        view_lineage_output_prefix = (
            f"artifacts/apps/metabase/workflows/{input.workflow_id}/"
            f"{self.run_id}/view-lineage"
        )
        lineage_stage_prefix = (
            f"artifacts/apps/metabase/workflows/{input.workflow_id}/"
            f"{self.run_id}/lineage-stage"
        )
        publish_state_prefix = (
            f"persistent-artifacts/apps/atlan-publish-app/state/"
            f"{connection_qn}/publish-state"
        )
        current_state_prefix = f"argo-artifacts/{connection_qn}/current-state"
        lineage_publish_state_prefix = (
            f"persistent-artifacts/apps/atlan-publish-app/state/"
            f"{connection_qn}/lineage/publish-state"
        )
        lineage_current_state_prefix = (
            f"argo-artifacts/{connection_qn}/lineage/current-state"
        )

        # --- 10. Return -------------------------------------------------
        return MetabaseOutput(
            transformed_data_prefix=transformed_data_prefix,
            connection_qualified_name=connection_qn,
            output_path=output_path,
            view_lineage_output_prefix=view_lineage_output_prefix,
            publish_state_prefix=publish_state_prefix,
            current_state_prefix=current_state_prefix,
            lineage_publish_state_prefix=lineage_publish_state_prefix,
            lineage_current_state_prefix=lineage_current_state_prefix,
            lineage_stage_prefix=lineage_stage_prefix,
            total_records=total_transformed,
            residual_failures_file=residual_failures_file,
        )

    # ------------------------------------------------------------------
    # LINEAGE @task — file I/O for extract_lineage
    # ------------------------------------------------------------------
    # Lives in a @task (not the entrypoint) because Temporal's workflow
    # sandbox blocks built-in open() inside workflow code. Activities
    # are free to do file I/O.

    @task(timeout_seconds=1800)
    async def build_lineage_records(
        self, input: BuildLineageInput
    ) -> BuildLineageOutput:
        """Read QI parsed-SQL output and stage Process + ColumnProcess NDJSON.

        QI NDJSON shape: ``{sql, gudusoft: {dbobjs, relationships}, extra: {…}}``
        (current Gudusoft 3.x output) — see app/lineage/qi_reader.py for the
        format coercion and app/lineage/ars_builder.py for the ARS 2.0
        ``arsIdentity``-bearing record construction.
        """
        processes: list[dict[str, Any]] = []
        column_processes: list[dict[str, Any]] = []

        for record in iter_qi_records(input.qi_local_path):
            query_id, sql, source_tables, source_columns = parse_qi_record(record)
            if not query_id:
                continue
            # QI emits the source-question qualifiedName at
            # extra.attributes.qualifiedName (current) or top-level
            # QUERY_ID (legacy). Format is
            # ``default/metabase/<conn>/questions/<id>``; the trailing id
            # is what builds the BIProcess QN.
            question_id = query_id.rsplit("/", 1)[-1]
            question_name = _qi_question_name(record) or query_id

            process = build_process(
                connection_qualified_name=input.connection_qualified_name,
                connection_name=input.connection_name,
                question_id=question_id,
                question_name=question_name,
                sql=sql,
                source_tables=source_tables,
            )
            if process is None:
                continue
            processes.append(process)

            cp = build_column_process(
                connection_qualified_name=input.connection_qualified_name,
                connection_name=input.connection_name,
                question_id=question_id,
                question_name=question_name,
                sql=sql,
                source_columns=source_columns,
                parent_process_hash=process_hash(question_id, sql),
            )
            if cp is not None:
                column_processes.append(cp)

        # ARS 2.0 producer-split convention: records carrying arsIdentity
        # MUST land under a ``resolvable/`` subdirectory of the transformed
        # data prefix. publish-app's Step 0 ARS resolver globs
        # ``{transformed_data_prefix}/resolvable/**/*.json`` — files outside
        # that subdir are treated as plain entities and the arsIdentity
        # block is ignored. See atlan-publish-app
        # ``app/lib/partitioning/resolve/orchestrator.py:104`` (resolvable_glob)
        # and ``app/lib/partitioning/duckdb_partitioner.py:535`` (Step 0
        # call site). Skipping this subdir is what was causing every
        # Process publish to land ATLAS-400-00-021 — the resolver never
        # saw our records, the arsIdentity refs never got UNNESTed, and
        # publish-app posted malformed ObjectIds to Atlas.
        stage_dir = os.path.join(input.output_path, "lineage-stage")
        resolvable_dir = os.path.join(stage_dir, "resolvable")
        os.makedirs(os.path.join(resolvable_dir, "PROCESS"), exist_ok=True)
        os.makedirs(os.path.join(resolvable_dir, "COLUMNPROCESS"), exist_ok=True)
        write_jsonl(os.path.join(resolvable_dir, "PROCESS", "result-0.json"), processes)
        write_jsonl(
            os.path.join(resolvable_dir, "COLUMNPROCESS", "result-0.json"),
            column_processes,
        )

        return BuildLineageOutput(
            stage_dir=stage_dir,
            process_count=len(processes),
            column_process_count=len(column_processes),
        )

    # ------------------------------------------------------------------
    # extract_lineage — second @entrypoint
    # ------------------------------------------------------------------
    # Intentional Hightouch-style two-entrypoint app: contract/app.pkl sets
    # emitEntrypoints=false so the platform treats this as ONE marketplace
    # connector card (see app.pkl lines 14-29). extract_lineage is never
    # dispatched via the multi-entrypoint HTTP contract path P016 checks —
    # it's invoked directly by workflowType "metabase:extract-lineage" from
    # the extraNodes DAG (app.pkl lines 178-213). Adding a named Entrypoint
    # block would flip the platform into its multi-entrypoint branch, which
    # 404s (BLDX-1425); removing the @entrypoint would break lineage
    # publishing. The proper fix — toolkit-native packageId grouping on
    # Entrypoint — lands with BLDX-1342 (application-sdk#2351, unreleased);
    # revisit this suppression once it ships.
    # conformance: ignore[P016] intentional single-card two-entrypoint app, see comment above (BLDX-1342 pending)
    @entrypoint
    async def extract_lineage(
        self, input: MetabaseLineageInput
    ) -> MetabaseLineageOutput:
        """Build Process + ColumnProcess from QueryIntelligence parsed-SQL output.

        The platform's QueryIntelligenceNode runs against our transformed
        MetabaseQuestion JSON (consuming ``attributes.metabaseQuery`` +
        ``attributes.metabaseSourceDatabaseName``) and writes its parsed-SQL
        output to ``input.view_lineage_input_prefix``. This entrypoint reads
        that output, constructs Process + ColumnProcess records with
        cross-connector qualified names (the platform's lineage-publish layer
        resolves them to actual upstream Atlan assets), and writes the
        result NDJSON to ``lineage_stage_prefix`` for LineagePublishNode to
        consume.

        For initial v3 cutover this is a thin reader that:
          1. Reads parsed-SQL NDJSON from ``view_lineage_input_prefix``
          2. For each parsed query, emits one Process (Question →
             upstream tables) and one ColumnProcess (Question →
             upstream columns) record
          3. Uploads the result tree to ``lineage_stage_prefix``

        Cross-connector resolution policy (matches v2 marketplace-scripts):
        upstream table refs use ``connector_name + db_name + schema + table``
        QNs; the platform resolves to concrete Atlan asset QNs during
        lineage-publish.
        """
        connection_qn = (
            input.connection_qualified_name
            or input.connection.attributes.qualified_name
        )
        output_path = input.output_path or default_output_path(input.workflow_id)
        connection_name = input.connection.attributes.name or "metabase"
        logger.info(
            "MetabaseApp.extract_lineage: connection_qn=%s, view_lineage_input_prefix=%s",
            connection_qn,
            input.view_lineage_input_prefix,
        )

        # Download QI parsed-SQL output from the object store. The Pkl
        # contract threads ``view_lineage_input_prefix`` as a storage key
        # (``$.extract.outputs.view_lineage_output_prefix``); iter_qi_records
        # needs a local path. download() handles a missing prefix gracefully
        # (returns an empty local dir, file_count=0).
        qi_local_path = ""
        if input.view_lineage_input_prefix:
            dl = await self.download(
                DownloadInput(storage_path=input.view_lineage_input_prefix)
            )
            qi_local_path = dl.ref.local_path or ""

        # File I/O (read QI NDJSON, write Process/ColumnProcess staging) is
        # delegated to build_lineage_records — the workflow sandbox forbids
        # built-in open() in entrypoint code.
        build = await self.build_lineage_records(
            BuildLineageInput(
                output_path=output_path,
                qi_local_path=qi_local_path,
                connection_qualified_name=connection_qn,
                connection_name=connection_name,
            )
        )

        # Upload lineage-stage/ to object store at the canonical prefix.
        lineage_stage_prefix = ""
        if build.stage_dir:
            upload = await self.upload(
                UploadInput(local_path=build.stage_dir, tier=StorageTier.RETAINED)
            )
            lineage_stage_prefix = upload.ref.storage_path or ""

        lineage_publish_state_prefix = (
            f"persistent-artifacts/apps/atlan-publish-app/state/"
            f"{connection_qn}/lineage/publish-state"
        )
        lineage_current_state_prefix = (
            f"argo-artifacts/{connection_qn}/lineage/current-state"
        )

        logger.info(
            "MetabaseApp.extract_lineage complete: processes=%d, column_processes=%d, "
            "lineage_stage_prefix=%s",
            build.process_count,
            build.column_process_count,
            lineage_stage_prefix,
        )

        return MetabaseLineageOutput(
            lineage_stage_prefix=lineage_stage_prefix,
            connection_qualified_name=connection_qn,
            lineage_publish_state_prefix=lineage_publish_state_prefix,
            lineage_current_state_prefix=lineage_current_state_prefix,
            process_count=build.process_count,
            column_process_count=build.column_process_count,
        )
