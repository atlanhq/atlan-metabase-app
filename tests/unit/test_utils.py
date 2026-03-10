"""Unit tests for app.utils utility helpers."""

import json

from app.utils import serialize_complex_columns, strip_html_tags, to_epoch_ms


class TestToEpochMs:
    """Tests for to_epoch_ms() ISO 8601 → epoch milliseconds converter."""

    # -------------------------------------------------------------------------
    # Valid inputs
    # -------------------------------------------------------------------------

    def test_valid_iso_with_z_timezone(self):
        """ISO 8601 string with Z suffix is parsed correctly."""
        result = to_epoch_ms("2024-01-15T10:30:00.000Z")
        assert isinstance(result, int)
        # Must be in a plausible range for Jan 2024 (epoch ms > 1.7e12)
        assert result > 1_700_000_000_000

    def test_valid_iso_with_plus_timezone(self):
        """ISO 8601 string with +HH:MM timezone offset is handled."""
        result = to_epoch_ms("2024-01-15T10:30:00+00:00")
        assert isinstance(result, int)
        assert result > 1_700_000_000_000

    def test_valid_iso_naive_assumes_utc(self):
        """Naive ISO 8601 string is treated as UTC."""
        result = to_epoch_ms("2024-01-15T10:30:00")
        assert isinstance(result, int)
        assert result > 1_700_000_000_000

    def test_valid_iso_with_z_and_plus_produce_same_result(self):
        """Z and +00:00 timezone representations must yield the same epoch ms."""
        result_z = to_epoch_ms("2024-01-15T10:30:00.000Z")
        result_plus = to_epoch_ms("2024-01-15T10:30:00+00:00")
        assert result_z is not None
        assert result_plus is not None
        # Allow 1 ms tolerance for fractional seconds
        assert abs(result_z - result_plus) <= 1

    def test_valid_iso_with_microseconds_naive(self):
        """ISO 8601 with microseconds and no timezone parsed correctly."""
        result = to_epoch_ms("2024-01-15T10:30:00.123456")
        assert isinstance(result, int)
        # Must be greater than base value (Jan 15 2024 10:30:00 UTC)
        assert result > 1_700_000_000_000

    def test_result_is_integer(self):
        """Return type must always be int, not float."""
        result = to_epoch_ms("2024-01-15T10:30:00.000Z")
        assert type(result) is int

    # -------------------------------------------------------------------------
    # Falsy / invalid inputs → None
    # -------------------------------------------------------------------------

    def test_none_returns_none(self):
        assert to_epoch_ms(None) is None

    def test_empty_string_returns_none(self):
        assert to_epoch_ms("") is None

    def test_unparseable_string_returns_none(self):
        assert to_epoch_ms("not-a-date") is None

    def test_partial_date_returns_none(self):
        assert to_epoch_ms("2024-01-15") is None

    def test_plain_number_string_returns_none(self):
        assert to_epoch_ms("1705312200000") is None

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    def test_different_dates_produce_different_values(self):
        ts1 = to_epoch_ms("2024-01-15T10:30:00.000Z")
        ts2 = to_epoch_ms("2024-01-16T10:30:00.000Z")
        assert ts1 is not None
        assert ts2 is not None
        assert ts2 > ts1
        # One day apart in ms (86400 seconds = 86400000 ms)
        assert ts2 - ts1 == 86_400_000


class TestStripHtmlTags:
    """Tests for strip_html_tags() HTML → plain text converter."""

    def test_strips_paragraph_tags(self):
        result = strip_html_tags("<p>Hello world</p>")
        assert result == "Hello world"

    def test_strips_anchor_tags(self):
        result = strip_html_tags('<a href="https://example.com">Click here</a>')
        assert result == "Click here"

    def test_strips_multiple_tags(self):
        result = strip_html_tags("<p><strong>Bold</strong> and <em>italic</em></p>")
        assert result == "Bold and italic"

    def test_unescapes_html_entities(self):
        result = strip_html_tags("Revenue &amp; Growth")
        assert result == "Revenue & Growth"

    def test_unescapes_lt_gt_entities(self):
        result = strip_html_tags("a &lt; b &gt; c")
        assert result == "a < b > c"

    def test_plain_text_unchanged(self):
        result = strip_html_tags("No HTML here")
        assert result == "No HTML here"

    def test_none_returns_none(self):
        assert strip_html_tags(None) is None

    def test_empty_string_returns_empty_string(self):
        """Empty string is falsy — returned unchanged (same as None path)."""
        result = strip_html_tags("")
        # The function does `if not text: return text` so "" → ""
        assert result == ""

    def test_nested_tags_stripped(self):
        result = strip_html_tags("<div><p>Nested <b>bold</b> text</p></div>")
        assert result == "Nested bold text"

    def test_combined_entities_and_tags(self):
        result = strip_html_tags("<p>Revenue &amp; <strong>Profit</strong></p>")
        assert result == "Revenue & Profit"


class TestSerializeComplexColumns:
    """Tests for serialize_complex_columns() record dict serializer."""

    def test_dict_value_serialized_to_json_string(self):
        record = {"dataset_query": {"type": "native", "query": "SELECT 1"}}
        result = serialize_complex_columns(record)
        assert isinstance(result["dataset_query"], str)
        assert json.loads(result["dataset_query"]) == {
            "type": "native",
            "query": "SELECT 1",
        }

    def test_list_value_serialized_to_json_string(self):
        record = {"result_metadata": [{"name": "col1"}, {"name": "col2"}]}
        result = serialize_complex_columns(record)
        assert isinstance(result["result_metadata"], str)
        assert json.loads(result["result_metadata"]) == [
            {"name": "col1"},
            {"name": "col2"},
        ]

    def test_str_value_passed_through(self):
        record = {"name": "My Question"}
        result = serialize_complex_columns(record)
        assert result["name"] == "My Question"
        assert isinstance(result["name"], str)

    def test_int_value_passed_through(self):
        record = {"id": 42}
        result = serialize_complex_columns(record)
        assert result["id"] == 42

    def test_bool_value_passed_through(self):
        record = {"archived": False}
        result = serialize_complex_columns(record)
        assert result["archived"] is False

    def test_none_value_passed_through(self):
        record = {"description": None}
        result = serialize_complex_columns(record)
        assert result["description"] is None

    def test_mixed_record_serializes_only_complex_values(self):
        record = {
            "id": 1,
            "name": "Revenue",
            "dataset_query": {"type": "query"},
            "tags": ["finance", "growth"],
            "archived": False,
        }
        result = serialize_complex_columns(record)
        assert result["id"] == 1
        assert result["name"] == "Revenue"
        assert isinstance(result["dataset_query"], str)
        assert isinstance(result["tags"], str)
        assert result["archived"] is False

    def test_empty_record_returns_empty_dict(self):
        result = serialize_complex_columns({})
        assert result == {}

    def test_empty_dict_value_serialized_to_json_string(self):
        record = {"details": {}}
        result = serialize_complex_columns(record)
        assert result["details"] == "{}"

    def test_empty_list_value_serialized_to_json_string(self):
        record = {"ordered_cards": []}
        result = serialize_complex_columns(record)
        assert result["ordered_cards"] == "[]"

    def test_output_is_a_new_dict_not_mutated_in_place(self):
        original = {"dataset_query": {"type": "native"}}
        result = serialize_complex_columns(original)
        # Original must not be mutated
        assert isinstance(original["dataset_query"], dict)
        assert isinstance(result["dataset_query"], str)
