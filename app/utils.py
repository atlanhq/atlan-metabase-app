"""Shared utility helpers for the Metabase connector."""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from typing import Any


def strip_html_tags(text: str | None) -> str | None:
    """Unescape HTML entities then strip all HTML tags.

    Used to clean ``description`` fields on collections, dashboards, and
    questions, which Metabase may store with embedded HTML markup.
    """
    if not text:
        return text
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def to_epoch_ms(dt_str: str | None) -> int | None:
    """Convert an ISO 8601 datetime string to epoch milliseconds."""
    if not dt_str:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(dt_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def serialize_complex_columns(record: dict[str, Any]) -> dict[str, Any]:
    """Serialize dict and list values in a record to JSON strings."""
    result: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            result[key] = json.dumps(value)
        else:
            result[key] = value
    return result


def write_jsonl(local_path: str, records: list[dict[str, Any]]) -> None:
    """Write *records* as newline-delimited JSON to *local_path*.

    Creates the parent directory if needed. Replaces the v2 ``JsonFileWriter``
    helper — v3 connectors write to local disk and return a ``FileReference``
    so the SDK handles upload/download between tasks.
    """
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(local_path: str) -> list[dict[str, Any]]:
    """Read newline-delimited JSON records from *local_path*.

    Missing files return ``[]`` so callers can keep their flow simple
    (matches the v2 ``_read_ndjson_dir`` behaviour).
    """
    if not local_path or not os.path.isfile(local_path):
        return []
    records: list[dict[str, Any]] = []
    with open(local_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
