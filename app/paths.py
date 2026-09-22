"""Local filesystem layout for the Metabase connector.

Every ``@task`` reads/writes JSONL under its own scratch directory (see
:func:`task_scratch_dir`) using sibling subtrees:

- ``raw/``         — direct dumps of Metabase API responses
- ``processed/``   — enriched records keyed by asset type
- ``transformed/`` — final Atlas JSON, uploaded to object storage
- ``residual/``    — tolerated-failure records (see ``app/residuals.py``)

Keeping the directory names and join logic in one module means tasks don't
hard-code path fragments, and tests can refer to the same constants.

No path is shared between tasks. Each task may run on a different pod, so
anything one task hands another travels as a ``FileReference`` on its
output contract, never as a directory both are assumed to see.
"""

from __future__ import annotations

import os
import tempfile

RAW_DIR = "raw"
PROCESSED_DIR = "processed"
TRANSFORMED_DIR = "transformed"


def task_scratch_dir(task_name: str) -> str:
    """Create and return a fresh local scratch directory for one task invocation.

    A task needs *a* writable directory on its own pod, not the producer's,
    so it makes one rather than receiving a path through its contract (the
    same shape as ``atlan-openapi-app``'s ``tempfile.mkdtemp`` per task).
    A new directory per invocation also means a retried activity never
    appends to a previous attempt's files.
    """
    return tempfile.mkdtemp(prefix=f"atlan-metabase-{task_name}-")


def raw_file(output_path: str, name: str) -> str:
    """Return the path of a ``raw/<name>/result-0.json`` JSONL file."""
    return os.path.join(output_path, RAW_DIR, name, "result-0.json")


def processed_file(output_path: str, name: str) -> str:
    """Return the path of a ``processed/<name>/result-0.json`` JSONL file."""
    return os.path.join(output_path, PROCESSED_DIR, name, "result-0.json")


def transformed_leaf(typename: str, chunk_start: int) -> str:
    """Return the ``<TYPENAME>/result-<chunk>.json`` leaf of a transformed file.

    This is the key shape PublishNode reads, expressed once.
    ``transform_data`` joins it under ``<output_path>/transformed/`` to
    write the file; ``extract_metadata`` reuses it verbatim as the
    ``DeclaredFile`` label so the delivered object-store key matches the
    local tree. Built with an explicit ``/`` rather than ``os.path.join``
    because it is an object-store key, not a filesystem path.
    """
    return f"{typename}/result-{chunk_start}.json"


def transformed_file(output_path: str, typename: str, chunk_start: int) -> str:
    """Return the local path of one ``transformed/<TYPENAME>/result-<chunk>.json``."""
    return os.path.join(
        output_path, TRANSFORMED_DIR, typename, f"result-{chunk_start}.json"
    )
