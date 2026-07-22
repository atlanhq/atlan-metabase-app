"""Unit tests for app/connector.py.

Covers:
- _build_client credential routing (CredentialRef vs inline; error path).
- @task method bodies — each one mocks the API client and asserts the
  JSONL output is written + FileReference is returned correctly.
- extract_metadata @entrypoint orchestration — mocks every @task and
  asserts the call sequence + returned MetabaseOutput path fields.
- extract_lineage @entrypoint — feeds canonical QI NDJSON through the
  pipeline and verifies Process/ColumnProcess records hit lineage-stage/.

Credential helpers and path helpers are tested in test_credentials.py and
test_paths.py respectively.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef
from application_sdk.errors import InvalidInputError

from app.connector import (
    MetabaseApp,
    _build_process_records,
    _ref,
    read_jsonl,
    write_jsonl,
)
from app.contracts import (
    BuildLineageInput,
    FetchInput,
    FilterInput,
    MetabaseInput,
    MetabaseLineageInput,
)


class TestRef:
    def test_ref_returns_retained_file_reference(self):
        ref = _ref("/tmp/out/raw/collections/result-0.json")
        assert isinstance(ref, FileReference)
        assert ref.local_path == "/tmp/out/raw/collections/result-0.json"


# ---------------------------------------------------------------------------
# _build_client credential resolution
# ---------------------------------------------------------------------------


class TestBuildClient:
    @pytest.fixture
    def app(self):
        return MetabaseApp()

    @pytest.mark.asyncio
    async def test_inline_credentials_path(self, app):
        """When no credential_ref, falls back to inline_credentials."""
        with patch("app.connector.build_client", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = MagicMock()
            fake_input = MagicMock()
            fake_input.credential_ref = None
            fake_input.inline_credentials = {
                "host": "http://x",
                "port": 3000,
                "username": "u",
                "password": "p",
            }
            await app._build_client(fake_input)
            cred = mock_build.call_args[0][0]
            assert cred.host == "http://x"
            assert cred.username == "u"

    @pytest.mark.asyncio
    async def test_credential_ref_path_resolves_via_context(self, app):
        """When credential_ref is present, looks up via self.context."""
        ref = CredentialRef(name="x", credential_type="basic", credential_guid="g")
        fake_input = MagicMock()
        fake_input.credential_ref = ref

        with (
            patch.object(
                MetabaseApp, "context", create=True, new_callable=MagicMock
            ) as mock_ctx,
            patch("app.connector.build_client", new_callable=AsyncMock) as mock_build,
        ):
            mock_ctx.resolve_credential_raw = AsyncMock(
                return_value={"host": "h", "username": "u", "password": "p"}
            )
            mock_build.return_value = MagicMock()
            await app._build_client(fake_input)
            mock_ctx.resolve_credential_raw.assert_awaited_once_with(ref)

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self, app):
        """Empty inline + no ref → ValueError."""
        fake_input = MagicMock()
        fake_input.credential_ref = None
        fake_input.inline_credentials = {}
        with pytest.raises(
            InvalidInputError, match="no credential_ref or inline_credentials"
        ):
            await app._build_client(fake_input)


# ---------------------------------------------------------------------------
# @task method bodies — drive each one with a mocked client + extract fn
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_mock_client():
    """MetabaseApp with _build_client patched to return a sentinel mock client."""
    app = MetabaseApp()
    app._build_client = AsyncMock(return_value=MagicMock())
    return app


@pytest.fixture
def fetch_input(tmp_path):
    return FetchInput(output_path=str(tmp_path), inline_credentials={"host": "h"})


class TestExtractTasks:
    @pytest.mark.asyncio
    async def test_extract_collections(self, app_with_mock_client, fetch_input):
        records = [{"id": 1, "name": "c1"}, {"id": 2, "name": "c2"}]
        with patch(
            "app.connector.fetch_collections_summaries",
            new_callable=AsyncMock,
            return_value=records,
        ):
            out = await app_with_mock_client.extract_collections(fetch_input)
        assert out.record_count == 2
        assert out.typename == "collections"
        assert out.output_file is not None
        # Verify JSONL is written to raw/collections/result-0.json
        written = Path(out.output_file.local_path)
        assert written.exists()
        lines = written.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == records[0]

    @pytest.mark.asyncio
    async def test_extract_dashboards(self, app_with_mock_client, fetch_input):
        records = [{"id": 1, "name": "d1"}]
        with patch(
            "app.connector.fetch_dashboards_summaries",
            new_callable=AsyncMock,
            return_value=records,
        ):
            out = await app_with_mock_client.extract_dashboards(fetch_input)
        assert out.record_count == 1
        assert out.typename == "dashboards"

    @pytest.mark.asyncio
    async def test_extract_questions(self, app_with_mock_client, fetch_input):
        records = [{"id": 1, "name": "q1"}, {"id": 2, "name": "q2"}]
        with patch(
            "app.connector.fetch_questions_summaries",
            new_callable=AsyncMock,
            return_value=records,
        ):
            out = await app_with_mock_client.extract_questions(fetch_input)
        assert out.record_count == 2
        assert out.typename == "questions"

    @pytest.mark.asyncio
    async def test_extract_databases(self, app_with_mock_client, fetch_input):
        records = [{"id": 1, "name": "db1"}]
        with patch(
            "app.connector.fetch_databases_summaries",
            new_callable=AsyncMock,
            return_value=records,
        ):
            out = await app_with_mock_client.extract_databases(fetch_input)
        assert out.record_count == 1
        assert out.typename == "databases"


class TestFilterDataTask:
    @pytest.mark.asyncio
    async def test_filter_data_drops_excluded_collections(self, tmp_path):
        # Seed the four raw files filter_data reads.
        raw_dir = tmp_path / "raw"
        cf = raw_dir / "collections" / "result-0.json"
        df = raw_dir / "dashboards" / "result-0.json"
        qf = raw_dir / "questions" / "result-0.json"
        dbf = raw_dir / "databases" / "result-0.json"
        for p in (cf, df, qf, dbf):
            p.parent.mkdir(parents=True, exist_ok=True)

        cf.write_text(
            json.dumps({"id": 1, "name": "kept"})
            + "\n"
            + json.dumps({"id": 2, "name": "skipped"})
            + "\n"
        )
        df.write_text(
            json.dumps({"id": 10, "collection_id": 1, "name": "kept-d"})
            + "\n"
            + json.dumps({"id": 11, "collection_id": 2, "name": "skipped-d"})
            + "\n"
        )
        qf.write_text(
            json.dumps({"id": 20, "collection_id": 1, "name": "kept-q"})
            + "\n"
            + json.dumps({"id": 21, "collection_id": 2, "name": "skipped-q"})
            + "\n"
        )
        dbf.write_text(json.dumps({"id": 100, "name": "db"}) + "\n")

        app = MetabaseApp()
        input_obj = FilterInput(
            output_path=str(tmp_path),
            include_collections={},
            exclude_collections={"2": {}},  # type: ignore[arg-type]
            collections_file=FileReference(local_path=str(cf)),
            dashboards_file=FileReference(local_path=str(df)),
            questions_file=FileReference(local_path=str(qf)),
            databases_file=FileReference(local_path=str(dbf)),
        )
        out = await app.filter_data(input_obj)

        # Excluded collection 2 should not appear in any filtered file.
        for ref in (
            out.collections_filtered_file,
            out.dashboards_filtered_file,
            out.questions_filtered_file,
        ):
            assert ref is not None
            assert ref.local_path is not None
            text = Path(ref.local_path).read_text()
            assert "skipped" not in text
        # Databases pass through unfiltered.
        assert out.databases_filtered_file is not None
        assert out.databases_filtered_file.local_path is not None
        db_text = Path(out.databases_filtered_file.local_path).read_text()
        assert "db" in db_text


# ---------------------------------------------------------------------------
# Heartbeat-timeout regression guard — run_in_thread offload
# ---------------------------------------------------------------------------
# Bare write_jsonl()/read_jsonl() calls (and the QI-record parsing loop)
# run synchronous, tenant-scale file I/O directly on the activity's event
# loop. That starves the SDK's auto-heartbeat background task, which shares
# the same loop — Temporal then kills the activity as dead even though it's
# still working (heartbeat-timeout-detector rule HB-13). The offload uses the
# SDK's self.run_in_thread (dedicated sdk-blocking-* pool, isolated from
# Temporal's own worker pool), not bare asyncio.to_thread. These tests spy on
# run_in_thread on a few representative call sites (one write_jsonl site, one
# read_jsonl site, and the build_lineage_records QI loop) so a future revert
# of the offload fails loudly instead of silently.


async def _inline_run_in_thread(func, *args, **kwargs):
    """Test double for App.run_in_thread: runs func inline, no real thread.

    Keeps the real file-I/O side effects the surrounding assertions check
    while letting the spy record how ``self.run_in_thread`` was called.
    (``run_in_thread`` is patched on the class, so the mock is unbound and
    receives ``func`` as its first positional arg — no ``self``.)
    """
    return func(*args, **kwargs)


class TestRunInThreadOffload:
    @pytest.mark.asyncio
    async def test_extract_collections_offloads_write_jsonl(
        self, app_with_mock_client, fetch_input
    ):
        """Representative write_jsonl() site: extract_collections."""
        records = [{"id": 1, "name": "c1"}]
        with (
            patch(
                "app.connector.fetch_collections_summaries",
                new_callable=AsyncMock,
                return_value=records,
            ),
            patch(
                "app.connector.App.run_in_thread", side_effect=_inline_run_in_thread
            ) as mock_run_in_thread,
        ):
            out = await app_with_mock_client.extract_collections(fetch_input)

        assert out.record_count == 1
        assert Path(out.output_file.local_path).exists()
        offloaded = [c.args[0] for c in mock_run_in_thread.call_args_list]
        assert write_jsonl in offloaded, (
            "write_jsonl must run via self.run_in_thread, not directly on "
            "the event loop — see heartbeat-timeout-detector rule HB-13"
        )

    @pytest.mark.asyncio
    async def test_filter_data_offloads_read_jsonl(self, tmp_path):
        """Representative read_jsonl() site: filter_data (4 reads + 4 writes)."""
        raw_dir = tmp_path / "raw"
        paths = [
            raw_dir / "collections" / "result-0.json",
            raw_dir / "dashboards" / "result-0.json",
            raw_dir / "questions" / "result-0.json",
            raw_dir / "databases" / "result-0.json",
        ]
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")

        app = MetabaseApp()
        input_obj = FilterInput(
            output_path=str(tmp_path),
            collections_file=FileReference(local_path=str(paths[0])),
            dashboards_file=FileReference(local_path=str(paths[1])),
            questions_file=FileReference(local_path=str(paths[2])),
            databases_file=FileReference(local_path=str(paths[3])),
        )
        with patch(
            "app.connector.App.run_in_thread", side_effect=_inline_run_in_thread
        ) as mock_run_in_thread:
            await app.filter_data(input_obj)

        offloaded = [c.args[0] for c in mock_run_in_thread.call_args_list]
        assert (
            offloaded.count(read_jsonl) == 4
        ), "filter_data must read all four raw files via self.run_in_thread"
        assert (
            offloaded.count(write_jsonl) == 4
        ), "filter_data must write all four filtered files via self.run_in_thread"

    @pytest.mark.asyncio
    async def test_build_lineage_records_offloads_qi_parsing(self, tmp_path):
        """build_lineage_records: the QI-parsing loop must be materialized in
        a sync helper (``_build_process_records``) and thread-offloaded — a
        sync generator can't be handed to self.run_in_thread mid-iteration."""
        qi_dir = tmp_path / "qi"
        qi_dir.mkdir()
        record = {
            "QUERY_ID": "default/metabase/test/questions/40",
            "SQL": "SELECT customer_name FROM analytics.customers",
            "QUESTION_NAME": "Top Customers",
            "PARSED_DATA": {
                "dbobjs": [
                    {
                        "name": "customers",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                        "vendor_name": "postgres",
                    }
                ],
                "relationships": [
                    {
                        "source": {
                            "column": "customer_name",
                            "table": "customers",
                            "schema": "analytics",
                            "db": "testdata",
                            "vendor_name": "postgres",
                        }
                    }
                ],
            },
        }
        (qi_dir / "out.json").write_text(json.dumps(record) + "\n")

        app = MetabaseApp()
        input_obj = BuildLineageInput(
            output_path=str(tmp_path / "out"),
            qi_local_path=str(qi_dir),
            connection_qualified_name="default/metabase/test",
            connection_name="metabase-test",
        )
        with patch(
            "app.connector.App.run_in_thread", side_effect=_inline_run_in_thread
        ) as mock_run_in_thread:
            out = await app.build_lineage_records(input_obj)

        assert out.process_count == 1
        offloaded = [c.args[0] for c in mock_run_in_thread.call_args_list]
        assert _build_process_records in offloaded, (
            "the QI record loop must run via self.run_in_thread, not "
            "iterate directly on the event loop — see HB-13"
        )
        assert write_jsonl in offloaded


