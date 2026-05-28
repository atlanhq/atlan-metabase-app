"""Metabase v3 connector — single ``run()`` orchestrator.

Architecture mirrors ``atlan-openapi-app``: one ``App`` subclass with one
``async def run()`` override and a flat fan-out of ``@task`` methods.

The platform (platform-packages) dispatches Metabase as **one** Argo
workflow instance with extract→publish as nested DAG nodes (not two
separate workflow submissions), so a single entrypoint matches the platform
shape exactly. The previous two-``@entrypoint`` shape (extract_metadata,
transform_metadata) is replaced by inline orchestration inside ``run()``.

The ``MetabaseHandler`` (imported below to register it for the SDK)
serves the platform endpoints: ``/workflows/v1/auth``,
``/workflows/v1/check``, ``/workflows/v1/metadata``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import daft
from application_sdk.app import App, entrypoint, task
from application_sdk.contracts.storage import UploadInput
from application_sdk.contracts.types import FileReference, StorageTier
from application_sdk.credentials.ref import CredentialRef
from application_sdk.observability.logger_adaptor import get_logger

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
    MetabaseCredential,
    MetabaseInput,
    MetabaseLineageInput,
    MetabaseLineageOutput,
    MetabaseOutput,
    ProcessInput,
    ProcessOutput,
    TransformTaskInput,
    TransformTaskOutput,
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
from app.lineage.qi_reader import iter_qi_records, parse_qi_record
from app.transformers import MetabaseTransformer
from app.utils import read_jsonl, write_jsonl

logger = get_logger(__name__)


# Module-level constants — Temporal sandbox blocks os.environ inside run().
_RAW_DIR = "raw"
_PROCESSED_DIR = "processed"
_TRANSFORMED_DIR = "transformed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_credential_ref(
    input: MetabaseInput,
) -> tuple[CredentialRef | None, dict[str, Any]]:
    """Resolve credentials from the three supported paths into (ref, inline).

    Exactly one of the returned values is populated:

    - ``credential_ref`` — from ``input.metabase_credential`` (PKL contract)
      or constructed from ``input.credential_guid`` (legacy GUID).
    - ``inline_credentials`` — from ``input.credentials`` (list[{key,value}]
      from the HTTP service layer, or a flat dict for local dev).

    Tasks read ``credential_ref`` first; if absent they fall back to inline.
    """
    if input.metabase_credential is not None:
        return input.metabase_credential, {}
    if input.credential_guid:
        ref = CredentialRef(
            name=input.credential_guid,
            credential_type="basic",
            credential_guid=input.credential_guid,
        )
        return ref, {}
    # Inline fallback (local-dev or service-passed raw creds).
    inline: dict[str, Any] = {}
    creds = input.credentials
    if isinstance(creds, list):
        for item in creds:
            if isinstance(item, dict) and "key" in item:
                inline[item["key"]] = item.get("value", "")
    elif isinstance(creds, dict):
        inline = creds
    return None, inline


def _parse_credential_dict(raw: dict[str, Any]) -> MetabaseCredential:
    """Coerce a raw credential dict into a typed :class:`MetabaseCredential`.

    Accepts both the flat HTTP shape (``{host, port, username, password}``)
    and the v2 nested shape (``{host, port, extra: {username, password}}``).
    """
    if not raw:
        return MetabaseCredential()
    flat = dict(raw)
    extra = raw.get("extra")
    if isinstance(extra, dict):
        for k, v in extra.items():
            flat.setdefault(k, v)
    return MetabaseCredential(
        host=str(flat.get("host", "") or ""),
        port=int(flat.get("port", 443) or 443),
        username=str(flat.get("username", "") or ""),
        password=str(flat.get("password", "") or ""),
    )


def _default_output_path(workflow_id: str) -> str:
    """Build a sensible local output path when AE doesn't supply one."""
    base = Path(tempfile.gettempdir()) / "atlan-metabase-app"
    if workflow_id:
        base = base / workflow_id
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def _raw_file(output_path: str, name: str) -> str:
    return os.path.join(output_path, _RAW_DIR, name, "result-0.json")


def _processed_file(output_path: str, name: str) -> str:
    return os.path.join(output_path, _PROCESSED_DIR, name, "result-0.json")


