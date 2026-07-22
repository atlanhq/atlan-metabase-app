"""Unit tests for app.api_types — typed record factories and helpers.

Pins the ``_to_millis`` timestamp-coercion contract:

- ints/floats pass through as epoch ms unchanged
- ISO-8601 strings ("Z" suffix, explicit offsets, or naive) convert to the
  exact UTC epoch ms — naive timestamps are assumed UTC, never local time
- ``None``/empty values return ``None`` *silently*; unparseable strings
  return ``None`` and emit the exact diagnostic warning (with traceback)
"""

from __future__ import annotations

import os
import time
from unittest import mock

import pytest

from app.api_types import _to_millis

# 2024-03-04T11:22:33Z as epoch ms (11:22:33.456Z → …456).
EPOCH_MS = 1709551353000


class TestToMillisPassthrough:
    def test_none_returns_none(self):
        assert _to_millis(None) is None

    def test_empty_string_returns_none_without_warning(self):
        """ "" is a routine missing-value shape, not a parse failure — it must
        short-circuit to None silently instead of tripping the ValueError
        warning path."""
        with mock.patch("app.api_types.logger") as logger:
            assert _to_millis("") is None
        logger.warning.assert_not_called()

    def test_int_passes_through_unchanged(self):
        assert _to_millis(1234567890123) == 1234567890123

    def test_float_truncates_to_int(self):
        assert _to_millis(1234.9) == 1234

    def test_non_temporal_type_returns_none(self):
        assert _to_millis(["2024-03-04T11:22:33Z"]) is None


class TestToMillisIsoParsing:
    def test_z_suffix_parses_to_exact_epoch_ms(self):
        assert _to_millis("2024-03-04T11:22:33.456Z") == EPOCH_MS + 456

    def test_explicit_offset_is_honoured(self):
        # +05:30 must NOT be collapsed to UTC: 11:22:33+05:30 == 05:52:33Z,
        # i.e. exactly 5.5 h earlier than the same wall-clock time in UTC.
        assert _to_millis("2024-03-04T11:22:33+05:30") == EPOCH_MS - 19_800_000

    def test_naive_timestamp_assumed_utc_regardless_of_local_tz(self):
        """A tz-less timestamp is stamped UTC, not interpreted in the local
        zone — pin it under a non-UTC TZ so a local-time regression cannot
        hide on UTC-configured CI hosts."""
        if not hasattr(time, "tzset"):  # pragma: no cover - non-POSIX only
            pytest.skip("time.tzset() unavailable on this platform")
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            assert _to_millis("2024-03-04T11:22:33") == EPOCH_MS
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()


class TestToMillisFailurePath:
    def test_unparseable_string_returns_none_and_warns_exactly(self):
        """Parse failures must degrade to None (attribute stays unset) and
        log the exact diagnostic with the offending value and traceback."""
        with mock.patch("app.api_types.logger") as logger:
            assert _to_millis("not-a-timestamp") is None
        logger.warning.assert_called_once_with(
            "Failed to parse timestamp %r as epoch ms",
            "not-a-timestamp",
            exc_info=True,
        )
