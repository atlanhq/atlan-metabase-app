"""Unit tests for app.lineage.qi_reader.

Guards the QI output shape the connector consumes — both the current
(Gudusoft 3.0.6 + sqlglot 28.x) shape and the legacy QUERY_ID/SQL/
PARSED_DATA shape. The mismatch between these two was a silent bug for
weeks: every Process and ColumnProcess publish was zero because
``parse_qi_record`` was returning empty query_id for every record.

The sample records below are condensed from real on-tenant QI output
shared during the metabase-app debugging session.
"""

from __future__ import annotations

from app.lineage.qi_reader import (
    _coerce_column_refs,
    _coerce_table_ref,
    _unquote_ident,
    parse_qi_record,
)


CURRENT_SHAPE_RECORD = {
    "OUTPUT_FLAGS": 33558531,
    "P0_PROCESSING_TIME": 98,
    "P1_PROCESSING_TIME": 19,
    "sql": 'SELECT * FROM "PRODUCTION"."ORDERS"',
    "hash": "abc123",
    "gudusoft": {
        "dbobjs": [
            {
                "columns": [{"name": '"ORDER_ID"', "id": "5"}],
                "displayName": 'ATLANDBTQA."PRODUCTION"."ORDERS"',
                "type": "table",
                "id": "4",
                "name": '"ORDERS"',
                "database": "ATLANDBTQA",
                "schema": '"PRODUCTION"',
            }
        ],
        "relationships": [],
        "processes": [],
        "dbvendor": "dbsnowflake",
        "queryType": "sstselect",
        "simpleQueryType": "SELECT",
    },
    "gudusoftVersion": "base: 53 | gudusoft: 3.0.6.6 | sqlglot: 28.10.1",
    "vendorName": "metabase",
    "queryType": "",
    "simpleQueryType": "",
    "sourceQueryType": "MetabaseQuestion",
    "error": False,
    "extra": {
        "typeName": "MetabaseQuestion",
        "status": "ACTIVE",
        "attributes": {
            "name": "All Orders - SQL",
            "qualifiedName": "default/metabase/1779953446/questions/3",
            "metabaseQuery": 'SELECT * FROM "PRODUCTION"."ORDERS"',
        },
    },
}


LEGACY_SHAPE_RECORD = {
    "QUERY_ID": "default/metabase/1779953446/questions/3",
    "QUESTION_NAME": "All Orders - SQL",
    "SQL": 'SELECT * FROM "PRODUCTION"."ORDERS"',
    "PARSED_DATA": {
        "dbobjs": [
            {
                "name": '"ORDERS"',
                "type": "table",
                "db": "ATLANDBTQA",
                "schema": '"PRODUCTION"',
            }
        ],
        "relationships": [],
    },
}


class TestParseQiRecordCurrentShape:
    """Current QI output: sql / gudusoft / extra.attributes."""

    def test_query_id_resolves_from_extra_attributes_qualifiedname(self):
        qid, _, _, _ = parse_qi_record(CURRENT_SHAPE_RECORD)
        assert qid == "default/metabase/1779953446/questions/3"

    def test_sql_resolves_from_lowercase_sql_key(self):
        _, sql, _, _ = parse_qi_record(CURRENT_SHAPE_RECORD)
        assert sql == 'SELECT * FROM "PRODUCTION"."ORDERS"'

    def test_dbobjs_resolve_from_gudusoft_block(self):
        _, _, tables, _ = parse_qi_record(CURRENT_SHAPE_RECORD)
        assert len(tables) == 1
        assert tables[0]["database"] == "ATLANDBTQA"
        assert tables[0]["schema"] == "PRODUCTION"  # unquoted
        assert tables[0]["table_name"] == "ORDERS"  # unquoted

    def test_empty_relationships_yield_empty_source_columns(self):
        _, _, _, columns = parse_qi_record(CURRENT_SHAPE_RECORD)
        assert columns == []


class TestParseQiRecordLegacyShape:
    """Legacy QI output: QUERY_ID / SQL / PARSED_DATA. Backward compat."""

    def test_query_id_resolves_from_top_level_query_id(self):
        qid, _, _, _ = parse_qi_record(LEGACY_SHAPE_RECORD)
        assert qid == "default/metabase/1779953446/questions/3"

    def test_sql_resolves_from_uppercase_sql_key(self):
        _, sql, _, _ = parse_qi_record(LEGACY_SHAPE_RECORD)
        assert sql == 'SELECT * FROM "PRODUCTION"."ORDERS"'

    def test_dbobjs_resolve_from_parsed_data_block(self):
        _, _, tables, _ = parse_qi_record(LEGACY_SHAPE_RECORD)
        assert len(tables) == 1
        assert tables[0]["database"] == "ATLANDBTQA"
        assert tables[0]["schema"] == "PRODUCTION"  # unquoted
        assert tables[0]["table_name"] == "ORDERS"  # unquoted


class TestUnquoteIdent:
    def test_strips_double_quotes(self):
        assert _unquote_ident('"PRODUCTION"') == "PRODUCTION"

    def test_strips_backticks(self):
        assert _unquote_ident("`orders`") == "orders"

    def test_leaves_bare_identifier_untouched(self):
        assert _unquote_ident("PUBLIC") == "PUBLIC"

    def test_handles_empty_and_short(self):
        assert _unquote_ident("") == ""
        assert _unquote_ident('"') == '"'  # too short to be a wrapped ident


class TestCoerceTableRef:
    def test_returns_none_for_missing_name(self):
        assert _coerce_table_ref({"type": "table", "database": "x"}) is None

    def test_returns_none_for_non_table_type(self):
        assert _coerce_table_ref({"name": "cte_alias", "type": "subquery"}) is None

    def test_propagates_default_vendor_when_none_on_record(self):
        ref = _coerce_table_ref(
            {"name": "ORDERS", "type": "table", "database": "DB", "schema": "S"},
            default_vendor="snowflake",
        )
        assert ref is not None
        assert ref["vendor_name"] == "snowflake"


class TestCoerceColumnRefs:
    def test_extracts_distinct_source_columns(self):
        relationships = [
            {
                "source": {
                    "column": '"ORDER_ID"',
                    "table": '"ORDERS"',
                    "schema": '"PRODUCTION"',
                    "db": "ATLANDBTQA",
                    "vendor_name": "snowflake",
                }
            },
            {
                "source": {
                    "column": '"ORDER_ID"',
                    "table": '"ORDERS"',
                    "schema": '"PRODUCTION"',
                    "db": "ATLANDBTQA",
                    "vendor_name": "snowflake",
                }
            },  # duplicate — should be deduplicated
            {
                "source": {
                    "column": "CUSTOMER_ID",
                    "table": "ORDERS",
                    "schema": "PRODUCTION",
                    "db": "ATLANDBTQA",
                    "vendor_name": "snowflake",
                }
            },
        ]
        refs = _coerce_column_refs(relationships)
        assert len(refs) == 2
        assert {r["column_name"] for r in refs} == {"ORDER_ID", "CUSTOMER_ID"}
        # All identifiers stripped of quotes.
        for r in refs:
            assert r["schema"] == "PRODUCTION"
            assert r["table_name"] == "ORDERS"
