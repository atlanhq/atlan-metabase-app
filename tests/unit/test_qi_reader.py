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
    _build_dbobj_index,
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


class TestCoerceColumnRefsIdBased:
    """Current Gudusoft shape — relationships use ``sources`` (plural)
    with ``parentId`` referencing the parent table in ``dbobjs``.

    Mirrors QI's ``to_gudusoft_output`` (atlan-query-intelligence-app
    app/pond/lorien/lineage/lineage.py:564–706). Pre-fix, this shape
    fell through ``_coerce_column_refs`` silently — the reader only
    looked for ``source``/``from`` (singular) keys — and every
    ColumnProcess publish was zero.
    """

    _DBOBJS = [
        {
            "type": "table",
            "id": 1,
            "name": "ORDERS",
            "database": "ANALYTICS",
            "schema": "DBT_ANALYTICS",
            "columns": [
                {"name": "ORDER_ID", "id": 2},
                {"name": "CUSTOMER_ID", "id": 3},
            ],
        },
        {
            "type": "table",
            "id": 4,
            "name": "CUSTOMERS",
            "database": "ANALYTICS",
            "schema": "DBT_ANALYTICS",
            "columns": [{"name": "CUSTOMER_ID", "id": 5}],
        },
    ]

    def test_resolves_parentid_against_dbobjs(self):
        relationships = [
            {
                "id": 100,
                "type": "fdd",
                "sources": [
                    {
                        "id": 2,
                        "column": "ORDER_ID",
                        "parentId": 1,
                        "parentName": "ANALYTICS.DBT_ANALYTICS.ORDERS",
                    }
                ],
                "target": {
                    "id": 99,
                    "column": "order_id",
                    "parentId": 0,
                    "parentName": "MetabaseQuestion",
                },
            },
        ]
        refs = _coerce_column_refs(
            relationships, dbobj_index=_build_dbobj_index(self._DBOBJS)
        )
        assert len(refs) == 1
        ref = refs[0]
        assert ref["column_name"] == "ORDER_ID"
        assert ref["table_name"] == "ORDERS"
        assert ref["schema"] == "DBT_ANALYTICS"
        assert ref["database"] == "ANALYTICS"

    def test_multiple_sources_dedup_across_relationships(self):
        # Real QI output has one relationship per output column —
        # the same source column can appear multiple times across
        # relationships and must be deduplicated.
        relationships = [
            {
                "type": "fdd",
                "sources": [
                    {"id": 2, "column": "ORDER_ID", "parentId": 1},
                    {"id": 5, "column": "CUSTOMER_ID", "parentId": 4},
                ],
            },
            {
                "type": "fdd",
                "sources": [
                    {"id": 2, "column": "ORDER_ID", "parentId": 1},  # dup
                    {"id": 3, "column": "CUSTOMER_ID", "parentId": 1},  # diff parent
                ],
            },
        ]
        refs = _coerce_column_refs(
            relationships, dbobj_index=_build_dbobj_index(self._DBOBJS)
        )
        assert len(refs) == 3
        names_and_tables = {(r["column_name"], r["table_name"]) for r in refs}
        assert names_and_tables == {
            ("ORDER_ID", "ORDERS"),
            ("CUSTOMER_ID", "ORDERS"),
            ("CUSTOMER_ID", "CUSTOMERS"),
        }

    def test_falls_back_to_parentname_when_parentid_unknown(self):
        # If the dbobj_index doesn't have the parentId (partial QI output
        # drop), split parentName as a best-effort fallback.
        relationships = [
            {
                "type": "fdd",
                "sources": [
                    {
                        "id": 2,
                        "column": '"order_id"',
                        "parentId": 999,
                        "parentName": "RAW.PUBLIC.ORDERS",
                    },
                ],
            },
        ]
        refs = _coerce_column_refs(
            relationships, dbobj_index=_build_dbobj_index(self._DBOBJS)
        )
        assert len(refs) == 1
        assert refs[0]["column_name"] == "order_id"  # unquoted
        assert refs[0]["database"] == "RAW"
        assert refs[0]["schema"] == "PUBLIC"
        assert refs[0]["table_name"] == "ORDERS"

    def test_id_based_shape_takes_precedence_over_legacy_when_both_present(self):
        # If a record has both ``sources`` (plural, new) and
        # ``source`` (singular, legacy), the new shape wins — otherwise
        # we'd double-count.
        relationships = [
            {
                "type": "fdd",
                "sources": [{"id": 2, "column": "ORDER_ID", "parentId": 1}],
                "source": {  # legacy — must be ignored when sources is present
                    "column": "SHOULD_NOT_APPEAR",
                    "table": "x",
                    "schema": "x",
                    "db": "x",
                },
            },
        ]
        refs = _coerce_column_refs(
            relationships, dbobj_index=_build_dbobj_index(self._DBOBJS)
        )
        assert {r["column_name"] for r in refs} == {"ORDER_ID"}

    def test_parse_qi_record_end_to_end_populates_columns(self):
        # Regression guard for the original bug — a full QI record
        # (current shape) flowing through parse_qi_record must yield a
        # non-empty source_columns list. Pre-fix this list was always
        # empty and every ColumnProcess publish was zero.
        record = {
            "sql": 'SELECT "ORDER_ID" FROM "ANALYTICS"."DBT_ANALYTICS"."ORDERS"',
            "gudusoft": {
                "dbobjs": self._DBOBJS,
                "relationships": [
                    {
                        "type": "fdd",
                        "sources": [
                            {"id": 2, "column": "ORDER_ID", "parentId": 1},
                        ],
                    },
                ],
            },
            "extra": {
                "attributes": {
                    "qualifiedName": "default/metabase/1/questions/7",
                    "name": "Order IDs",
                },
            },
        }
        _, _, _, columns = parse_qi_record(record)
        assert len(columns) == 1
        assert columns[0]["column_name"] == "ORDER_ID"
        assert columns[0]["table_name"] == "ORDERS"
