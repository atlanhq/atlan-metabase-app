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

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from app.lineage.qi_reader import (
    _build_dbobj_index,
    _coerce_column_refs,
    _coerce_one_id_based_source,
    _coerce_one_inline_source,
    _coerce_table_ref,
    _question_name,
    _question_qn,
    _unquote_ident,
    iter_qi_records,
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

    def test_dbvendor_propagates_as_vendor_name(self):
        # REGRESSION GUARD for the second half of the staging-diff bug.
        # publish-app's ``build_partial_qualified_name`` returns an empty
        # QN when ``connectorType`` is missing from the components map,
        # which leaves the resolved PartialObject with
        # ``qualifiedName: null`` (atlan-publish-app
        # app/lib/partitioning/resolve/macros.py:106-128).
        # ``dbvendor: "dbsnowflake"`` must propagate as
        # ``vendor_name: "snowflake"`` on every Table ref so the
        # downstream ``arsIdentity.components.connectorType`` is set.
        _, _, tables, _ = parse_qi_record(CURRENT_SHAPE_RECORD)
        assert tables[0]["vendor_name"] == "snowflake"

    def test_dbvendor_strips_db_prefix_lowercases(self):
        # Gudusoft emits `dbBigQuery` / `dbMSSQL` etc. — the publish-app
        # component key is the lowercase suffix.
        rec = {
            **CURRENT_SHAPE_RECORD,
            "gudusoft": {**CURRENT_SHAPE_RECORD["gudusoft"], "dbvendor": "dbBigQuery"},
        }
        _, _, tables, _ = parse_qi_record(rec)
        assert tables[0]["vendor_name"] == "bigquery"

    def test_default_vendor_used_when_dbvendor_missing(self):
        # When QI didn't determine vendor (rare, but possible for
        # generic-SQL fall-back), the caller's ``default_vendor`` is
        # honoured.
        rec = {
            **CURRENT_SHAPE_RECORD,
            "gudusoft": {
                k: v
                for k, v in CURRENT_SHAPE_RECORD["gudusoft"].items()
                if k != "dbvendor"
            },
        }
        _, _, tables, _ = parse_qi_record(rec, default_vendor="redshift")
        assert tables[0]["vendor_name"] == "redshift"


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


class TestUnquoteIdentBoundaries:
    """Boundary and mismatched-quote contracts for _unquote_ident."""

    def test_two_char_quoted_pair_is_stripped_to_empty(self):
        # len == 2 is the boundary: a bare quote-pair IS a wrapped (empty)
        # identifier and must be stripped, not returned verbatim.
        assert _unquote_ident('""') == ""
        assert _unquote_ident("``") == ""

    def test_three_char_quoted_ident_is_stripped(self):
        assert _unquote_ident('"a"') == "a"

    def test_mismatched_double_quote_left_untouched(self):
        # Only a *matched* pair is stripped — a leading or trailing quote
        # alone is part of the identifier.
        assert _unquote_ident('"abc') == '"abc'
        assert _unquote_ident('abc"') == 'abc"'

    def test_mismatched_backtick_left_untouched(self):
        assert _unquote_ident("`abc") == "`abc"
        assert _unquote_ident("abc`") == "abc`"

    def test_mixed_quote_styles_left_untouched(self):
        assert _unquote_ident('"abc`') == '"abc`'
        assert _unquote_ident('`abc"') == '`abc"'


class TestCoerceTableRefFieldFallbacks:
    """Exact key-fallback and default contracts for _coerce_table_ref."""

    def test_tablename_key_is_the_name_fallback(self):
        ref = _coerce_table_ref({"tableName": "T1", "type": "table"})
        assert ref is not None
        assert ref["table_name"] == "T1"

    def test_missing_type_is_treated_as_table_and_defaults_are_empty(self):
        # No ``type``/``objectType`` at all → the ref is kept, and every
        # unset optional field defaults to the empty string; the built-in
        # default_vendor is "" (not any placeholder).
        ref = _coerce_table_ref({"name": "T"})
        assert ref == {
            "vendor_name": "",
            "database": "",
            "schema": "",
            "table_name": "T",
        }

    def test_objecttype_key_is_the_type_fallback_for_skipping(self):
        # ``objectType`` (no ``type``) must still classify the entry —
        # a subquery is skipped, not coerced.
        assert _coerce_table_ref({"name": "x", "objectType": "subquery"}) is None

    def test_view_type_is_accepted(self):
        ref = _coerce_table_ref({"name": "V", "type": "VIEW"})
        assert ref is not None
        assert ref["table_name"] == "V"

    def test_vendor_name_snake_key_wins_over_default(self):
        ref = _coerce_table_ref(
            {"name": "T", "type": "table", "vendor_name": "postgres"},
            default_vendor="snowflake",
        )
        assert ref is not None
        assert ref["vendor_name"] == "postgres"

    def test_vendorname_camel_key_wins_over_default(self):
        ref = _coerce_table_ref(
            {"name": "T", "type": "table", "vendorName": "mysql"},
            default_vendor="snowflake",
        )
        assert ref is not None
        assert ref["vendor_name"] == "mysql"


class TestBuildDbobjIndex:
    def test_skips_non_dicts_and_entries_without_id(self):
        entry = {"id": 7, "name": "y"}
        # Non-dict entries and dicts without an ``id`` are excluded;
        # nothing is indexed under a None key.
        dbobjs = cast("list[dict[str, Any]]", ["junk", {"name": "x"}, entry])
        assert _build_dbobj_index(dbobjs) == {7: entry}

    def test_empty_input_yields_empty_index(self):
        assert _build_dbobj_index([]) == {}


class TestCoerceOneIdBasedSource:
    _INDEX = _build_dbobj_index(
        [
            {"id": 1, "db": "D", "schema": "S", "name": "T"},
            {"id": 2, "tableName": "TBL"},
            {"id": 3},
            {"id": 4, "database": "D2", "name": "T4", "vendor_name": "postgres"},
            {"id": 5, "name": "T5", "vendorName": "mysql"},
        ]
    )

    def test_returns_none_without_column_or_name(self):
        assert _coerce_one_id_based_source({"parentId": 1}, self._INDEX, "") is None

    def test_name_key_is_the_column_fallback(self):
        ref = _coerce_one_id_based_source(
            {"name": "COL", "parentId": 1}, self._INDEX, ""
        )
        assert ref is not None
        assert ref["column_name"] == "COL"

    def test_parent_db_key_resolves_all_fields(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 1}, self._INDEX, "dv"
        )
        assert ref == {
            "vendor_name": "dv",  # parent has no vendor → default_vendor
            "database": "D",  # parent ``db`` key
            "schema": "S",
            "table_name": "T",
            "column_name": "C",
        }

    def test_parent_tablename_key_is_the_table_fallback(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 2}, self._INDEX, ""
        )
        assert ref is not None
        assert ref["table_name"] == "TBL"

    def test_parent_missing_fields_default_to_empty_strings(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 3}, self._INDEX, ""
        )
        assert ref == {
            "vendor_name": "",
            "database": "",
            "schema": "",
            "table_name": "",
            "column_name": "C",
        }

    def test_parent_vendor_name_snake_key_wins_over_default(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 4}, self._INDEX, "dv"
        )
        assert ref is not None
        assert ref["vendor_name"] == "postgres"
        assert ref["database"] == "D2"  # parent ``database`` key fallback

    def test_parent_vendorname_camel_key_wins_over_default(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 5}, self._INDEX, "dv"
        )
        assert ref is not None
        assert ref["vendor_name"] == "mysql"

    # --- parentName fallback branch (parentId unresolvable) ---

    def test_fallback_four_part_parentname_keeps_dotted_table(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 99, "parentName": "A.B.C.D"}, self._INDEX, ""
        )
        assert ref is not None
        assert ref["database"] == "A"
        assert ref["schema"] == "B"
        assert ref["table_name"] == "C.D"  # extra dots stay in the table name

    def test_fallback_two_part_parentname_is_schema_table(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 99, "parentName": "S.T"}, self._INDEX, ""
        )
        assert ref is not None
        assert ref["database"] == ""
        assert ref["schema"] == "S"
        assert ref["table_name"] == "T"

    def test_fallback_one_part_parentname_is_table_only(self):
        ref = _coerce_one_id_based_source(
            {"column": "C", "parentId": 99, "parentName": "TBL"}, self._INDEX, ""
        )
        assert ref is not None
        assert ref["database"] == ""
        assert ref["schema"] == ""
        assert ref["table_name"] == "TBL"

    def test_fallback_missing_parentname_yields_empty_fields_and_default_vendor(self):
        ref = _coerce_one_id_based_source({"column": "C"}, {}, "dv")
        assert ref == {
            "vendor_name": "dv",
            "database": "",
            "schema": "",
            "table_name": "",
            "column_name": "C",
        }


