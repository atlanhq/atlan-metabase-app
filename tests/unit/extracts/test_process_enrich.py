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
from unittest.mock import patch

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
        """Structured MBQL queries (dicts) must be coerced to ""."""
        records = [{"question_id": 1, "query": {"complex": "mbql"}}]
        result = generate_questions_query_map(records)
        assert result[1]["query"] == ""

    def test_raises_on_record_missing_question_id(self):
        """Current behavior: missing question_id is a hard error (KeyError).
        Documents the contract — caller must ensure question_id is present."""
        with pytest.raises(KeyError):
            generate_questions_query_map([{"query": "no id"}])

    def test_missing_query_and_params_default_to_empty_string(self):
        """Records without ``query`` / ``params`` keys must produce exactly
        ``""`` for both fields — the downstream SQL parser and transformer
        expect strings, never None or sentinel garbage."""
        result = generate_questions_query_map([{"question_id": 1}])
        assert result[1]["query"] == ""
        assert result[1]["params"] == ""


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
        metabaseSourceSchemaName / metabaseSourceEngine populated on each
        enriched question."""
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
        # Engine drives the QI vendorKey routing. The fixture's
        # databases_map declares engine=postgres, which is in
        # METABASE_ATLAN_SOURCE_ENGINE_MAP and survives unchanged.
        assert q["metabase_source_engine"] == "postgres"

    def test_query_type_from_v1_50_stages_format(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
    ):
        """Metabase v1.50+ moved dataset_query.type into
        dataset_query.stages[0].lib/type with values like
        ``mbql.stage/native`` / ``mbql.stage/mbql``. The connector must
        normalise both back to ``native`` / ``query`` so attributes.
        metabaseQueryType is non-empty for QI downstream."""
        new_format_native = [
            {
                "id": 10,
                "name": "Top Customers",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {
                    "lib/type": "mbql/query",
                    "stages": [
                        {
                            "lib/type": "mbql.stage/native",
                            "native": "SELECT 1",
                        }
                    ],
                },
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=new_format_native,
            metabase_host="http://m",
        )
        assert questions[0]["query_type"] == "native"

        new_format_mbql = [
            {
                "id": 10,
                "name": "Top Customers",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {
                    "lib/type": "mbql/query",
                    "stages": [
                        {
                            "lib/type": "mbql.stage/mbql",
                            "source-table": 12,
                        }
                    ],
                },
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=new_format_mbql,
            metabase_host="http://m",
        )
        assert questions[0]["query_type"] == "query"

    def test_engine_maps_via_atlan_source_engine_map(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        """Metabase's ``bigquery-cloud-sdk`` engine must map to Atlan's
        ``bigquery`` so the QI vendorKey routing picks the right parser."""
        bq_dbs = {
            100: {
                "id": 100,
                "name": "bq",
                "engine": "bigquery-cloud-sdk",
                "details": {"dbname": "proj", "schema": "ds"},
            }
        }
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=bq_dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert questions[0]["metabase_source_engine"] == "bigquery"

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
            connection_qualified_name="default/metabase/123",
        )
        assert len(lineage) == 1
        edge = lineage[0]
        assert edge["name"] == "Top Customers"
        assert edge["question_id"] == 10
        assert edge["inputs"] == [
            {
                "typeName": "MetabaseQuestion",
                "uniqueAttributes": {
                    "qualifiedName": "default/metabase/123/questions/10"
                },
            }
        ]
        assert edge["outputs"] == [
            {
                "typeName": "MetabaseDashboard",
                "uniqueAttributes": {
                    "qualifiedName": "default/metabase/123/dashboards/200"
                },
            }
        ]

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


class TestProcessAssetsQuestionContract:
    """Exact-value pins on the enriched-question dict shape."""

    def test_question_enrichment_full_contract(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        q = questions[0]
        assert q["metabase_host"] == "http://m"
        assert q["sourceURL"] == "http://m/question/10"
        # collection_id=1 must resolve to THE Finance collection, not root.
        assert q["collection"]["id"] == 1
        assert q["collection"]["name"] == "Finance"
        # Full query_object contract, exact.
        assert q["query"] == {
            "query": "SELECT * FROM analytics.customers",
            "params": [],
            "default_database_name": "testdata",
            "default_schema_name": "analytics",
            "engine": "postgres",
        }

    def test_dashboard_gets_collection_attached(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        dashboards, _, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        d = dashboards[0]
        assert d["collection"]["id"] == 1
        assert d["collection"]["name"] == "Finance"

    def test_lineage_with_default_connection_qualified_name(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        """When connection_qualified_name is not passed, the default is exactly
        "" — qualifiedNames must start with "/", no sentinel prefix."""
        _, _, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert (
            lineage[0]["inputs"][0]["uniqueAttributes"]["qualifiedName"]
            == "/questions/10"
        )
        assert (
            lineage[0]["outputs"][0]["uniqueAttributes"]["qualifiedName"]
            == "/dashboards/200"
        )


class TestProcessAssetsRootFallback:
    """collection_id=None must fall back to the literal "root" key."""

    def test_dashboard_collection_id_none_falls_back_to_root(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        dash = [{"id": 400, "name": "Rootless", "collection_id": None, "dashcards": []}]
        dashboards, _, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dash,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert len(dashboards) == 1
        assert dashboards[0]["collection"]["id"] == "root"

    def test_question_collection_id_none_falls_back_to_root(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
    ):
        qs = [
            {
                "id": 10,
                "name": "Rootless Q",
                "collection_id": None,
                "database_id": 100,
                "dataset_query": {"type": "native"},
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=qs,
            metabase_host="http://m",
        )
        assert len(questions) == 1
        assert questions[0]["collection"]["id"] == "root"


class TestProcessAssetsLoopContinuation:
    """Skips must be per-item (continue), never abort the whole loop (break)."""

    def test_orphan_dashboard_does_not_abort_loop(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        orphan = {"id": 300, "name": "Orphan", "collection_id": 9999, "dashcards": []}
        dashboards, _, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=[orphan] + dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert [d["id"] for d in dashboards] == [200]
        # The valid dashboard's cards must still be mapped for lineage.
        assert len(lineage) == 1

    def test_orphan_question_does_not_abort_loop(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        orphan = {
            "id": 99,
            "name": "Orphan Q",
            "collection_id": 9999,
            "database_id": 100,
            "dataset_query": {"type": "native"},
        }
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=[orphan] + filtered_questions,
            metabase_host="http://m",
        )
        assert [q["id"] for q in questions] == [10]

    def test_question_missing_database_does_not_abort_loop(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        no_db = {
            "id": 77,
            "name": "No DB",
            "collection_id": 1,
            "database_id": 999,  # not in databases_map
            "dataset_query": {"type": "native"},
        }
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map={
                **questions_query_map,
                77: {"query": "x", "params": []},
            },
            dashboard_details=dashboard_details,
            filtered_questions=[no_db] + filtered_questions,
            metabase_host="http://m",
        )
        assert [q["id"] for q in questions] == [10]

    def test_card_without_card_id_does_not_stop_card_mapping(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        dash = [
            {
                "id": 200,
                "name": "D",
                "collection_id": 1,
                "dashcards": [
                    {"note": "text card, no card_id"},
                    {"card_id": 10, "card": {"id": 10, "name": "Q10"}},
                ],
            }
        ]
        _, questions, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dash,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert len(questions[0]["dashboards"]) == 1
        assert len(lineage) == 1

    def test_card_none_does_not_stop_card_mapping(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        dash = [
            {
                "id": 200,
                "name": "D",
                "collection_id": 1,
                "dashcards": [
                    {"card_id": 5, "card": None},
                    {"card_id": 10, "card": {"id": 10, "name": "Q10"}},
                ],
            }
        ]
        _, questions, lineage = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dash,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert len(questions[0]["dashboards"]) == 1
        assert len(lineage) == 1


class TestProcessAssetsSkipGuards:
    """query/database missing → skip; the guard is OR, not AND."""

    def test_question_with_missing_database_is_skipped(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
    ):
        qs = [
            {
                "id": 10,
                "name": "Q",
                "collection_id": 1,
                "database_id": 999,  # unknown database
                "dataset_query": {"type": "native"},
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=qs,
            metabase_host="http://m",
        )
        assert questions == []

    def test_question_with_missing_query_is_skipped(
        self,
        collections_map,
        databases_map,
        dashboard_details,
        filtered_questions,
    ):
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map={},  # no query for question 10
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert questions == []


class TestProcessAssetsDashcardsCleanup:
    def test_both_dashcards_and_ordered_cards_present_pops_both(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        filtered_questions,
    ):
        """When both field names exist (server straddling v0.49), dashcards
        wins for the count and BOTH raw fields are removed."""
        dash = [
            {
                "id": 200,
                "name": "D",
                "collection_id": 1,
                "dashcards": [{"card_id": 10, "card": {"id": 10}}],
                "ordered_cards": [
                    {"card_id": 1, "card": {"id": 1}},
                    {"card_id": 2, "card": {"id": 2}},
                ],
            }
        ]
        dashboards, _, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dash,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        d = dashboards[0]
        assert d["cards_count"] == 1  # dashcards wins
        assert "dashcards" not in d
        assert "ordered_cards" not in d


class TestProcessAssetsQueryTypeResolution:
    def test_missing_dataset_query_key_yields_empty_query_type(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
    ):
        """A question with no dataset_query at all must still enrich, with
        query_type exactly ""."""
        qs = [
            {
                "id": 10,
                "name": "No dataset_query",
                "collection_id": 1,
                "database_id": 100,
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=qs,
            metabase_host="http://m",
        )
        assert len(questions) == 1
        assert questions[0]["query_type"] == ""
        # Not native → query_object is still built.
        assert questions[0]["query"] != {}

    def test_empty_stages_list_is_handled(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
    ):
        """stages=[] must not be indexed; question enriches with query_type ""."""
        qs = [
            {
                "id": 10,
                "name": "Empty stages",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {"stages": []},
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=qs,
            metabase_host="http://m",
        )
        assert len(questions) == 1
        assert questions[0]["query_type"] == ""


class TestProcessAssetsQueryObjectGate:
    def test_native_question_without_db_details_gets_empty_query_object(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        """Native query + database with NO details key → the query_object gate
        must NOT fire: query stays {} and every flattened field is ""."""
        dbs = {100: {"id": 100, "name": "no-details", "engine": "postgres"}}
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        q = questions[0]
        assert q["query_type"] == "native"
        assert q["query"] == {}
        assert q["metabase_query"] == ""
        assert q["metabase_database_name"] == ""
        assert q["metabase_schema_name"] == ""
        assert q["metabase_source_engine"] == ""


class TestProcessAssetsEngineResolution:
    def test_unmapped_engine_passes_through_unchanged(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        """Engines not in METABASE_ATLAN_SOURCE_ENGINE_MAP fall back to the
        raw Metabase engine string, not None/""."""
        dbs = {
            100: {
                "id": 100,
                "name": "rs",
                "engine": "redshift",
                "details": {"dbname": "d", "schema": "s"},
            }
        }
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        q = questions[0]
        assert q["metabase_source_engine"] == "redshift"
        assert q["query"]["engine"] == "redshift"

    def test_database_without_engine_key_yields_empty_engine(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
    ):
        dbs = {100: {"id": 100, "name": "n", "details": {"dbname": "d"}}}
        qs = [
            {
                "id": 10,
                "name": "Q",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {"type": "query"},
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=qs,
            metabase_host="http://m",
        )
        q = questions[0]
        assert q["metabase_source_engine"] == ""
        assert q["query"]["engine"] == ""


class TestProcessAssetsNameFallbacks:
    def test_database_without_name_key_yields_empty_names(
        self,
        collections_map,
        questions_query_map,
        dashboard_details,
    ):
        """No details.dbname / details.db AND no database.name → the stored
        default_database_name is exactly "" (never None), and the flattened
        db/schema fields are ""."""
        dbs = {
            100: {
                "id": 100,
                "engine": "postgres",
                "details": {"host": "x"},  # no dbname/db/schema, no name key
            }
        }
        qs = [
            {
                "id": 10,
                "name": "Q",
                "collection_id": 1,
                "database_id": 100,
                "dataset_query": {"type": "query"},
            }
        ]
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=dbs,
            questions_query_map=questions_query_map,
            dashboard_details=dashboard_details,
            filtered_questions=qs,
            metabase_host="http://m",
        )
        q = questions[0]
        assert q["query"]["default_database_name"] == ""
        assert q["metabase_database_name"] == ""
        assert q["metabase_schema_name"] == ""

    def test_query_map_entry_without_query_key_yields_empty_metabase_query(
        self,
        collections_map,
        databases_map,
        dashboard_details,
        filtered_questions,
    ):
        _, questions, _ = process_assets(
            collections_map=collections_map,
            databases_map=databases_map,
            questions_query_map={10: {"params": []}},  # no "query" key
            dashboard_details=dashboard_details,
            filtered_questions=filtered_questions,
            metabase_host="http://m",
        )
        assert questions[0]["metabase_query"] == ""


# ---------------------------------------------------------------------------
# Observability contract — log messages and args are pinned exactly because
# on-call queries the log store for these strings during incidents.
# ---------------------------------------------------------------------------


class TestEnrichmentLogging:
    def test_generate_collections_map_logs_entry_count(self):
        with patch("app.extracts.process.logger") as mock_logger:
            generate_collections_map([{"id": 1}, {"id": 2}], "http://m")
        mock_logger.info.assert_called_once_with(
            "generate_collections_map: built map with %d entries", 2
        )

    def test_generate_databases_map_logs_entry_count(self):
        with patch("app.extracts.process.logger") as mock_logger:
            generate_databases_map([{"id": 1}], "http://m")
        mock_logger.info.assert_called_once_with(
            "generate_databases_map: built map with %d entries", 1
        )

    def test_generate_questions_query_map_logs_entry_count(self):
        with patch("app.extracts.process.logger") as mock_logger:
            generate_questions_query_map(
                [
                    {"question_id": 1, "query": "SELECT 1"},
                    {"question_id": 2, "query": "SELECT 2"},
                ]
            )
        mock_logger.info.assert_called_once_with(
            "generate_questions_query_map: built map with %d entries", 2
        )

    def test_process_assets_logs_summary_counts(
        self,
        collections_map,
        databases_map,
        questions_query_map,
        dashboard_details,
        filtered_questions,
    ):
        with patch("app.extracts.process.logger") as mock_logger:
            process_assets(
                collections_map=collections_map,
                databases_map=databases_map,
                questions_query_map=questions_query_map,
                dashboard_details=dashboard_details,
                filtered_questions=filtered_questions,
                metabase_host="http://m",
            )
        mock_logger.info.assert_any_call("process_assets: enriched %d dashboards", 1)
        mock_logger.info.assert_any_call(
            "process_assets: enriched %d questions, %d questions-dashboards lineage records",
            1,
            1,
        )

    def test_process_assets_logs_skipped_dashboard(
        self,
        collections_map,
        databases_map,
        questions_query_map,
    ):
        orphan = [{"id": 300, "name": "O", "collection_id": 9999, "dashcards": []}]
        with patch("app.extracts.process.logger") as mock_logger:
            process_assets(
                collections_map=collections_map,
                databases_map=databases_map,
                questions_query_map=questions_query_map,
                dashboard_details=orphan,
                filtered_questions=[],
                metabase_host="http://m",
            )
        mock_logger.debug.assert_called_once_with(
            "process_assets: skipping dashboard id=%s (collection_id=%s not in collections_map)",
            300,
            9999,
        )

    def test_process_assets_logs_skipped_question(
        self,
        collections_map,
        databases_map,
        questions_query_map,
    ):
        orphan = [
            {
                "id": 99,
                "name": "Orphan Q",
                "collection_id": 9999,
                "database_id": 100,
                "dataset_query": {"type": "native"},
            }
        ]
        with patch("app.extracts.process.logger") as mock_logger:
            process_assets(
                collections_map=collections_map,
                databases_map=databases_map,
                questions_query_map=questions_query_map,
                dashboard_details=[],
                filtered_questions=orphan,
                metabase_host="http://m",
            )
        mock_logger.debug.assert_called_once_with(
            "process_assets: skipping question id=%s (collection_id=%s not in collections_map)",
            99,
            9999,
        )

    def test_process_assets_warns_on_missing_query_or_database(
        self,
        collections_map,
        databases_map,
        questions_query_map,
    ):
        no_db = [
            {
                "id": 10,
                "name": "Top Customers",
                "collection_id": 1,
                "database_id": 999,  # unknown database
                "dataset_query": {"type": "native"},
            }
        ]
        with patch("app.extracts.process.logger") as mock_logger:
            process_assets(
                collections_map=collections_map,
                databases_map=databases_map,
                questions_query_map=questions_query_map,
                dashboard_details=[],
                filtered_questions=no_db,
                metabase_host="http://m",
            )
        mock_logger.warning.assert_called_once_with(
            "process_assets: missing query or database for question id=%s name=%s — skipping",
            10,
            "Top Customers",
        )
