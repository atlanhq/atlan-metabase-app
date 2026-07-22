"""Unit tests for app.extracts.filter — pure filter functions."""

from unittest.mock import patch

import pytest

from app.extracts.filter import (
    build_accepted_collection_ids,
    filter_collections,
    filter_dashboards,
    filter_questions,
    parse_filter_arg,
)


class TestParseFilterArg:
    """Tests for parse_filter_arg() helper."""

    def test_dict_passthrough(self):
        value = {"1": "Engineering", "2": "Marketing"}
        result = parse_filter_arg(value)
        assert result == value

    def test_json_string_is_parsed(self):
        result = parse_filter_arg('{"1": "Engineering"}')
        assert result == {"1": "Engineering"}

    def test_none_returns_empty_dict(self):
        assert parse_filter_arg(None) == {}

    def test_empty_string_returns_empty_dict(self):
        assert parse_filter_arg("") == {}

    def test_invalid_json_returns_empty_dict(self):
        assert parse_filter_arg("{invalid json}") == {}

    def test_empty_json_object_string_returns_empty_dict(self):
        assert parse_filter_arg("{}") == {}

    def test_invalid_json_logs_warning_with_value(self):
        """The invalid-JSON path logs the exact warning with the raw value."""
        with patch("app.extracts.filter.logger") as mock_logger:
            result = parse_filter_arg("{invalid json}")

        assert result == {}
        mock_logger.warning.assert_called_once_with(
            "Filter arg %r is not valid JSON; treating as empty filter",
            "{invalid json}",
            exc_info=True,
        )


class TestFilterCollections:
    """Tests for filter_collections()."""

    @pytest.fixture
    def all_collections(self):
        return [
            {"id": 1, "name": "Engineering"},
            {"id": 2, "name": "Marketing"},
            {"id": 3, "name": "Finance"},
        ]

    # -------------------------------------------------------------------------
    # Empty include/exclude → all pass
    # -------------------------------------------------------------------------

    def test_empty_include_and_exclude_passes_all(self, all_collections):
        result = filter_collections(all_collections, None, None)
        assert len(result) == 3

    def test_empty_include_and_exclude_dicts_passes_all(self, all_collections):
        result = filter_collections(all_collections, {}, {})
        assert len(result) == 3

    # -------------------------------------------------------------------------
    # Include filter
    # -------------------------------------------------------------------------

    def test_include_with_match_passes(self, all_collections):
        result = filter_collections(all_collections, {"1": "Engineering"}, None)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_include_without_match_drops_all(self, all_collections):
        result = filter_collections(all_collections, {"99": "Non-existent"}, None)
        assert result == []

    def test_include_multiple_ids(self, all_collections):
        result = filter_collections(
            all_collections, {"1": "Engineering", "3": "Finance"}, None
        )
        assert len(result) == 2
        ids = {c["id"] for c in result}
        assert ids == {1, 3}

    # -------------------------------------------------------------------------
    # Exclude filter
    # -------------------------------------------------------------------------

    def test_exclude_with_match_drops(self, all_collections):
        result = filter_collections(all_collections, None, {"2": "Marketing"})
        assert len(result) == 2
        ids = {c["id"] for c in result}
        assert 2 not in ids

    def test_exclude_without_match_keeps_all(self, all_collections):
        result = filter_collections(all_collections, None, {"99": "Non-existent"})
        assert len(result) == 3

    # -------------------------------------------------------------------------
    # Exclude takes precedence over include
    # -------------------------------------------------------------------------

    def test_exclude_takes_precedence_over_include(self, all_collections):
        """When ID is in both include and exclude, it is excluded."""
        result = filter_collections(
            all_collections,
            {"1": "Engineering"},
            {"1": "Engineering"},
        )
        assert result == []

    # -------------------------------------------------------------------------
    # String vs integer ID normalisation
    # -------------------------------------------------------------------------

    def test_integer_ids_in_filter_match_integer_ids_in_records(self):
        collections = [{"id": 1, "name": "Eng"}, {"id": 2, "name": "Mkt"}]
        # include_filter key is int (JSON keys are always strings after parse,
        # but we allow dict input with int keys too)
        result = filter_collections(collections, {1: "Eng"}, {})
        assert len(result) == 1
        assert result[0]["id"] == 1

    # -------------------------------------------------------------------------
    # Missing / None id → treated as 'root'
    # -------------------------------------------------------------------------

    def test_missing_id_treated_as_root(self):
        """A collection dict without an 'id' key is treated as the root id."""
        result = filter_collections([{"name": "No ID"}], {"root": "Root"}, None)
        assert len(result) == 1

    def test_none_id_treated_as_root(self):
        """A collection with id=None is treated as the root id."""
        result = filter_collections([{"id": None}], {"root": "Root"}, None)
        assert len(result) == 1

    # -------------------------------------------------------------------------
    # Logging contract
    # -------------------------------------------------------------------------

    def test_include_only_logs_counts_and_keys(self, all_collections):
        """Include-only filtering logs exact counts, keys, and 'none' exclude."""
        with patch("app.extracts.filter.logger") as mock_logger:
            filter_collections(all_collections, {"1": "Engineering"}, None)

        mock_logger.info.assert_called_once_with(
            "filter_collections: %d → %d (include=%s, exclude=%s)",
            3,
            1,
            ["1"],
            "none",
        )

    def test_exclude_only_logs_all_for_include(self, all_collections):
        """Exclude-only filtering logs 'all' for the empty include filter."""
        with patch("app.extracts.filter.logger") as mock_logger:
            filter_collections(all_collections, None, {"2": "Marketing"})

        mock_logger.info.assert_called_once_with(
            "filter_collections: %d → %d (include=%s, exclude=%s)",
            3,
            2,
            "all",
            ["2"],
        )

    # -------------------------------------------------------------------------
    # Empty collection list
    # -------------------------------------------------------------------------

    def test_empty_collections_list_returns_empty(self):
        result = filter_collections([], {"1": "Eng"}, {})
        assert result == []


