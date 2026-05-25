"""Unit tests for app/connector.py.

Covers:
- Module-level helpers (build_credential_ref, _parse_credential_dict,
  _default_output_path, _raw_file, _processed_file, _ref).
- _build_client credential routing (CredentialRef vs inline; error path).
- @task method bodies — each one mocks the API client and asserts the
  JSONL output is written + FileReference is returned correctly.
- extract_metadata @entrypoint orchestration — mocks every @task and
  asserts the call sequence + returned MetabaseOutput path fields.
- extract_lineage @entrypoint — feeds canonical QI NDJSON through the
  pipeline and verifies Process/ColumnProcess records hit lineage-stage/.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef

from app.connector import (
    MetabaseApp,
    _default_output_path,
    _parse_credential_dict,
    _processed_file,
    _raw_file,
    _ref,
    build_credential_ref,
)
from app.contracts import (
    FetchInput,
    FilterInput,
    MetabaseCredential,
    MetabaseInput,
    MetabaseLineageInput,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestBuildCredentialRef:
    def test_metabase_credential_ref_takes_precedence(self):
        ref = CredentialRef(name="x", credential_type="basic", credential_guid="g")
        inp = MetabaseInput(metabase_credential=ref)
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is ref
        assert inline == {}

    def test_credential_guid_creates_ref(self):
        inp = MetabaseInput(credential_guid="guid-123")
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is not None
        assert out_ref.name == "guid-123"
        assert out_ref.credential_type == "basic"
        assert inline == {}

    def test_credentials_list_flattens_to_inline(self):
        inp = MetabaseInput(
            credentials=[
                {"key": "host", "value": "http://localhost"},
                {"key": "port", "value": "3000"},
                {"key": "username", "value": "u"},
                {"key": "password", "value": "p"},
            ]
        )
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is None
        assert inline == {
            "host": "http://localhost",
            "port": "3000",
            "username": "u",
            "password": "p",
        }

    def test_credentials_dict_passes_through(self):
        inp = MetabaseInput(credentials={"host": "h", "port": 3000})
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is None
        assert inline == {"host": "h", "port": 3000}

    def test_no_credentials_returns_empty_inline(self):
        out_ref, inline = build_credential_ref(MetabaseInput())
        assert out_ref is None
        assert inline == {}


class TestParseCredentialDict:
    def test_empty_raw_returns_default_credential(self):
        cred = _parse_credential_dict({})
        assert isinstance(cred, MetabaseCredential)
        assert cred.host == ""
        assert cred.port == 443

    def test_flat_shape(self):
        cred = _parse_credential_dict(
            {"host": "http://x", "port": 3000, "username": "u", "password": "p"}
        )
        assert cred.host == "http://x"
        assert cred.port == 3000
        assert cred.username == "u"
        assert cred.password == "p"

    def test_nested_extra_shape(self):
        cred = _parse_credential_dict(
            {"host": "h", "extra": {"username": "u", "password": "p"}}
        )
        assert cred.username == "u"
        assert cred.password == "p"

    def test_none_port_falls_back_to_443(self):
        cred = _parse_credential_dict({"host": "h", "port": None})
        assert cred.port == 443


class TestPathHelpers:
    def test_default_output_path_uses_tempdir_with_workflow_id(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        result = _default_output_path("wf-abc")
        assert result == str(tmp_path / "atlan-metabase-app" / "wf-abc")
        assert Path(result).exists()

    def test_default_output_path_no_workflow_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        result = _default_output_path("")
        assert result == str(tmp_path / "atlan-metabase-app")

    def test_raw_file_path_layout(self):
        assert _raw_file("/tmp/out", "collections") == (
            "/tmp/out/raw/collections/result-0.json"
        )

    def test_processed_file_path_layout(self):
        assert _processed_file("/tmp/out", "questions") == (
            "/tmp/out/processed/questions/result-0.json"
        )

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
        with pytest.raises(ValueError, match="no credential_ref or inline_credentials"):
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
        app.upload = AsyncMock(return_value=MagicMock(ref=MagicMock(storage_path="")))
        inp = MetabaseLineageInput(
            workflow_id="wf",
            connection=connection,
            connection_qualified_name="default/metabase/test",
            view_lineage_input_prefix=str(tmp_path / "nope"),
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
        app.upload = AsyncMock(
            return_value=MagicMock(ref=MagicMock(storage_path="x/y"))
        )
        inp = MetabaseLineageInput(
            workflow_id="wf",
            connection=connection,
            connection_qualified_name="default/metabase/test",
            view_lineage_input_prefix=str(qi_dir),
            output_path=str(tmp_path / "out"),
        )

        out = await app.extract_lineage(inp)  # type: ignore[call-arg]
        assert out.process_count == 1
        assert out.column_process_count == 1

        # NDJSON files written under output_path/lineage-stage/{PROCESS,COLUMNPROCESS}/
        stage = tmp_path / "out" / "lineage-stage"
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
        # Process input must be a PARTIAL_OBJECT Table ref.
        first_input = p_records[0]["attributes"]["inputs"][0]
        assert (
            first_input["attributes"]["arsEntityConfig"][
                "publishTransformationHandling"
            ]
            == "PARTIAL_OBJECT"
        )
        # ColumnProcess parent must point at the Process QN.
        assert (
            cp_records[0]["attributes"]["process"]["uniqueAttributes"]["qualifiedName"]
            == p_records[0]["attributes"]["qualifiedName"]
        )


# ---------------------------------------------------------------------------
# Module-level smoke test — passthrough_modules wiring
# ---------------------------------------------------------------------------


def test_passthrough_modules_includes_transformers_and_lineage():
    """Both helper packages must be in passthrough_modules so the SDK
    instrumentation skips them."""
    pt = MetabaseApp.passthrough_modules or set()
    assert "app.transformers" in pt
    assert "app.lineage" in pt


def test_app_name_is_metabase():
    """workflowType derives from MetabaseApp.name in the PKL contract."""
    assert MetabaseApp.name == "metabase"