class TestCoerceOneInlineSource:
    def test_returns_none_without_column_or_name(self):
        assert _coerce_one_inline_source({"table": "T"}, "") is None

    def test_name_key_is_the_column_fallback(self):
        ref = _coerce_one_inline_source({"name": "C", "table": "T"}, "")
        assert ref is not None
        assert ref["column_name"] == "C"

    def test_db_key_resolves_database(self):
        ref = _coerce_one_inline_source({"column": "C", "db": "D"}, "")
        assert ref is not None
        assert ref["database"] == "D"

    def test_database_key_is_the_db_fallback(self):
        ref = _coerce_one_inline_source({"column": "C", "database": "DBX"}, "")
        assert ref is not None
        assert ref["database"] == "DBX"

    def test_tablename_key_is_the_table_fallback(self):
        ref = _coerce_one_inline_source({"column": "C", "tableName": "TBL"}, "")
        assert ref is not None
        assert ref["table_name"] == "TBL"

    def test_missing_fields_default_to_empty_and_vendor_to_default(self):
        ref = _coerce_one_inline_source({"column": "C"}, "dv")
        assert ref == {
            "vendor_name": "dv",
            "database": "",
            "schema": "",
            "table_name": "",
            "column_name": "C",
        }

    def test_vendor_name_snake_key_wins_over_default(self):
        ref = _coerce_one_inline_source(
            {"column": "C", "vendor_name": "postgres"}, "dv"
        )
        assert ref is not None
        assert ref["vendor_name"] == "postgres"

    def test_vendorname_camel_key_wins_over_default(self):
        ref = _coerce_one_inline_source({"column": "C", "vendorName": "mysql"}, "dv")
        assert ref is not None
        assert ref["vendor_name"] == "mysql"


