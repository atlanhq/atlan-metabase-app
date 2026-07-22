"""Unit tests for app.utils utility helpers."""

import json
import os
import time
from unittest.mock import call, patch

import pytest

from app.utils import (
    read_jsonl,
    serialize_complex_columns,
    strip_html_tags,
    to_epoch_ms,
)


@pytest.fixture
def force_new_york_tz():
    """Force a non-UTC local timezone so naive-vs-UTC bugs can't hide.

    ``datetime.timestamp()`` on a naive datetime uses the *local* timezone,
    so on a UTC machine 'treat naive as UTC' and 'leave naive alone' are
    indistinguishable. Pinning TZ to America/New_York makes the difference
    observable regardless of the host machine's timezone.
    """
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


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
    # Timezone semantics (exact values, non-UTC local tz forced)
    # -------------------------------------------------------------------------

    def test_naive_string_is_interpreted_as_utc_not_local(self, force_new_york_tz):
        """A naive datetime must be pinned to UTC, never the local timezone.

        2024-01-15T10:30:00 UTC == 1705314600000 ms. If the naive datetime
        leaked through to ``timestamp()`` it would be interpreted as
        America/New_York (UTC-5) and come out 18000000 ms higher.
        """
        assert to_epoch_ms("2024-01-15T10:30:00") == 1_705_314_600_000

    def test_explicit_offset_is_respected_not_replaced_with_utc(self):
        """A +05:30 offset must shift the epoch; replacing it with UTC would
        yield 1705314600000 instead."""
        assert to_epoch_ms("2024-01-15T10:30:00+05:30") == 1_705_294_800_000

    # -------------------------------------------------------------------------
    # Logging contract
    # -------------------------------------------------------------------------

    def test_debug_logged_for_each_non_matching_format(self):
        """Each failed format attempt logs the input and format at DEBUG."""
        dt_str = "2024-01-15T10:30:00"  # matches only the 4th format
        with patch("app.utils.logger") as mock_logger:
            result = to_epoch_ms(dt_str)
        assert result is not None
        assert mock_logger.debug.call_args_list == [
            call(
                "Datetime %r did not match format %r", dt_str, "%Y-%m-%dT%H:%M:%S.%f%z"
            ),
            call("Datetime %r did not match format %r", dt_str, "%Y-%m-%dT%H:%M:%S%z"),
            call("Datetime %r did not match format %r", dt_str, "%Y-%m-%dT%H:%M:%S.%f"),
        ]
        mock_logger.warning.assert_not_called()

    def test_warning_logged_when_no_format_matches(self):
        with patch("app.utils.logger") as mock_logger:
            assert to_epoch_ms("not-a-date") is None
        mock_logger.warning.assert_called_once_with(
            "Failed to parse datetime %r against any known format", "not-a-date"
        )

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


class TestReadJsonl:
    """Tests for read_jsonl() newline-delimited JSON reader."""

    def test_none_path_returns_empty_list(self):
        assert read_jsonl(None) == []

    def test_nonexistent_path_returns_empty_list(self, tmp_path):
        """A non-empty path to a missing file must return [], not raise."""
        assert read_jsonl(str(tmp_path / "does-not-exist.json")) == []

    def test_reads_all_records(self, tmp_path):
        p = tmp_path / "records.json"
        p.write_bytes(b'{"a": 1}\n{"b": 2}\n')
        assert read_jsonl(str(p)) == [{"a": 1}, {"b": 2}]

    def test_blank_lines_are_skipped_not_terminal(self, tmp_path):
        """A blank line mid-file must not stop the read."""
        p = tmp_path / "records.json"
        p.write_bytes(b'{"a": 1}\n\n   \n{"b": 2}\n')
        assert read_jsonl(str(p)) == [{"a": 1}, {"b": 2}]

    def test_bad_line_is_skipped_not_terminal(self, tmp_path):
        """An unparseable line is skipped; later records still load."""
        p = tmp_path / "records.json"
        p.write_bytes(b'not json at all\n{"b": 2}\n')
        assert read_jsonl(str(p)) == [{"b": 2}]

    def test_non_utf8_line_is_skipped_via_binary_read(self, tmp_path):
        """Reading in binary mode tolerates invalid UTF-8 bytes: the line
        fails JSON parsing and is skipped. A text-mode read would raise
        UnicodeDecodeError during iteration instead."""
        p = tmp_path / "records.json"
        p.write_bytes(b'\x80\x81\xff\n{"ok": true}\n')
        assert read_jsonl(str(p)) == [{"ok": True}]

    def test_bad_line_logs_warning_with_path_and_traceback(self, tmp_path):
        p = tmp_path / "records.json"
        p.write_bytes(b"garbage\n")
        with patch("app.utils.logger") as mock_logger:
            assert read_jsonl(str(p)) == []
        mock_logger.warning.assert_called_once_with(
            "Skipping unparseable JSONL line in %s", str(p), exc_info=True
        )
