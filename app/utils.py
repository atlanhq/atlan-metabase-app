"""Shared utility helpers for the Metabase connector."""

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from application_sdk.io.json import JsonFileWriter


def strip_html_tags(text: Optional[str]) -> Optional[str]:
    """Unescape HTML entities then strip all HTML tags.

    Used to clean ``description`` fields on collections, dashboards, and
    questions, which Metabase may store with embedded HTML markup.

    Args:
        text: Raw string that may contain HTML entities (e.g. ``&amp;``) and
            tags (e.g. ``<p>``).  ``None`` is returned unchanged.

    Returns:
        Cleaned plain-text string, or ``None`` if ``text`` was falsy.
    """
    if not text:
        return text
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def to_epoch_ms(dt_str: Optional[str]) -> Optional[int]:
    """Convert an ISO 8601 datetime string to epoch milliseconds.

    Attempts several common format patterns in order, including timezone-aware
    and naive variants.  Metabase timestamps are typically returned as strings
    such as ``"2024-01-15T10:30:00.000Z"`` or ``"2024-01-15T10:30:00"``.

    Args:
        dt_str: ISO 8601 datetime string, or ``None``.

    Returns:
        Integer epoch milliseconds, or ``None`` if ``dt_str`` is falsy or
        cannot be parsed.
    """
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
            # If naive, assume UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def serialize_complex_columns(record: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize dict and list values in a record to JSON strings.

    Used before writing records to NDJSON to ensure that nested structures
    (e.g. ``dataset_query``, ``details``, ``result_metadata``) are stored as
    plain strings rather than raw Python objects, which simplifies downstream
    JSON reading without requiring recursive serialisation of nested dicts.

    Args:
        record: Flat or nested dict representing a single API record.

    Returns:
        New dict where every value that is a ``dict`` or ``list`` has been
        replaced by its JSON string representation.  All other value types
        (str, int, float, bool, None) are passed through unchanged.
    """
    result: Dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            result[key] = json.dumps(value)
        else:
            result[key] = value
    return result


def setup_json_writer(output_path: str, suffix: str) -> JsonFileWriter:
    """Create a ``JsonFileWriter`` for the given output path and typename suffix.

    Wraps :class:`~application_sdk.io.json.JsonFileWriter` so that extract and
    process activities can obtain a writer without importing the SDK class
    directly.

    Args:
        output_path: Base output directory for the current workflow run.
        suffix: Typename / subdirectory suffix (e.g. ``"collections"``,
            ``"dashboards"``).

    Returns:
        Configured :class:`~application_sdk.io.json.JsonFileWriter` instance.
    """
    return JsonFileWriter(path=output_path, typename=suffix)