class TestCoerceColumnRefsDefaultsAndKeys:
    def test_default_vendor_defaults_to_empty_string(self):
        refs = _coerce_column_refs([{"source": {"column": "C", "table": "T"}}])
        assert refs[0]["vendor_name"] == ""

    def test_default_vendor_reaches_inline_sources(self):
        refs = _coerce_column_refs(
            [{"source": {"column": "C", "table": "T"}}], default_vendor="dv"
        )
        assert refs[0]["vendor_name"] == "dv"

    def test_default_vendor_reaches_id_based_sources(self):
        refs = _coerce_column_refs(
            [{"sources": [{"column": "C", "parentId": 9, "parentName": "S.T"}]}],
            default_vendor="dv",
        )
        assert len(refs) == 1
        assert refs[0]["vendor_name"] == "dv"

    def test_legacy_from_key_is_supported(self):
        refs = _coerce_column_refs([{"from": {"column": "C", "table": "T"}}])
        assert len(refs) == 1
        assert refs[0]["column_name"] == "C"
        assert refs[0]["table_name"] == "T"


class TestIterQiRecords:
    def test_missing_path_yields_nothing(self, tmp_path):
        assert list(iter_qi_records(tmp_path / "nope")) == []

    def test_directory_entries_matching_glob_are_skipped_not_fatal(self, tmp_path):
        # rglob("*.json") can match a *directory* named like a json file
        # (sorts before the real file) — it must be skipped, and the
        # remaining files still read.
        (tmp_path / "0_dir.json").mkdir()
        (tmp_path / "1_rec.json").write_text('{"a": 1}\n')
        assert list(iter_qi_records(tmp_path)) == [{"a": 1}]

    def test_blank_lines_are_skipped_not_fatal(self, tmp_path):
        f = tmp_path / "rec.json"
        f.write_text('\n   \n{"a": 1}\n')
        assert list(iter_qi_records(f)) == [{"a": 1}]

    def test_unparseable_line_warns_and_continues(self, tmp_path):
        f = tmp_path / "rec.json"
        f.write_text('{oops\n{"a": 1}\n')
        with patch("app.lineage.qi_reader.logger") as mock_logger:
            records = list(iter_qi_records(f))
        # The bad line is skipped, the rest of the file is still read.
        assert records == [{"a": 1}]
        # Exact observability contract: which file, with the traceback.
        mock_logger.warning.assert_called_once_with(
            "Skipping unparseable QI line in %s", Path(f), exc_info=True
        )


