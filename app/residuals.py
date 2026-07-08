"""Purely-additive residual-failure tracking.

Some extract paths deliberately tolerate individual API failures — returning
an empty list or ``None`` — rather than aborting the whole workflow over one
flaky endpoint or one bad record (see the ``# conformance: ignore[E020]``
directives at each call site). Logging the failure and moving on risks it
never being reviewed, since worker-pod logs are easy to miss and don't
aggregate per-workflow-run.

This module gives those call sites a second, additive output: a local JSONL
file recording every tolerated failure, written into the same ``output_path``
staging tree tasks already use (alongside ``raw/``, ``processed/``,
``transformed/``). It changes no ``Input``/``Output`` contract field and no
task's return value — call sites keep returning their existing empty/None
sentinel exactly as before; this only adds a side file for later review.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import orjson
from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)

RESIDUAL_DIR = "residual"
RESIDUAL_FAILURES_FILE = "failures.jsonl"


def record_residual_failure(output_path: str, category: str, **detail: Any) -> None:
    """Append one JSONL record describing a tolerated (non-fatal) failure.

    Best-effort: a failure to write the residual record must never itself
    fail the calling task — this is observability, not part of the task's
    contract.

    Args:
        output_path: The task's local staging directory (``input.output_path``).
        category: Short machine-readable failure category, e.g.
            ``"dashboard_detail_fetch_failed"``.
        **detail: Additional structured fields describing the failure (e.g.
            ``endpoint=``, ``http_status=``, ``record_id=``).
    """
    try:
        residual_dir = os.path.join(output_path, RESIDUAL_DIR)
        os.makedirs(residual_dir, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            **detail,
        }
        path = os.path.join(residual_dir, RESIDUAL_FAILURES_FILE)
        with open(path, "ab") as fh:
            fh.write(orjson.dumps(record) + b"\n")
    except OSError:
        logger.warning(
            "Failed to write residual-failure record for category=%s",
            category,
            exc_info=True,
        )
