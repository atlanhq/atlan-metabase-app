"""Read QueryIntelligence parsed-SQL output and yield per-question lineage records.

QI writes NDJSON records of shape::

    {
        "QUERY_ID":    "<the qualifiedName of the source MetabaseQuestion>",
        "SQL":         "<original SQL>",
        "PARSED_DATA": {
            "dbobjs":         [...],   # list of parsed table refs
            "relationships":  [...],   # column-level lineage edges
            "SIMPLE_QUERY_TYPE": "...",
            ...
        },
        "OUTPUT_FLAGS": "...",
    }

Each ``dbobjs`` entry is a parsed source-table reference of shape::

    {
        "name":   "customers",
        "qn":     "<database>/<schema>/<table>",   # parsed best-effort
        "type":   "table" | "view" | …,
        "db":     "<database>",
        "schema": "<schema>",
        ...
    }

The exact shape varies by parser (Gudusoft vs sqlglot, governed by QI's
internal selection). This module normalises both into the
``{vendor_name, database, schema, table_name}`` shape the ARS builder
consumes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _coerce_table_ref(
    obj: dict[str, Any], default_vendor: str = ""
) -> dict[str, str] | None:
    """Coerce one dbobjs entry to the ARS builder's table-ref shape.

    Returns ``None`` when the entry is not a resolvable table reference
    (e.g. a CTE alias, a subquery, or a missing-name record).
    """
    name = obj.get("name") or obj.get("tableName") or ""
    if not name:
        return None
    obj_type = (obj.get("type") or obj.get("objectType") or "").lower()
    if obj_type and obj_type not in ("table", "view"):
        # Skip CTEs, subqueries, derived tables.
        return None
    return {
        "vendor_name": obj.get("vendor_name")
        or obj.get("vendorName")
        or default_vendor,
        "database": obj.get("db") or obj.get("database") or "",
        "schema": obj.get("schema") or "",
        "table_name": name,
    }


def _coerce_column_refs(
    relationships: list[dict[str, Any]], default_vendor: str = ""
) -> list[dict[str, str]]:
    """Coerce QI relationship entries to ARS column-ref shape.

    QI's relationships array contains pairs of source/target column refs
    plus an edge kind (typically ``"fdd"`` = field-direct-dependency).
    We collect distinct *source* column refs — column lineage on the
    Process side flows source_columns → MetabaseQuestion.
    """
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []
    for rel in relationships or []:
        for end in (rel.get("source"), rel.get("from")):
            if not isinstance(end, dict):
                continue
            name = end.get("column") or end.get("name") or ""
            if not name:
                continue
            ref = {
                "vendor_name": end.get("vendor_name")
                or end.get("vendorName")
                or default_vendor,
                "database": end.get("db") or end.get("database") or "",
                "schema": end.get("schema") or "",
                "table_name": end.get("table") or end.get("tableName") or "",
                "column_name": name,
            }
            key = tuple(ref.values())
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
    return out


def iter_qi_records(input_path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield QI output records from a single NDJSON file or directory tree.

    Silently skips empty/malformed lines so a partial QI write doesn't
    abort the whole lineage build.
    """
    p = Path(input_path)
    if not p.exists():
        return
    files = sorted(p.rglob("*.json")) if p.is_dir() else [p]
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_qi_record(
    record: dict[str, Any], default_vendor: str = ""
) -> tuple[str, str, list[dict[str, str]], list[dict[str, str]]]:
    """Extract (query_id, sql, source_tables, source_columns) from one record.

    ``default_vendor`` is applied to dbobjs entries that don't carry a
    ``vendor_name`` of their own — typically the catalog metadata QI was
    initialised with for the scope.
    """
    query_id = str(record.get("QUERY_ID") or "")
    sql = str(record.get("SQL") or "")
    parsed = record.get("PARSED_DATA") or {}
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            parsed = {}

    dbobjs = parsed.get("dbobjs") or []
    source_tables: list[dict[str, str]] = []
    for obj in dbobjs:
        if not isinstance(obj, dict):
            continue
        ref = _coerce_table_ref(obj, default_vendor=default_vendor)
        if ref is not None:
            source_tables.append(ref)

    relationships = parsed.get("relationships") or []
    source_columns = _coerce_column_refs(relationships, default_vendor=default_vendor)

    return query_id, sql, source_tables, source_columns