class TestQuestionQnAndName:
    def test_question_qn_empty_record_returns_empty_string(self):
        assert _question_qn({}) == ""

    def test_question_name_current_shape(self):
        assert _question_name({"extra": {"attributes": {"name": "N"}}}) == "N"

    def test_question_name_legacy_shape(self):
        assert _question_name({"QUESTION_NAME": "Legacy"}) == "Legacy"

    def test_question_name_empty_record_returns_empty_string(self):
        assert _question_name({}) == ""


class TestParseQiRecordEdgeCases:
    def test_empty_record_contract(self):
        assert parse_qi_record({}) == ("", "", [], [])

    def test_default_vendor_defaults_to_empty_string(self):
        record = {"gudusoft": {"dbobjs": [{"name": "T", "type": "table"}]}}
        _, _, tables, _ = parse_qi_record(record)
        assert tables == [
            {"vendor_name": "", "database": "", "schema": "", "table_name": "T"}
        ]

    def test_json_string_parsed_payload_is_decoded(self):
        # Legacy PARSED_DATA could arrive as a JSON *string* — it must be
        # decoded and used, not dropped.
        record = {"gudusoft": '{"dbobjs": [{"name": "T", "type": "table"}]}'}
        _, _, tables, _ = parse_qi_record(record)
        assert len(tables) == 1
        assert tables[0]["table_name"] == "T"

    def test_unparseable_string_payload_warns_and_treats_as_empty(self):
        record = {
            "extra": {"attributes": {"qualifiedName": "qn1"}},
            "gudusoft": "{not json",
        }
        with patch("app.lineage.qi_reader.logger") as mock_logger:
            result = parse_qi_record(record)
        assert result == ("qn1", "", [], [])
        # Exact observability contract: which record, with the traceback.
        mock_logger.warning.assert_called_once_with(
            "QI record %r has unparseable parsed-SQL payload; treating as empty",
            "qn1",
            exc_info=True,
        )

    def test_non_dict_non_string_payload_treated_as_empty(self):
        assert parse_qi_record({"gudusoft": 12345}) == ("", "", [], [])
        assert parse_qi_record({"gudusoft": [1, 2]}) == ("", "", [], [])

    def test_non_dict_dbobjs_entries_skipped_without_aborting(self):
        record = {"gudusoft": {"dbobjs": ["junk", {"name": "T", "type": "table"}]}}
        _, _, tables, _ = parse_qi_record(record)
        assert [t["table_name"] for t in tables] == ["T"]

    def test_effective_vendor_flows_into_column_refs(self):
        # dbvendor must be applied to *column* refs too (parent dbobj has
        # no vendor of its own → falls back to the record-level vendor).
        record = {
            "gudusoft": {
                "dbvendor": "dbsnowflake",
                "dbobjs": [
                    {
                        "id": 1,
                        "name": "T",
                        "type": "table",
                        "database": "D",
                        "schema": "S",
                    }
                ],
                "relationships": [{"sources": [{"column": "C", "parentId": 1}]}],
            },
        }
        _, _, tables, columns = parse_qi_record(record)
        assert tables[0]["vendor_name"] == "snowflake"
        assert len(columns) == 1
        assert columns[0]["vendor_name"] == "snowflake"
