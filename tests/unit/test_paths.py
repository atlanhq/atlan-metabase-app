"""Unit tests for app/paths.py."""

from __future__ import annotations

from pathlib import Path

from app.paths import (
    PROCESSED_DIR,
    RAW_DIR,
    TRANSFORMED_DIR,
    default_output_path,
    processed_file,
    raw_file,
)


def test_constants_match_layout_used_by_tasks():
    assert RAW_DIR == "raw"
    assert PROCESSED_DIR == "processed"
    assert TRANSFORMED_DIR == "transformed"


def test_default_output_path_uses_tempdir_with_workflow_id(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    result = default_output_path("wf-abc")
    assert result == str(tmp_path / "atlan-metabase-app" / "wf-abc")
    assert Path(result).exists()


def test_default_output_path_no_workflow_id(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    result = default_output_path("")
    assert result == str(tmp_path / "atlan-metabase-app")
    assert Path(result).exists()


def test_default_output_path_is_idempotent(tmp_path, monkeypatch):
    """Calling twice must not raise: mkdir uses exist_ok=True so an existing
    per-workflow directory (e.g. on activity retry) is reused, not fatal."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    first = default_output_path("wf-retry")
    second = default_output_path("wf-retry")
    assert first == second
    assert Path(first).is_dir()


def test_raw_file_path_layout():
    assert raw_file("/tmp/out", "collections") == (
        "/tmp/out/raw/collections/result-0.json"
    )


def test_processed_file_path_layout():
    assert processed_file("/tmp/out", "questions") == (
        "/tmp/out/processed/questions/result-0.json"
    )