def _ref(local_path: str) -> FileReference:
    return FileReference(local_path=local_path, tier=StorageTier.RETAINED)


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
    passthrough_modules = {"app.transformers", "app.lineage"}

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
                raise ValueError(
                    "_build_client: no credential_ref or inline_credentials"
                )
            raw_creds = inline

        credential = _parse_credential_dict(raw_creds)
        return await build_client(credential)

    # ------------------------------------------------------------------
    # EXTRACTION @tasks
    # ------------------------------------------------------------------

    @task(timeout_seconds=600)
    async def extract_collections(self, input: FetchInput) -> FetchOutput:
        """Fetch all collections → ``raw/collections/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_collections_summaries(client)
        out = _raw_file(input.output_path, "collections")
        write_jsonl(out, records)
        logger.info(f"extract_collections: wrote {len(records)} records")
        return FetchOutput(
            typename="collections", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def extract_dashboards(self, input: FetchInput) -> FetchOutput:
        """Fetch dashboard summaries → ``raw/dashboards/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_dashboards_summaries(client)
        out = _raw_file(input.output_path, "dashboards")
        write_jsonl(out, records)
        logger.info(f"extract_dashboards: wrote {len(records)} records")
        return FetchOutput(
            typename="dashboards", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def extract_questions(self, input: FetchInput) -> FetchOutput:
        """Fetch question (card) summaries → ``raw/questions/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_questions_summaries(client)
        out = _raw_file(input.output_path, "questions")
        write_jsonl(out, records)
        logger.info(f"extract_questions: wrote {len(records)} records")
        return FetchOutput(
            typename="questions", record_count=len(records), output_file=_ref(out)
        )

    @task(timeout_seconds=600)
    async def extract_databases(self, input: FetchInput) -> FetchOutput:
        """Fetch database summaries → ``raw/databases/result-0.json``."""
        client = await self._build_client(input)
        records = await fetch_databases_summaries(client)
        out = _raw_file(input.output_path, "databases")
        write_jsonl(out, records)
        logger.info(f"extract_databases: wrote {len(records)} records")
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

        c_out = _raw_file(input.output_path, "collections_filtered")
        d_out = _raw_file(input.output_path, "dashboards_filtered")
        q_out = _raw_file(input.output_path, "questions_filtered")
        db_out = _raw_file(input.output_path, "databases_filtered")

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
        records = await fetch_dashboards_details(client, filtered_dashboards)
        out = _raw_file(input.output_path, "dashboard_details")
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
        records = await fetch_databases_details(client, databases)
        out = _raw_file(input.output_path, "database_metadata")
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
        records = await fetch_question_queries(client, questions)
        out = _raw_file(input.output_path, "question_queries")
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

        c_out = _processed_file(input.output_path, "collections")
        d_out = _processed_file(input.output_path, "dashboards")
        q_out = _processed_file(input.output_path, "questions")
        qd_out = _processed_file(input.output_path, "questions_dashboards")

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

        Reads ``<processed_data_path>/processed/<subdir>/result-0.json`` and
        writes Atlas entities to
        ``<output_path>/transformed/<typename>/result-<chunk>.json``.
        """
        typename = (input.typename or "").upper()
        if not typename:
            raise ValueError("transform_data: 'typename' is required")
        if not input.output_path:
            raise ValueError("transform_data: 'output_path' is required")

        subdir = TYPENAME_TO_PROCESS_DIR.get(typename, input.typename.lower())
        processed_root = input.processed_data_path or input.output_path
        input_file = os.path.join(
            processed_root, _PROCESSED_DIR, subdir, "result-0.json"
        )

        logger.info("transform_data: typename=%s, input_file=%s", typename, input_file)

        records = read_jsonl(input_file)
        if not records:
            logger.info("transform_data: no records found for %s", typename)
            return TransformTaskOutput(typename=typename, record_count=0)

        transformer = MetabaseTransformer()
        # Strip raw Metabase fields the transformer YAML doesn't read. Daft's
        # type inference fails when a column varies across rows — e.g.
        # ``visualization_settings`` is a different-shaped dict per question,
        # ``dataset_query`` has variant schemas, ``result_metadata`` mixes
        # snake_case and kebab-case keys across Metabase versions. None of
        # these are referenced by any transformer YAML (see
        # ``app/transformers/*.yaml``), so dropping them is safe and avoids
        # the Daft ``Need at least 1 series to perform concat`` and
        # ``casting from Struct ... to String not implemented`` panics.
        _DROP_KEYS = {
            "cache_invalidated_at",
            "cache_ttl",
            "dashboards",
            "dashcards",
            "dataset_query",
            "last-edit-info",
            "legacy_query",
            "metabase_version",
            "ordered_cards",
            "param_fields",
            "param_values",
            "parameter_mappings",
            "parameters",
            "query_description",
            "result_metadata",
            "source_card_id",
            "table_id",
            "view_count",
            "visualization_settings",
        }
        records = [{k: v for k, v in r.items() if k not in _DROP_KEYS} for r in records]
        dataframe = daft.from_pylist(records)

        transform_kwargs: dict[str, Any] = {
            "workflow_id": input.workflow_id,
            "workflow_run_id": input.workflow_id,
            "connection_name": input.connection_name,
            "connection_qualified_name": input.connection_qualified_name,
        }

        out_dir = os.path.join(input.output_path, _TRANSFORMED_DIR, typename)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"result-{input.chunk_start}.json")

        transformed_df = transformer.transform_metadata(
            typename=typename,
            dataframe=dataframe,
            **transform_kwargs,
        )

        if transformed_df is None:
            return TransformTaskOutput(typename=typename, record_count=0)

        result_dict = transformed_df.to_pydict()
        rows = len(result_dict.get("typeName", []))
        with open(out_file, "w", encoding="utf-8") as fh:
            for i in range(rows):
                entity = {
                    "typeName": result_dict["typeName"][i],
                    "status": result_dict["status"][i],
                    "attributes": result_dict["attributes"][i],
                }
                fh.write(json.dumps(entity, ensure_ascii=False) + "\n")

        logger.info("transform_data complete: typename=%s, records=%d", typename, rows)
        return TransformTaskOutput(typename=typename, record_count=rows)

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
        output_path = input.output_path or _default_output_path(input.workflow_id)
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
        transformed_dir = os.path.join(output_path, _TRANSFORMED_DIR)
        transformed_data_prefix = ""
        if os.path.isdir(transformed_dir):
            upload = await self.upload(
                UploadInput(local_path=transformed_dir, tier=StorageTier.RETAINED)
            )
            transformed_data_prefix = upload.ref.storage_path or ""

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

        QI NDJSON shape: {QUERY_ID, SQL, PARSED_DATA{dbobjs, relationships}, OUTPUT_FLAGS}
        See app/lineage/qi_reader.py for format coercion and
        app/lineage/ars_builder.py for the PARTIAL_OBJECT / PARTIAL_FIELD
        record construction.
        """
        processes: list[dict[str, Any]] = []
        column_processes: list[dict[str, Any]] = []

        for record in iter_qi_records(input.view_lineage_input_prefix):
            query_id, sql, source_tables, source_columns = parse_qi_record(record)
            if not query_id:
                continue
            # QI QUERY_ID is the MetabaseQuestion qualifiedName
            # (default/metabase/<conn>/questions/<id>) — extract the trailing id.
            question_id = query_id.rsplit("/", 1)[-1]
            question_name = record.get("QUESTION_NAME") or query_id

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

        stage_dir = os.path.join(input.output_path, "lineage-stage")
        os.makedirs(os.path.join(stage_dir, "PROCESS"), exist_ok=True)
        os.makedirs(os.path.join(stage_dir, "COLUMNPROCESS"), exist_ok=True)
        write_jsonl(os.path.join(stage_dir, "PROCESS", "result-0.json"), processes)
        write_jsonl(
            os.path.join(stage_dir, "COLUMNPROCESS", "result-0.json"),
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
        output_path = input.output_path or _default_output_path(input.workflow_id)
        connection_name = input.connection.attributes.name or "metabase"
        logger.info(
            "MetabaseApp.extract_lineage: connection_qn=%s, view_lineage_input_prefix=%s",
            connection_qn,
            input.view_lineage_input_prefix,
        )

        # File I/O (read QI NDJSON, write Process/ColumnProcess staging) is
        # delegated to build_lineage_records — the workflow sandbox forbids
        # built-in open() in entrypoint code.
        build = await self.build_lineage_records(
            BuildLineageInput(
                output_path=output_path,
                view_lineage_input_prefix=input.view_lineage_input_prefix,
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
