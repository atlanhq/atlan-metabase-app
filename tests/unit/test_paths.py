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
    transformed_file,
    transformed_leaf,
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


def test_raw_file_path_layout():
    assert raw_file("/tmp/out", "collections") == (
        "/tmp/out/raw/collections/result-0.json"
    )


def test_processed_file_path_layout():
    assert processed_file("/tmp/out", "questions") == (
        "/tmp/out/processed/questions/result-0.json"
    )


def test_transformed_leaf_keeps_the_typename_segment():
    """The leaf publish walks: ``<TYPENAME>/result-<chunk>.json``.

    Built with an explicit ``/`` because it is used verbatim as an
    object-store key (the ``DeclaredFile`` label the fan-in hands the SDK),
    not only as a filesystem path.
    """
    assert transformed_leaf("METABASECOLLECTION", 0) == (
        "METABASECOLLECTION/result-0.json"
    )
    assert transformed_leaf("BIPROCESS", 5) == "BIPROCESS/result-5.json"


def test_transformed_file_joins_the_leaf_under_the_transformed_dir():
    """The producer's local path and the delivered key share one leaf.

    ``transform_data`` writes here and declares the file; the entrypoint
    labels the same ref with ``transformed_leaf``, so the object-store key
    equals the local key under a different prefix.
    """
    path = transformed_file("/tmp/out", "METABASEQUESTION", 0)
    assert path == "/tmp/out/transformed/METABASEQUESTION/result-0.json"
    assert path.endswith(transformed_leaf("METABASEQUESTION", 0))