class TestFilterDashboards:
    """Tests for filter_dashboards()."""

    @pytest.fixture
    def dashboards(self):
        return [
            {"id": 10, "name": "Sales Dash", "collection_id": 1},
            {"id": 11, "name": "Ops Dash", "collection_id": 2},
            {"id": 12, "name": "Finance Dash", "collection_id": 3},
        ]

    # -------------------------------------------------------------------------
    # Empty accepted_collection_ids → all pass
    # -------------------------------------------------------------------------

    def test_empty_accepted_ids_returns_all_dashboards(self, dashboards):
        result = filter_dashboards(dashboards, set())
        assert len(result) == 3

    # -------------------------------------------------------------------------
    # Membership check
    # -------------------------------------------------------------------------

    def test_dashboard_in_accepted_collection_passes(self, dashboards):
        result = filter_dashboards(dashboards, {"1", "2"})
        assert len(result) == 2
        ids = {d["id"] for d in result}
        assert ids == {10, 11}

    def test_dashboard_not_in_accepted_collection_dropped(self, dashboards):
        result = filter_dashboards(dashboards, {"1"})
        assert len(result) == 1
        assert result[0]["id"] == 10

    def test_no_dashboards_match_returns_empty(self, dashboards):
        result = filter_dashboards(dashboards, {"99"})
        assert result == []

    # -------------------------------------------------------------------------
    # None collection_id → treated as 'root'
    # -------------------------------------------------------------------------

    def test_none_collection_id_treated_as_root(self):
        dashboards = [{"id": 20, "collection_id": None}]
        result = filter_dashboards(dashboards, {"root"})
        assert len(result) == 1

    def test_none_collection_id_excluded_when_root_not_accepted(self):
        dashboards = [{"id": 20, "collection_id": None}]
        result = filter_dashboards(dashboards, {"1"})
        assert result == []

    def test_missing_collection_id_treated_as_root(self):
        """A dashboard without a 'collection_id' key is treated as root."""
        dashboards = [{"id": 20}]
        result = filter_dashboards(dashboards, {"root"})
        assert len(result) == 1

    # -------------------------------------------------------------------------
    # Logging contract
    # -------------------------------------------------------------------------

    def test_logs_exact_counts(self, dashboards):
        """Filtering logs the exact before/after dashboard counts."""
        with patch("app.extracts.filter.logger") as mock_logger:
            filter_dashboards(dashboards, {"1", "2"})

        mock_logger.info.assert_called_once_with("filter_dashboards: %d → %d", 3, 2)

    # -------------------------------------------------------------------------
    # Empty dashboard list
    # -------------------------------------------------------------------------

    def test_empty_dashboards_returns_empty(self):
        result = filter_dashboards([], {"1"})
        assert result == []


