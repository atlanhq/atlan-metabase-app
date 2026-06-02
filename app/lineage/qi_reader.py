"""Read QueryIntelligence parsed-SQL output and yield per-question lineage records.

QI's current output shape (Gudusoft 3.0.6 + sqlglot 28.x dual-parser era)::

    {
        "OUTPUT_FLAGS": 33558531,
        "P0_PROCESSING_TIME": 98,
        "P1_PROCESSING_TIME": 19,
        "sql": "<original SQL>",
        "hash": "<sha1 of sql>",
        "gudusoft": {
            "dbobjs":        [...],   # list of parsed table refs
            "relationships": [...],   # column-level lineage edges
            "processes":     [...],
            "errors":        [...],
            "dbvendor":      "...",
            "queryType":     "...",
            "simpleQueryType": "...",
        },
        "gudusoftVersion": "...",
        "vendorName":      "...",
        "sourceQueryType": "MetabaseQuestion",
        "error":           false,
        "extra": {
            "typeName":   "MetabaseQuestion",
            "status":     "ACTIVE",
            "attributes": {
                "qualifiedName": "<question qn>",
                "name":          "<question name>",
                ...
            },
            "relationshipAttributes": {...}
        }
    }

We previously read ``QUERY_ID`` / ``SQL`` / ``PARSED_DATA`` (uppercase) —
that schema is from an earlier QI generation and no longer matches what
QI emits on tenant. Accept both shapes so a future QI rev that flips
back doesn't break us silently.

Each ``dbobjs`` entry is a parsed source-table reference of shape::

    {
        "name":   "customers",
        "type":   "table" | "view" | …,
        "database": "<database>",
        "schema":   "<schema>",
        ...
    }

The exact field-naming varies by parser (Gudusoft vs sqlglot); this
module normalises both into the ``{vendor_name, database, schema,
table_name}`` shape the ARS builder consumes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _unquote_ident(value: str) -> str:
    """Strip surrounding SQL identifier quotes from a parser output.

    QI's Gudusoft output preserves the original identifier quoting from
    the SQL — e.g. ``"orderlines"``, ``"PRODUCTION"``. The Atlas lineage
    publish layer matches against unquoted identifiers (``orderlines``,
    ``PRODUCTION``), so we strip a single layer of double-quote or
    backtick wrappers. Leaves bare identifiers untouched.
    """
    if not value or len(value) < 2:
        return value
    if (value[0] == '"' and value[-1] == '"') or (value[0] == "`" and value[-1] == "`"):
        return value[1:-1]
    return value


def _coerce_table_ref(
    obj: dict[str, Any], default_vendor: str = ""
) -> dict[str, str] | None:
    """Coerce one dbobjs entry to the ARS builder's table-ref shape.

    Returns ``None`` when the entry is not a resolvable table reference
    (e.g. a CTE alias, a subquery, or a missing-name record).
    """
    raw_name = obj.get("name") or obj.get("tableName") or ""
    name = _unquote_ident(str(raw_name))
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
        "database": _unquote_ident(str(obj.get("db") or obj.get("database") or "")),
        "schema": _unquote_ident(str(obj.get("schema") or "")),
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
            raw_name = end.get("column") or end.get("name") or ""
            column_name = _unquote_ident(str(raw_name))
            if not column_name:
                continue
            ref = {
                "vendor_name": end.get("vendor_name")
                or end.get("vendorName")
                or default_vendor,
                "database": _unquote_ident(
                    str(end.get("db") or end.get("database") or "")
                ),
                "schema": _unquote_ident(str(end.get("schema") or "")),
                "table_name": _unquote_ident(
                    str(end.get("table") or end.get("tableName") or "")
                ),
                "column_name": column_name,
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


def _question_qn(record: dict[str, Any]) -> str:
    """Pull the source MetabaseQuestion qualifiedName from a QI record.

    Current QI output: ``extra.attributes.qualifiedName``.
    Legacy QI output: top-level ``QUERY_ID``.
    """
    extra = record.get("extra") or {}
    if isinstance(extra, dict):
        attrs = extra.get("attributes") or {}
        if isinstance(attrs, dict):
            qn = attrs.get("qualifiedName")
            if qn:
                return str(qn)
    # Legacy fallback — kept in case QI rolls back the shape.
    return str(record.get("QUERY_ID") or "")


def _question_name(record: dict[str, Any]) -> str:
    """Pull the source MetabaseQuestion human name from a QI record.

    Current QI output: ``extra.attributes.name``.
    Legacy QI output: top-level ``QUESTION_NAME``.
    """
    extra = record.get("extra") or {}
    if isinstance(extra, dict):
        attrs = extra.get("attributes") or {}
        if isinstance(attrs, dict):
            name = attrs.get("name")
            if name:
                return str(name)
    return str(record.get("QUESTION_NAME") or "")


def parse_qi_record(
    record: dict[str, Any], default_vendor: str = ""
) -> tuple[str, str, list[dict[str, str]], list[dict[str, str]]]:
    """Extract (query_id, sql, source_tables, source_columns) from one record.

    Tolerates both QI output shapes:

    - **Current** (Gudusoft 3.0.6 + sqlglot 28.x): ``sql`` / ``gudusoft.*``
      / ``extra.attributes.qualifiedName``.
    - **Legacy**: ``SQL`` / ``PARSED_DATA.*`` / ``QUERY_ID``.

    A QI record with ``error: true`` (gudusoft + sqlglot both failed) is
    still processed — its ``gudusoft.dbobjs`` and ``gudusoft.relationships``
    are typically empty, which the ARS builder handles by emitting a
    Process with zero source-table references (still useful for the QN
    skeleton).

    ``default_vendor`` is applied to dbobjs entries that don't carry a
    ``vendor_name`` of their own — typically the catalog metadata QI was
    initialised with for the scope.
    """
    query_id = _question_qn(record)
    # `sql` is the current key; `SQL` is the legacy fallback.
    sql = str(record.get("sql") or record.get("SQL") or "")

    # Current shape: parsed lives under `gudusoft.{dbobjs,relationships}`.
    # Legacy shape: under `PARSED_DATA.{dbobjs,relationships}`.
    parsed = record.get("gudusoft") or record.get("PARSED_DATA") or {}
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            parsed = {}
    if not isinstance(parsed, dict):
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
