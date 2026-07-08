"""Shared utility helpers for the Metabase connector."""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from typing import Any

import orjson
from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)


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
            logger.debug("Datetime %r did not match format %r", dt_str, fmt)
            continue
    logger.warning("Failed to parse datetime %r against any known format", dt_str)
    return None


def serialize_complex_columns(record: dict[str, Any]) -> dict[str, Any]:
    """Serialize dict and list values in a record to JSON strings."""
    result: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            result[key] = orjson.dumps(value).decode("utf-8")
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
    with open(local_path, "wb") as fh:
        for record in records:
            fh.write(orjson.dumps(record) + b"\n")


def read_jsonl(local_path: str | None) -> list[dict[str, Any]]:
    """Read newline-delimited JSON records from *local_path*.

    Missing or ``None`` paths return ``[]`` so callers can pass a
    ``FileReference.local_path`` (which is itself ``str | None``) without an
    extra guard.
    """
    if not local_path or not os.path.isfile(local_path):
        return []
    records: list[dict[str, Any]] = []
    with open(local_path, "rb") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(orjson.loads(line))
            except orjson.JSONDecodeError:
                logger.warning(
                    "Skipping unparseable JSONL line in %s", local_path, exc_info=True
                )
                continue
    return records
