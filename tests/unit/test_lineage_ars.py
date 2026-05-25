"""Unit tests for app/lineage/ars_builder.py + app/lineage/qi_reader.py.

Feeds canonical QueryIntelligence-shaped NDJSON records into the reader,
runs them through the ARS builder, and asserts the resulting
Process / ColumnProcess records carry the right PARTIAL_OBJECT /
PARTIAL_FIELD configs for cross-connector resolution by the publish app.
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
# PARTIAL_OBJECT / PARTIAL_FIELD shape
# ---------------------------------------------------------------------------


class TestPartialTableRef:
    def test_includes_partial_object_handling(self):
        ref = build_partial_table_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
        )
        cfg = ref["attributes"]["arsEntityConfig"]
        assert cfg["publishTransformationHandling"] == "PARTIAL_OBJECT"
        assert cfg["isRelationship"] is True
        assert cfg["skipLookup"] is False
        assert cfg["isTableViewAgnostic"] is True

    def test_identity_format(self):
        ref = build_partial_table_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
        )
        ars = ref["attributes"]["arsAttributes"]
        # connectorName|connectionName|databaseName|schemaName|tableName
        # connectionName is wildcard (ARS resolves any matching connection).
        assert ars["identity"] == "postgres|*|testdata|analytics|customers"
        assert ars["identityPattern"] == (
            "connectorName|connectionName|databaseName|schemaName|tableName"
        )

    def test_wildcards_for_missing_components(self):
        ref = build_partial_table_ref(
            vendor_name="",
            database="",
            schema="",
            table_name="orders",
        )
        ars = ref["attributes"]["arsAttributes"]
        assert ars["identity"] == "*|*|*|*|orders"

    def test_fallback_qn_built_from_known_parts(self):
        ref = build_partial_table_ref(
            vendor_name="postgres",
            database="db1",
            schema="sch1",
            table_name="t1",
        )
        assert (
            ref["attributes"]["arsAttributes"]["fallbackQualifiedName"] == "db1/sch1/t1"
        )
        assert ref["attributes"]["arsAttributes"]["fallbackTypeName"] == "Table"


class TestPartialColumnRef:
    def test_includes_partial_field_handling(self):
        ref = build_partial_column_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
            column_name="customer_name",
        )
        cfg = ref["attributes"]["arsEntityConfig"]
        parent_cfg = ref["attributes"]["arsParentEntityConfig"]
        assert cfg["publishTransformationHandling"] == "PARTIAL_FIELD"
        assert parent_cfg["publishTransformationHandling"] == "PARTIAL_OBJECT"
        assert parent_cfg["isTableViewAgnostic"] is True

    def test_identity_includes_column(self):
        ref = build_partial_column_ref(
            vendor_name="postgres",
            database="testdata",
            schema="analytics",
            table_name="customers",
            column_name="customer_name",
        )
        ars = ref["attributes"]["arsAttributes"]
        assert (
            ars["identity"] == "postgres|*|testdata|analytics|customers|customer_name"
        )


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

    def test_inputs_are_partial_objects(self):
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
        assert (
            inputs[0]["attributes"]["arsEntityConfig"]["publishTransformationHandling"]
            == "PARTIAL_OBJECT"
        )

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

    def test_lineage_asset_config_on_process(self):
        p = build_process(
            connection_qualified_name=_CONN_QN,
            connection_name=_CONN_NAME,
            question_id=_QID,
            question_name=_QNAME,
            sql=_SQL,
            source_tables=[_table("testdata", "analytics", "customers")],
        )
        assert p is not None
        cfg = p["attributes"]["arsEntityConfig"]
        assert cfg["publishTransformationHandling"] == "LINEAGE_ASSET"
        assert cfg["skipLookup"] is True


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

    def test_inputs_are_partial_fields(self):
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
        assert (
            inputs[0]["attributes"]["arsEntityConfig"]["publishTransformationHandling"]
            == "PARTIAL_FIELD"
        )


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
