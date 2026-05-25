"""Unit tests for app/extracts/process.py.

Covers the enrichment functions called by ``process_metabaseprocess``:
- generate_collections_map
- generate_databases_map
- generate_questions_query_map
- process_assets (the big one — builds enriched dashboards + questions +
  BIProcess lineage records; sets metabase_query, query_type,
  metabase_database_name, metabase_schema_name on questions).
- safe_get

Lineage gap-checks reflect the post-ARS architecture: this enrichment
populates the QI-input keys (attributes.metabaseQuery,
attributes.metabaseSourceDatabaseName, attributes.metabaseSourceSchemaName)
but no longer produces Process / ColumnProcess locally.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.extracts.process import (
    METABASE_ATLAN_SOURCE_ENGINE_MAP,
    generate_collections_map,
    generate_databases_map,
    generate_questions_query_map,
    process_assets,
    safe_get,
)


# ---------------------------------------------------------------------------
# safe_get
# ---------------------------------------------------------------------------


class TestSafeGet:
    def test_traverses_nested_dict(self):
        d = {"a": {"b": {"c": 42}}}
        assert safe_get(d, "a", "b", "c") == 42

    def test_returns_default_for_missing_key(self):
        assert safe_get({"a": 1}, "b", default="DEFAULT") == "DEFAULT"

    def test_returns_default_for_none_intermediate(self):
        assert safe_get({"a": None}, "a", "b", default="x") == "x"

    def test_returns_default_when_non_dict_intermediate(self):
        assert safe_get({"a": "string"}, "a", "b", default="fallback") == "fallback"

    def test_default_is_none_by_default(self):
        assert safe_get({}, "missing") is None


# ---------------------------------------------------------------------------
# generate_collections_map
# ---------------------------------------------------------------------------


class TestGenerateCollectionsMap:
    def test_annotates_each_collection_with_host_and_sourceurl(self):
        collections = [
            {"id": 1, "name": "Finance"},
            {"id": 2, "name": "Marketing"},
        ]
        result = generate_collections_map(collections, "http://metabase")
        assert result[1]["metabase_host"] == "http://metabase"
        assert result[1]["sourceURL"] == "http://metabase/collection/1"
        assert result[2]["sourceURL"] == "http://metabase/collection/2"

    def test_keys_preserve_original_id_type(self):
        result = generate_collections_map(
            [{"id": "root", "name": "Root"}, {"id": 7, "name": "Other"}],
            "http://x",
        )
        assert "root" in result
        assert 7 in result

    def test_empty_list_returns_empty_dict(self):
        assert generate_collections_map([], "http://x") == {}


# ---------------------------------------------------------------------------
# generate_databases_map
# ---------------------------------------------------------------------------


class TestGenerateDatabasesMap:
    def test_annotates_databases(self):
        dbs = [{"id": 1, "name": "src", "engine": "postgres"}]
        result = generate_databases_map(dbs, "http://x")
        assert result[1]["metabase_host"] == "http://x"
        assert result[1]["sourceURL"] == "http://x/browse/1"

    def test_empty(self):
        assert generate_databases_map([], "http://x") == {}


# ---------------------------------------------------------------------------
# generate_questions_query_map
# ---------------------------------------------------------------------------


class TestGenerateQuestionsQueryMap:
    def test_builds_lookup_by_question_id(self):
        records = [
            {"question_id": 10, "query": "SELECT 1", "params": []},
            {"question_id": 20, "query": "SELECT 2", "params": ["p"]},
        ]
        result = generate_questions_query_map(records)
        assert result[10]["query"] == "SELECT 1"
        assert result[20]["params"] == ["p"]

    def test_non_string_query_coerced_to_empty(self):
        """PES-3766: structured MBQL queries (dicts) must be coerced to ""."""
        records = [{"question_id": 1, "query": {"complex": "mbql"}}]
        result = generate_questions_query_map(records)
        assert result[1]["query"] == ""

    def test_raises_on_record_missing_question_id(self):
        """Current behavior: missing question_id is a hard error (KeyError).
        Documents the contract — caller must ensure question_id is present."""
        with pytest.raises(KeyError):
            generate_questions_query_map([{"query": "no id"}])


# ---------------------------------------------------------------------------
# Engine map
# ---------------------------------------------------------------------------


class TestEngineMap:
    def test_known_engines_present(self):
        assert METABASE_ATLAN_SOURCE_ENGINE_MAP["postgres"] == "postgres"
        assert METABASE_ATLAN_SOURCE_ENGINE_MAP["bigquery-cloud-sdk"] == "bigquery"
        assert METABASE_ATLAN_SOURCE_ENGINE_MAP["bigquery"] == "bigquery"
        assert METABASE_ATLAN_SOURCE_ENGINE_MAP["snowflake"] == "snowflake"


# ---------------------------------------------------------------------------
# process_assets — the orchestration function
# ---------------------------------------------------------------------------


@pytest.fixture
def collections_map() -> dict[Any, dict]:
    return {
        1: {
            "id": 1,
            "name": "Finance",
            "qualifiedName": "default/metabase/x/collection/1",
        },
        "root": {
            "id": "root",
            "name": "Root",
            "qualifiedName": "default/metabase/x/collection/root",
        },
    }


@pytest.fixture
def databases_map() -> dict[Any, dict]:
    return {
        100: {
            "id": 100,
            "name": "src-postgres",
            "engine": "postgres",
            "details": {"dbname": "testdata", "schema": "analytics"},
        }
    }


@pytest.fixture
def questions_query_map() -> dict[Any, dict]:
    return {
        10: {"query": "SELECT * FROM analytics.customers", "params": []},
    }


@pytest.fixture
def dashboard_details() -> list[dict]:
    return [
        {
            "id": 200,
            "name": "Dashboard A",
            "collection_id": 1,
            "dashcards": [
                {"card_id": 10, "card": {"id": 10, "name": "Q10"}},
            ],
        }
    ]


@pytest.fixture
def filtered_questions() -> list[dict]:
    return [
        {
            "id": 10,
            "name": "Top Customers",
            "collection_id": 1,
            "database_id": 100,
            "dataset_query": {"type": "native"},
        }
    ]


class TestProcessAssets:
    def test_returns_tuple_of_three_lists(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        dashboards, questions, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert isinstance(dashboards, list)
        assert isinstance(questions, list)
        assert isinstance(lineage, list)

    def test_dashboard_enrichment_dashcards_field(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        """v0.49+ Metabase uses ``dashcards``; process_assets must accept it."""
        dashboards, _, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert len(dashboards) == 1
        d = dashboards[0]
        assert d["metabase_host"] == "http://m"
        assert d["sourceURL"] == "http://m/dashboard/200"
        assert d["cards_count"] == 1
        # dashcards must be popped after enrichment.
        assert "dashcards" not in d
        assert "ordered_cards" not in d

    def test_dashboard_enrichment_ordered_cards_field(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        """Legacy ``ordered_cards`` field must still work for pre-v0.49 Metabase."""
        legacy = [
            {
                "id": 201,
                "name": "Legacy Dashboard",
                "collection_id": 1,
                "ordered_cards": [
                    {"card_id": 10, "card": {"id": 10, "name": "Q10"}},
                ],
            }
        ]
        dashboards, _, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=legacy,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert dashboards[0]["cards_count"] == 1

    def test_question_sets_qi_input_keys(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        """The QI node needs metabaseQuery / metabaseSourceDatabaseName /
        metabaseSourceSchemaName populated on each enriched question."""
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        q = questions[0]
        assert q["metabase_query"] == "SELECT * FROM analytics.customers"
        assert q["query_type"] == "native"
        assert q["metabase_database_name"] == "testdata"
        assert q["metabase_schema_name"] == "analytics"

    def test_db_name_falls_back_to_details_db_for_h2(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
    ):
        """Legacy H2 Sample Database uses ``details.db`` not ``details.dbname``."""
        h2_dbs = {
            100: {
                "id": 100,
                "name": "Sample Database",
                "engine": "h2",
                "details": {"db": "sample"},  # legacy key
            }
        }
        questions = [
            {
                "id": 10,
                "name": "Q",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {"type": "query"},
            }
        ]
        _, enriched, _ = process_assets(
            collections_map=collections_map,
            databases_map=h2_dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=questions,
            metabase_host="http://m",
        )
        assert enriched[0]["metabase_database_name"] == "sample"

    def test_db_name_falls_back_to_database_name_when_no_details(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
    ):
        """When neither dbname nor db is set, fall back to ``database.name``."""
        dbs = {
            100: {
                "id": 100,
                "name": "e2e-source",
                "engine": "postgres",
                "details": {"host": "x"},  # no dbname/db key
            }
        }
        questions = [
            {
                "id": 10,
                "name": "Q",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {"type": "query"},
            }
        ]
        _, enriched, _ = process_assets(
            collections_map=collections_map,
            databases_map=dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=questions,
            metabase_host="http://m",
        )
        # Falls back to database.name when details has neither dbname nor db.
        assert enriched[0]["metabase_database_name"] == "e2e-source"

    def test_emits_biprocess_lineage_for_questions_on_dashboards(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        _, _, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert len(lineage) == 1
        edge = lineage[0]
        assert edge["question_id"] == 10
        assert edge["question_name"] == "Top Customers"
        assert edge["dashboards"] == [{"id": 200, "name": "Dashboard A"}]

    def test_no_biprocess_when_question_has_no_dashboard(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        _, _, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=[],  # no dashboards link to question 10
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert lineage == []

    def test_skips_dashboard_whose_collection_is_not_in_map(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        orphan_dash = [
            {
                "id": 300,
                "name": "Orphan",
                "collection_id": 9999,  # not in collections_map
                "dashcards": [],
            }
        ]
        dashboards, _, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=orphan_dash,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        # Orphan dashboard is dropped.
        assert dashboards == []
