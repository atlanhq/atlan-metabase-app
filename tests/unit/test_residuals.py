"""Unit tests for app/residuals.py residual-failure tracking."""

from __future__ import annotations

import json
from unittest.mock import patch

from app.residuals import RESIDUAL_DIR, RESIDUAL_FAILURES_FILE, record_residual_failure


def _read_records(output_path) -> list[dict]:
    path = output_path / RESIDUAL_DIR / RESIDUAL_FAILURES_FILE
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestRecordResidualFailure:
    def test_writes_one_record_with_timestamp_category_and_detail(self, tmp_path):
        record_residual_failure(
            str(tmp_path),
            "dashboard_detail_fetch_failed",
            endpoint="/api/dashboard/7",
            http_status=500,
        )
        records = _read_records(tmp_path)
        assert len(records) == 1
        record = records[0]
        assert record["category"] == "dashboard_detail_fetch_failed"
        assert record["endpoint"] == "/api/dashboard/7"
        assert record["http_status"] == 500
        # Timestamp key must be exactly "timestamp" and carry an explicit
        # UTC offset (datetime.now(timezone.utc), never a naive local time).
        assert "timestamp" in record
        assert record["timestamp"].endswith("+00:00")

    def test_repeated_calls_append_records(self, tmp_path):
        """The residual dir already existing must not break later appends
        (os.makedirs is called with exist_ok=True on every write)."""
        record_residual_failure(str(tmp_path), "first_failure")
        record_residual_failure(str(tmp_path), "second_failure")
        records = _read_records(tmp_path)
        assert [r["category"] for r in records] == ["first_failure", "second_failure"]

    def test_write_failure_is_swallowed_and_logged(self, tmp_path):
        """Best-effort contract: an OSError while writing must not propagate,
        and must log a warning naming the category with the traceback."""
        # A *file* named residual/ makes os.makedirs raise FileExistsError
        # (an OSError) even with exist_ok=True.
        (tmp_path / RESIDUAL_DIR).write_text("not a directory")
        with patch("app.residuals.logger") as mock_logger:
            record_residual_failure(str(tmp_path), "collections_fetch_failed")
        mock_logger.warning.assert_called_once_with(
            "Failed to write residual-failure record for category=%s",
            "collections_fetch_failed",
            exc_info=True,
        )