class TestFilterQuestions:
    """Tests for filter_questions()."""

    @pytest.fixture
    def questions(self):
        return [
            {"id": 20, "name": "Revenue Q", "collection_id": 1},
            {"id": 21, "name": "Churn Q", "collection_id": 2},
            {"id": 22, "name": "Retention Q", "collection_id": 3},
        ]

    # -------------------------------------------------------------------------
    # Empty accepted_collection_ids → all pass
    # -------------------------------------------------------------------------

    def test_empty_accepted_ids_returns_all_questions(self, questions):
        result = filter_questions(questions, set())
        assert len(result) == 3

    # -------------------------------------------------------------------------
    # Membership check
    # -------------------------------------------------------------------------

    def test_question_in_accepted_collection_passes(self, questions):
        result = filter_questions(questions, {"1", "3"})
        assert len(result) == 2
        ids = {q["id"] for q in result}
        assert ids == {20, 22}

    def test_question_not_in_accepted_collection_dropped(self, questions):
        result = filter_questions(questions, {"2"})
        assert len(result) == 1
        assert result[0]["id"] == 21

    def test_no_questions_match_returns_empty(self, questions):
        result = filter_questions(questions, {"99"})
        assert result == []

    # -------------------------------------------------------------------------
    # None collection_id → treated as 'root'
    # -------------------------------------------------------------------------

    def test_none_collection_id_treated_as_root(self):
        questions = [{"id": 30, "collection_id": None}]
        result = filter_questions(questions, {"root"})
        assert len(result) == 1

    def test_none_collection_id_excluded_when_root_not_accepted(self):
        questions = [{"id": 30, "collection_id": None}]
        result = filter_questions(questions, {"1"})
        assert result == []

    def test_missing_collection_id_treated_as_root(self):
        """A question without a 'collection_id' key is treated as root."""
        questions = [{"id": 30}]
        result = filter_questions(questions, {"root"})
        assert len(result) == 1

    # -------------------------------------------------------------------------
    # Logging contract
    # -------------------------------------------------------------------------

    def test_logs_exact_counts(self, questions):
        """Filtering logs the exact before/after question counts."""
        with patch("app.extracts.filter.logger") as mock_logger:
            filter_questions(questions, {"1", "3"})

        mock_logger.info.assert_called_once_with("filter_questions: %d → %d", 3, 2)

    # -------------------------------------------------------------------------
    # Empty question list
    # -------------------------------------------------------------------------

    def test_empty_questions_returns_empty(self):
        result = filter_questions([], {"1"})
        assert result == []


class TestBuildAcceptedCollectionIds:
    """Tests for build_accepted_collection_ids()."""

    def test_builds_string_id_set(self):
        collections = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = build_accepted_collection_ids(collections)
        assert result == {"1", "2", "3"}

    def test_root_collection_included(self):
        collections = [{"id": "root"}, {"id": 1}]
        result = build_accepted_collection_ids(collections)
        assert "root" in result

    def test_none_id_defaults_to_root(self):
        collections = [{"id": None}]
        result = build_accepted_collection_ids(collections)
        assert "root" in result

    def test_missing_id_defaults_to_root(self):
        collections = [{"name": "No ID Collection"}]
        result = build_accepted_collection_ids(collections)
        assert "root" in result

    def test_empty_collections_returns_empty_set(self):
        result = build_accepted_collection_ids([])
        assert result == set()
