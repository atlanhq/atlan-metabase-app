"""Local filesystem layout for the Metabase connector.

Every ``@task`` reads/writes JSONL under a per-workflow output directory using
three sibling subtrees:

- ``raw/``         — direct dumps of Metabase API responses
- ``processed/``   — enriched records keyed by asset type
- ``transformed/`` — final Atlas JSON, uploaded to object storage

Keeping the directory names and join logic in one module means tasks don't
hard-code path fragments, and tests can refer to the same constants.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from application_sdk.contracts.types import FileReference

RAW_DIR = "raw"
PROCESSED_DIR = "processed"
TRANSFORMED_DIR = "transformed"


def staging_ref(path: str = "") -> FileReference:
    """Wrap a per-task LOCAL scratch base path as a typed ``FileReference``.

    ``output_path`` / ``processed_data_path`` / ``qi_local_path`` / ``stage_dir``
    are each task's local working directory for building JSONL paths — they are
    NOT the inter-task data channel (records move between tasks via the file
    ``FileReference`` fields, which the SDK persist/materialize interceptor
    round-trips). These bases are the deterministic, workflow-relative paths the
    app owns, so we set ``auto_materialize=False``: the interceptor skips them
    (see ``application_sdk.storage.file_ref_sync``), leaving the app's existing
    staging lifecycle untouched while giving the contract a typed path field
    (conformance P012) instead of a bare ``str``.
    """
    return FileReference(local_path=path, auto_materialize=False)


def default_output_path(workflow_id: str) -> str:
    """Build a sensible local output path when the orchestrator doesn't supply one.

    Used as a fallback so a workflow can still run end-to-end during local dev
    without the Automation Engine handing us a working directory.
    """
    base = Path(tempfile.gettempdir()) / "atlan-metabase-app"
    if workflow_id:
        base = base / workflow_id
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def raw_file(output_path: str, name: str) -> str:
    """Return the path of a ``raw/<name>/result-0.json`` JSONL file."""
    return os.path.join(output_path, RAW_DIR, name, "result-0.json")


def processed_file(output_path: str, name: str) -> str:
    """Return the path of a ``processed/<name>/result-0.json`` JSONL file."""
    return os.path.join(output_path, PROCESSED_DIR, name, "result-0.json")
