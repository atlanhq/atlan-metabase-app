"""Unit tests for app/lineage/ars_builder.py + app/lineage/qi_reader.py.

Feeds canonical QueryIntelligence-shaped NDJSON records into the reader,
runs them through the ARS builder, and asserts the resulting
Process / ColumnProcess records carry the right ARS 2.0 ``arsIdentity``
blocks for cross-connector resolution by the publish app.

ARS 2.0 schema is defined in atlan-publish-app
``app/lib/partitioning/duckdb_sql.py::ARS_IDENTITY_STRUCT_SCHEMA``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lineage.ars_builder import (
    build_column_process,
    build_partial_column_ref,
    build_partial_table_ref,
    build_process,
    process_hash,
)
from app.lineage.qi_reader import iter_qi_records, parse_qi_record

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CONN_QN = "default/metabase/e2e"
_CONN_NAME = "metabase-e2e"
_QID = 40
_QNAME = "Top Customers by Order Value"
_SQL = (
    "SELECT c.customer_name, SUM(o.order_total) AS total_value "
    "FROM analytics.customers c "
    "JOIN analytics.orders o ON o.customer_id = c.customer_id "
    "GROUP BY c.customer_name "
    "ORDER BY total_value DESC"
)


def _table(database: str, schema: str, name: str, vendor: str = "postgres"):
    return {
        "vendor_name": vendor,
        "database": database,
        "schema": schema,
        "table_name": name,
    }


def _column(
    database: str, schema: str, table: str, column: str, vendor: str = "postgres"
):
    return {
        "vendor_name": vendor,
        "database": database,
        "schema": schema,
        "table_name": table,
        "column_name": column,
    }


# ---------------------------------------------------------------------------
# ARS 2.0 nested-ref shape (Table / Column refs in inputs[])
# ---------------------------------------------------------------------------


class TestPartialTableRef:
    def test_carries_ars_2_0_identity(self):
        ref = build_partial_table_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
        )
        ai = ref["attributes"]["arsIdentity"]
        # Components are a MAP(VARCHAR, VARCHAR) — only populated keys.
        assert ai["components"] == {
            "connectorType": "postgres",
            "databaseName": "testdata",
            "schemaName": "analytics",
            "tableName": "customers",
        }
        # noMatchAction = create_partial → resolver synthesizes a PartialObject
        # when the lookup misses.
        assert ai["noMatchAction"] == "create_partial"
        assert ai["lookupResultHandling"] == "pick_first"
        # Resolver matches against Table OR View — Metabase sources can
        # be either, and the source connector decides which.
        assert ai["matchTypeNames"] == ["Table", "View"]

    def test_no_legacy_ars_1_0_keys(self):
        # ARS 1.0 keys are not emitted — those went through legacy_translator
        # before reaching the resolver, which is unwired in our contract.
        # Emitting them now would be misleading dead weight.
        ref = build_partial_table_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
        )
        attrs = ref["attributes"]
        assert "arsEntityConfig" not in attrs
        assert "arsAttributes" not in attrs

    def test_drops_empty_component_keys(self):
        # The ARS resolver filters empty component values at JOIN time
        # (see legacy_translator._legacy_pipe_components_json). Emit only
        # populated keys so the resolver matches what it expects.
        ref = build_partial_table_ref(
            vendor_name="",
            database="",
            schema="",
            table_name="orders",
        )
        ai = ref["attributes"]["arsIdentity"]
        assert ai["components"] == {"tableName": "orders"}

    def test_fallback_qn_from_known_parts(self):
        ref = build_partial_table_ref(
            vendor_name="postgres",
            database="db1",
            schema="sch1",
            table_name="t1",
        )
        ai = ref["attributes"]["arsIdentity"]
        assert ai["fallbackQualifiedName"] == "db1/sch1/t1"
        assert ai["fallbackTypeName"] == "Table"


class TestPartialColumnRef:
    def test_carries_ars_2_0_identity_with_column(self):
        ref = build_partial_column_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
            column_name="customer_name",
        )
        ai = ref["attributes"]["arsIdentity"]
        assert ai["components"] == {
            "connectorType": "postgres",
            "databaseName": "testdata",
            "schemaName": "analytics",
            "tableName": "customers",
            "columnName": "customer_name",
        }
        assert ai["matchTypeNames"] == ["Column"]
        assert ai["noMatchAction"] == "create_partial"

    def test_carries_parent_table_context(self):
        # ARS 2.0 PartialField synthesis needs the parent Table identity
        # so the new Column has a parent ref. parentComponentsKeys selects
        # which components map to the parent's identity subset.
        ref = build_partial_column_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
            column_name="customer_name",
        )
        ai = ref["attributes"]["arsIdentity"]
        assert ai["parentComponentsKeys"] == [
            "connectorType",
            "databaseName",
            "schemaName",
            "tableName",
        ]
        assert ai["parentMatchTypeNames"] == ["Table", "View"]
        assert ai["parentTypeNames"] == ["Table"]

    def test_no_legacy_ars_1_0_keys(self):
        ref = build_partial_column_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
            column_name="customer_name",
        )
        attrs = ref["attributes"]
        assert "arsEntityConfig" not in attrs
        assert "arsAttributes" not in attrs
        assert "arsParentEntityConfig" not in attrs
        assert "arsParentAttributes" not in attrs


# ---------------------------------------------------------------------------
# Process record assembly
# ---------------------------------------------------------------------------


class TestBuildProcess:
    def test_returns_none_when_no_source_tables(self):
        result = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=[],
        )
        assert result is None

    def test_basic_process_record(self):
        tables = [
            _table("testdata", "analytics", "customers"),
            _table("testdata", "analytics", "orders"),
        ]
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=tables,
        )
        assert p is not None
        assert p["typeName"] == "Process"
        assert p["status"] == "ACTIVE"
        attrs = p["attributes"]
        assert attrs["connectorName"] == "metabase"
        assert attrs["connectionName"] == _CONN_NAME
        assert attrs["connectionQualifiedName"] == _CONN_QN
        # QN ends with /question_tables/<id>/<hash>
        assert attrs["qualifiedName"].startswith(f"{_CONN_QN}/question_tables/{_QID}/")

    def test_inputs_carry_ars_2_0_identity(self):
        tables = [_table("testdata", "analytics", "customers")]
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=tables,
        )
        assert p is not None
        inputs = p["attributes"]["inputs"]
        assert len(inputs) == 1
        # Each input ref must have an arsIdentity block for cross-connector
        # resolution by publish-app's edge resolver.
        ai = inputs[0]["attributes"]["arsIdentity"]
        assert ai["noMatchAction"] == "create_partial"
        assert ai["components"]["tableName"] == "customers"

    def test_output_is_metabase_question(self):
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=[_table("testdata", "analytics", "customers")],
        )
        assert p is not None
        outputs = p["attributes"]["outputs"]
        assert len(outputs) == 1
        assert outputs[0]["typeName"] == "MetabaseQuestion"
        assert outputs[0]["uniqueAttributes"]["qualifiedName"] == (
            f"{_CONN_QN}/questions/{_QID}"
        )

    def test_process_uses_use_fallback_action(self):
        # The Process itself isn't looked up — this connector owns its
        # qualifiedName. noMatchAction = use_fallback short-circuits the
        # ARS lookup and uses the fallback QN directly.
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=[_table("testdata", "analytics", "customers")],
        )
        assert p is not None
        ai = p["attributes"]["arsIdentity"]
        assert ai["noMatchAction"] == "use_fallback"
        assert ai["fallbackTypeName"] == "Process"
        # fallbackQualifiedName is the same as the entity's own QN.
        assert ai["fallbackQualifiedName"] == p["attributes"]["qualifiedName"]

    def test_ars_nested_lookup_fields_lists_inputs_outputs(self):
        # Tells the resolver which fields contain nested refs that need
        # per-edge ARS resolution. Without this, the resolver won't UNNEST
        # inputs[]/outputs[] and the cross-connector refs never get
        # processed (the original symptom of the publish failure).
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=[_table("testdata", "analytics", "customers")],
        )
        assert p is not None
        assert p["attributes"]["arsNestedLookupFields"] == ["inputs", "outputs"]

    def test_relationship_attributes_mirrors_attributes(self):
        # Guards the structural shape that publish-app + Atlas require.
        # Pre-fix, the record had inputs/outputs only under ``attributes``
        # and Atlas rejected every Process with ATLAS-400-00-021
        # (INVALID_OBJECT_ID) because the constructed relationship-side
        # payload had no valid endpoints. ``relationshipAttributes`` must
        # exist at the top level and mirror the ``attributes`` content.
        tables = [_table("testdata", "analytics", "orders")]
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=tables,
        )
        assert p is not None
        assert "relationshipAttributes" in p, (
            "Process record must carry a top-level relationshipAttributes "
            "key — Atlas reads relationship-side ObjectIds from there"
        )
        rel = p["relationshipAttributes"]
        assert rel["inputs"] == p["attributes"]["inputs"]
        assert rel["outputs"] == p["attributes"]["outputs"]


# ---------------------------------------------------------------------------
# ColumnProcess record assembly + Process linkage
# ---------------------------------------------------------------------------


class TestBuildColumnProcess:
    def test_returns_none_when_no_source_columns(self):
        result = build_column_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_columns=[],
            parent_process_hash="abc",
        )
        assert result is None

    def test_parent_process_qn_matches(self):
        h = process_hash(_QID, _SQL)
        cp = build_column_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_columns=[
                _column("testdata", "analytics", "customers", "customer_name")
            ],
            parent_process_hash=h,
        )
        assert cp is not None
        parent_qn = cp["attributes"]["process"]["uniqueAttributes"]["qualifiedName"]
        # Must match what build_process() would have built with the same h
        assert parent_qn == f"{_CONN_QN}/question_tables/{_QID}/{h}"

    def test_inputs_carry_ars_2_0_identity_with_column(self):
        h = process_hash(_QID, _SQL)
        cp = build_column_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_columns=[
                _column("testdata", "analytics", "customers", "customer_name")
            ],
            parent_process_hash=h,
        )
        assert cp is not None
        inputs = cp["attributes"]["inputs"]
        assert len(inputs) == 1
        ai = inputs[0]["attributes"]["arsIdentity"]
        assert ai["components"]["columnName"] == "customer_name"
        assert ai["noMatchAction"] == "create_partial"
        # Parent table context for PartialField synthesis.
        assert "tableName" in ai["parentComponentsKeys"]

    def test_relationship_attributes_mirrors_attributes(self):
        # Companion to TestBuildProcess.test_relationship_attributes_mirrors_attributes
        # — same structural requirement applies to ColumnProcess.
        h = process_hash(_QID, _SQL)
        cp = build_column_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_columns=[
                _column("testdata", "analytics", "customers", "customer_name")
            ],
            parent_process_hash=h,
        )
        assert cp is not None
        assert "relationshipAttributes" in cp
        rel = cp["relationshipAttributes"]
        assert rel["inputs"] == cp["attributes"]["inputs"]
        assert rel["outputs"] == cp["attributes"]["outputs"]
        assert rel["process"] == cp["attributes"]["process"]


# ---------------------------------------------------------------------------
# QI reader — NDJSON → normalised refs
# ---------------------------------------------------------------------------


def _write_qi_ndjson(tmp_path: Path, records: list[dict]) -> Path:
    f = tmp_path / "qi-output.json"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return f


class TestQiReader:
    def test_iter_handles_missing_path(self, tmp_path):
        records = list(iter_qi_records(tmp_path / "nope"))
        assert records == []

    def test_iter_reads_ndjson_file(self, tmp_path):
        f = _write_qi_ndjson(
            tmp_path,
            [
                {"QUERY_ID": "q1", "SQL": "SELECT 1", "PARSED_DATA": {"dbobjs": []}},
                {"QUERY_ID": "q2", "SQL": "SELECT 2", "PARSED_DATA": {"dbobjs": []}},
            ],
        )
        out = list(iter_qi_records(f))
        assert len(out) == 2
        assert out[0]["QUERY_ID"] == "q1"

    def test_iter_skips_malformed_lines(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text('{"QUERY_ID": "q1"}\nnot-json\n{"QUERY_ID": "q2"}\n')
        out = list(iter_qi_records(f))
        assert [r["QUERY_ID"] for r in out] == ["q1", "q2"]

    def test_parse_extracts_table_refs(self):
        record = {
            "QUERY_ID": "default/metabase/e2e/questions/40",
            "SQL": _SQL,
            "PARSED_DATA": {
                "dbobjs": [
                    {
                        "name": "customers",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                        "vendor_name": "postgres",
                    },
                    {
                        "name": "orders",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                        "vendor_name": "postgres",
                    },
                ],
                "relationships": [],
            },
        }
        qid, sql, tables, columns = parse_qi_record(record)
        assert qid == "default/metabase/e2e/questions/40"
        assert sql == _SQL
        assert len(tables) == 2
        assert {t["table_name"] for t in tables} == {"customers", "orders"}
        assert columns == []

    def test_parse_skips_ctes_and_subqueries(self):
        record = {
            "QUERY_ID": "q1",
            "SQL": "WITH x AS (...) SELECT * FROM analytics.customers",
            "PARSED_DATA": {
                "dbobjs": [
                    {"name": "x", "type": "cte"},
                    {
                        "name": "customers",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                    },
                ]
            },
        }
        _, _, tables, _ = parse_qi_record(record)
        names = {t["table_name"] for t in tables}
        assert names == {"customers"}

    def test_parse_extracts_column_relationships(self):
        record = {
            "QUERY_ID": "q1",
            "SQL": _SQL,
            "PARSED_DATA": {
                "dbobjs": [],
                "relationships": [
                    {
                        "source": {
                            "column": "customer_name",
                            "table": "customers",
                            "schema": "analytics",
                            "db": "testdata",
                            "vendor_name": "postgres",
                        },
                        "target": {"column": "customer_name"},
                    },
                    {
                        "source": {
                            "column": "order_total",
                            "table": "orders",
                            "schema": "analytics",
                            "db": "testdata",
                            "vendor_name": "postgres",
                        },
                        "target": {"column": "total_value"},
                    },
                ],
            },
        }
        _, _, _, columns = parse_qi_record(record)
        assert len(columns) == 2
        cols = {c["column_name"] for c in columns}
        assert cols == {"customer_name", "order_total"}


# ---------------------------------------------------------------------------
# End-to-end — QI NDJSON → ARS Process + ColumnProcess records
# ---------------------------------------------------------------------------


class TestQiToArsIntegration:
    """Feeds a realistic QI record through the full pipeline."""

    @pytest.fixture
    def qi_record(self):
        return {
            "QUERY_ID": f"{_CONN_QN}/questions/{_QID}",
            "SQL": _SQL,
            "QUESTION_NAME": _QNAME,
            "PARSED_DATA": {
                "dbobjs": [
                    {
                        "name": "customers",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                        "vendor_name": "postgres",
                    },
                    {
                        "name": "orders",
                        "db": "testdata",
                        "schema": "analytics",
                        "type": "table",
                        "vendor_name": "postgres",
                    },
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

    def test_full_pipeline(self, qi_record):
        qid, sql, tables, columns = parse_qi_record(qi_record)
        question_id = qid.rsplit("/", 1)[-1]
        h = process_hash(question_id, sql)

        process = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=question_id,
            question_name=qi_record["QUESTION_NAME"],
            sql=sql,
            source_tables=tables,
        )
        assert process is not None
        # Both source tables should be PARTIAL_OBJECT inputs.
        assert len(process["attributes"]["inputs"]) == 2

        cp = build_column_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=question_id,
            question_name=qi_record["QUESTION_NAME"],
            sql=sql,
            source_columns=columns,
            parent_process_hash=h,
        )
        assert cp is not None
        # ColumnProcess parent must point to the same Process QN.
        assert (
            cp["attributes"]["process"]["uniqueAttributes"]["qualifiedName"]
            == (process["attributes"]["qualifiedName"])
        )