# ---------------------------------------------------------------------------
# extract_metadata orchestration — every @task mocked
# ---------------------------------------------------------------------------


class TestExtractMetadataOrchestration:
    @pytest.fixture
    def connection(self):
        return ConnectionRef.model_validate(
            {
                "attributes": {
                    "name": "metabase-test",
                    "qualified_name": "default/metabase/test",
                }
            }
        )

    @pytest.fixture
    def metabase_input(self, connection, tmp_path):
        return MetabaseInput(
            workflow_id="wf-1",
            connection=connection,
            credentials=[{"key": "host", "value": "http://x"}],
            output_path=str(tmp_path),
        )

    @pytest.mark.asyncio
    async def test_returns_all_path_fields(self, metabase_input):
        """The output dataclass exposes every prefix the DAG threads off of."""
        app = MetabaseApp()
        # Patch every @task body to a fast-success stub.
        fake_fetch = MagicMock(
            output_file=FileReference(local_path="/tmp/x.json"),
            record_count=0,
            typename="t",
        )
        fake_filter = MagicMock(
            collections_filtered_file=FileReference(local_path="/tmp/c.json"),
            dashboards_filtered_file=FileReference(local_path="/tmp/d.json"),
            questions_filtered_file=FileReference(local_path="/tmp/q.json"),
            databases_filtered_file=FileReference(local_path="/tmp/db.json"),
            total_records=0,
        )
        fake_process = MagicMock(total_records=0)
        fake_transform = MagicMock(record_count=0)

        app.extract_collections = AsyncMock(return_value=fake_fetch)
        app.extract_dashboards = AsyncMock(return_value=fake_fetch)
        app.extract_questions = AsyncMock(return_value=fake_fetch)
        app.extract_databases = AsyncMock(return_value=fake_fetch)
        app.filter_data = AsyncMock(return_value=fake_filter)
        app.extract_individual_dashboards = AsyncMock(return_value=fake_fetch)
        app.extract_individual_databases = AsyncMock(return_value=fake_fetch)
        app.fetch_question_queries_activity = AsyncMock(return_value=fake_fetch)
        app.process_metabaseprocess = AsyncMock(return_value=fake_process)
        app.transform_data = AsyncMock(return_value=fake_transform)
        # Stub run_id so path formatting works without SDK context.
        type(app).run_id = property(lambda _self: "run-xyz")  # type: ignore[misc]
        # Bypass self.upload() context dependency.
        app.upload = AsyncMock(return_value=MagicMock(ref=MagicMock(storage_path="")))

        out = await app.extract_metadata(metabase_input)  # type: ignore[call-arg]

        assert out.connection_qualified_name == "default/metabase/test"
        assert out.view_lineage_output_prefix.endswith("/view-lineage")
        assert out.lineage_stage_prefix.endswith("/lineage-stage")
        assert "publish-state" in out.publish_state_prefix
        assert "current-state" in out.current_state_prefix
        assert "lineage/publish-state" in out.lineage_publish_state_prefix
        assert "lineage/current-state" in out.lineage_current_state_prefix
        # transform_data must be called once per asset typename.
        assert app.transform_data.await_count == 4
        # No residual/ dir was ever created — no upload, no reference.
        assert out.residual_failures is None

    @pytest.mark.asyncio
    async def test_uploads_residual_dir_when_failures_were_recorded(
        self, metabase_input, tmp_path
    ):
        """A residual/ dir (written by record_residual_failure) is uploaded
        as a durable RETAINED reference and returned on the output."""
        from app.residuals import RESIDUAL_DIR, record_residual_failure

        record_residual_failure(
            str(tmp_path), "collections_fetch_failed", http_status=500
        )
        assert (tmp_path / RESIDUAL_DIR).is_dir()

        app = MetabaseApp()
        fake_fetch = MagicMock(
            output_file=FileReference(local_path="/tmp/x.json"),
            record_count=0,
            typename="t",
        )
        fake_filter = MagicMock(
            collections_filtered_file=FileReference(local_path="/tmp/c.json"),
            dashboards_filtered_file=FileReference(local_path="/tmp/d.json"),
            questions_filtered_file=FileReference(local_path="/tmp/q.json"),
            databases_filtered_file=FileReference(local_path="/tmp/db.json"),
            total_records=0,
        )
        app.extract_collections = AsyncMock(return_value=fake_fetch)
        app.extract_dashboards = AsyncMock(return_value=fake_fetch)
        app.extract_questions = AsyncMock(return_value=fake_fetch)
        app.extract_databases = AsyncMock(return_value=fake_fetch)
        app.filter_data = AsyncMock(return_value=fake_filter)
        app.extract_individual_dashboards = AsyncMock(return_value=fake_fetch)
        app.extract_individual_databases = AsyncMock(return_value=fake_fetch)
        app.fetch_question_queries_activity = AsyncMock(return_value=fake_fetch)
        app.process_metabaseprocess = AsyncMock(return_value=MagicMock(total_records=0))
        app.transform_data = AsyncMock(return_value=MagicMock(record_count=0))
        type(app).run_id = property(lambda _self: "run-xyz")  # type: ignore[misc]

        residual_ref = FileReference(
            local_path=str(tmp_path / RESIDUAL_DIR), storage_path="artifacts/residual"
        )

        async def fake_upload(input):
            if input.local_path == str(tmp_path / RESIDUAL_DIR):
                return MagicMock(ref=residual_ref)
            return MagicMock(ref=MagicMock(storage_path=""))

        app.upload = AsyncMock(side_effect=fake_upload)

        out = await app.extract_metadata(metabase_input)  # type: ignore[call-arg]

        assert out.residual_failures is residual_ref
        assert residual_ref.storage_path == "artifacts/residual"
        uploaded_paths = {c.args[0].local_path for c in app.upload.await_args_list}
        assert str(tmp_path / RESIDUAL_DIR) in uploaded_paths

    @pytest.mark.asyncio
    async def test_default_output_path_used_when_none_supplied(
        self, connection, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        app = MetabaseApp()
        type(app).run_id = property(lambda _self: "run-xyz")  # type: ignore[misc]
        for name in (
            "extract_collections",
            "extract_dashboards",
            "extract_questions",
            "extract_databases",
            "filter_data",
            "extract_individual_dashboards",
            "extract_individual_databases",
            "fetch_question_queries_activity",
            "process_metabaseprocess",
            "transform_data",
        ):
            setattr(
                app,
                name,
                AsyncMock(
                    return_value=MagicMock(
                        output_file=FileReference(local_path="/tmp/x"),
                        collections_filtered_file=FileReference(local_path="/tmp/c"),
                        dashboards_filtered_file=FileReference(local_path="/tmp/d"),
                        questions_filtered_file=FileReference(local_path="/tmp/q"),
                        databases_filtered_file=FileReference(local_path="/tmp/db"),
                        total_records=0,
                        record_count=0,
                        typename="t",
                    )
                ),
            )
        app.upload = AsyncMock(return_value=MagicMock(ref=MagicMock(storage_path="")))
        inp = MetabaseInput(workflow_id="wf-1", connection=connection)
        out = await app.extract_metadata(inp)  # type: ignore[call-arg]
        # output_path defaulted under our patched tempdir.
        assert str(tmp_path) in out.output_path


# ---------------------------------------------------------------------------
# extract_lineage — feed canonical QI NDJSON through and verify records
# ---------------------------------------------------------------------------


class TestExtractLineage:
    @pytest.fixture
    def connection(self):
        return ConnectionRef.model_validate(
            {
                "attributes": {
                    "name": "metabase-test",
                    "qualified_name": "default/metabase/test",
                }
            }
        )

    @pytest.mark.asyncio
    async def test_empty_qi_output_returns_zero_counts(self, connection, tmp_path):
        app = MetabaseApp()
        type(app).run_id = property(lambda _self: "run-x")  # type: ignore[misc]
        missing_local = str(tmp_path / "nope")
        # Download a missing storage prefix returns an empty local dir.
        app.download = AsyncMock(
            return_value=MagicMock(ref=MagicMock(local_path=missing_local))
        )
        app.upload = AsyncMock(return_value=MagicMock(ref=MagicMock(storage_path="")))
        inp = MetabaseLineageInput(
            workflow_id="wf",
            connection=connection,
            connection_qualified_name="default/metabase/test",
            view_lineage_input_prefix="artifacts/missing/view-lineage",
            output_path=str(tmp_path / "out"),
        )
        out = await app.extract_lineage(inp)  # type: ignore[call-arg]
        assert out.process_count == 0
        assert out.column_process_count == 0

    @pytest.mark.asyncio
    async def test_qi_input_produces_process_and_columnprocess(
        self, connection, tmp_path
    ):
        # Drop a QI NDJSON file with one parseable record.
        qi_dir = tmp_path / "qi"
        qi_dir.mkdir()
        record = {
            "QUERY_ID": "default/metabase/test/questions/40",
            "SQL": "SELECT customer_name FROM analytics.customers",
            "QUESTION_NAME": "Top Customers",
            "PARSED_DATA": {
                "dbobjs": [
                    {
                        "name": "customers",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                        "vendor_name": "postgres",
                    }
                ],
                "relationships": [
                    {
                        "source": {
                            "column": "customer_name",
                            "table": "customers",
                            "schema": "analytics",
                            "db": "testdata",
                            "vendor_name": "postgres",
                        }
                    }
                ],
            },
        }
        (qi_dir / "out.json").write_text(json.dumps(record) + "\n")

        app = MetabaseApp()
        type(app).run_id = property(lambda _self: "run-x")  # type: ignore[misc]
        # Mock download to hand back the local qi_dir (simulating the QI
        # storage prefix already being materialised on disk).
        app.download = AsyncMock(
            return_value=MagicMock(ref=MagicMock(local_path=str(qi_dir)))
        )
        app.upload = AsyncMock(
            return_value=MagicMock(ref=MagicMock(storage_path="x/y"))
        )
        inp = MetabaseLineageInput(
            workflow_id="wf",
            connection=connection,
            connection_qualified_name="default/metabase/test",
            view_lineage_input_prefix="artifacts/wf/run-x/view-lineage",
            output_path=str(tmp_path / "out"),
        )

        out = await app.extract_lineage(inp)  # type: ignore[call-arg]
        assert out.process_count == 1
        assert out.column_process_count == 1

        # ARS 2.0 producer-split: records carrying arsIdentity must land
        # under ``lineage-stage/resolvable/`` for publish-app's Step 0
        # resolver to pick them up. Files outside ``resolvable/`` are
        # skipped by the resolver and flow through as plain entities.
        stage = tmp_path / "out" / "lineage-stage" / "resolvable"
        p_records = [
            json.loads(line)
            for line in (stage / "PROCESS" / "result-0.json").read_text().splitlines()
            if line.strip()
        ]
        cp_records = [
            json.loads(line)
            for line in (stage / "COLUMNPROCESS" / "result-0.json")
            .read_text()
            .splitlines()
            if line.strip()
        ]
        assert len(p_records) == 1
        assert len(cp_records) == 1
        assert p_records[0]["typeName"] == "Process"
        # Process input must carry an ARS 2.0 arsIdentity with
        # noMatchAction=drop so publish-app skips the edge when the
        # upstream Table isn't in the catalog (no Partial synthesis).
        first_input = p_records[0]["attributes"]["inputs"][0]
        assert first_input["attributes"]["arsIdentity"]["noMatchAction"] == "drop"
        # ColumnProcess parent must point at the Process QN.
        assert (
            cp_records[0]["attributes"]["process"]["uniqueAttributes"]["qualifiedName"]
            == p_records[0]["attributes"]["qualifiedName"]
        )


# ---------------------------------------------------------------------------
# Module-level smoke test — passthrough_modules wiring
# ---------------------------------------------------------------------------


def test_passthrough_modules_includes_lineage():
    """The lineage helper package must be in passthrough_modules so the SDK
    instrumentation skips it. The transformers package was removed in the
    asset-mapper migration."""
    pt = MetabaseApp.passthrough_modules or set()
    assert "app.lineage" in pt


def test_app_name_is_metabase():
    """workflowType derives from MetabaseApp.name in the PKL contract."""
    assert MetabaseApp.name == "metabase"
