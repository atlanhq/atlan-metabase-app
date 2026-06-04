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


def _build_dbobj_index(dbobjs: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """Index dbobjs by id for parentId-based relationship resolution.

    QI's ``to_gudusoft_output`` (sqlparser/gudusoft 3.0.6 era) emits
    relationships whose source/target entries reference parent tables by
    ``parentId`` only — the table itself (database / schema / name /
    columns) lives in ``dbobjs[]``. Reverse-lookup needs an id index.
    """
    return {
        obj.get("id"): obj
        for obj in (dbobjs or [])
        if isinstance(obj, dict) and obj.get("id") is not None
    }


def _coerce_one_id_based_source(
    src: dict[str, Any],
    dbobj_index: dict[Any, dict[str, Any]],
    default_vendor: str,
) -> dict[str, str] | None:
    """Resolve a parentId-referenced source to a flat column ref.

    Mirrors ``to_gudusoft_output`` (see atlan-query-intelligence-app
    app/pond/lorien/lineage/lineage.py:564) which emits each source as::

        {"id": <col_id>, "column": <name>, "parentId": <table_id>,
         "parentName": "<db.schema.table>"}

    The parent dbobj carries the table's ``database`` / ``schema`` /
    ``name``. Falls back to splitting ``parentName`` when the parentId
    doesn't resolve (e.g. a partial QI output drop).
    """
    column_name = _unquote_ident(str(src.get("column") or src.get("name") or ""))
    if not column_name:
        return None

    parent = dbobj_index.get(src.get("parentId"))
    if isinstance(parent, dict):
        database = parent.get("db") or parent.get("database") or ""
        schema = parent.get("schema") or ""
        table_name = parent.get("name") or parent.get("tableName") or ""
        vendor = parent.get("vendor_name") or parent.get("vendorName") or default_vendor
    else:
        # ``parentName`` is ``database.schema.name`` (or shorter) on sources;
        # on targets it's just ``name``. Best-effort split — better than
        # dropping the ref entirely.
        parts = str(src.get("parentName") or "").split(".")
        if len(parts) >= 3:
            database, schema, table_name = parts[0], parts[1], ".".join(parts[2:])
        elif len(parts) == 2:
            database, schema, table_name = "", parts[0], parts[1]
        else:
            database, schema, table_name = "", "", parts[0] if parts else ""
        vendor = default_vendor

    return {
        "vendor_name": vendor,
        "database": _unquote_ident(str(database)),
        "schema": _unquote_ident(str(schema)),
        "table_name": _unquote_ident(str(table_name)),
        "column_name": column_name,
    }


def _coerce_one_inline_source(
    end: dict[str, Any], default_vendor: str
) -> dict[str, str] | None:
    """Coerce a legacy ``source``/``from`` end with inline db/schema/table."""
    column_name = _unquote_ident(str(end.get("column") or end.get("name") or ""))
    if not column_name:
        return None
    return {
        "vendor_name": end.get("vendor_name")
        or end.get("vendorName")
        or default_vendor,
        "database": _unquote_ident(str(end.get("db") or end.get("database") or "")),
        "schema": _unquote_ident(str(end.get("schema") or "")),
        "table_name": _unquote_ident(
            str(end.get("table") or end.get("tableName") or "")
        ),
        "column_name": column_name,
    }


def _coerce_column_refs(
    relationships: list[dict[str, Any]],
    dbobj_index: dict[Any, dict[str, Any]] | None = None,
    default_vendor: str = "",
) -> list[dict[str, str]]:
    """Coerce QI relationship entries to ARS column-ref shape.

    Supports both relationship shapes:

    - **Current** (Gudusoft 3.0.6 via QI's ``to_gudusoft_output``):
      ``rel["sources"]`` is a list of refs that carry ``parentId`` /
      ``column`` / ``parentName``; the parent table info (``database`` /
      ``schema`` / ``name``) lives in the corresponding ``dbobjs[]``
      entry and is resolved via ``dbobj_index``.
    - **Legacy**: ``rel["source"]`` / ``rel["from"]`` is a single dict
      with inline ``column`` / ``table`` / ``schema`` / ``db`` /
      ``vendor_name`` fields.

    Collects distinct *source* column refs — column lineage on the
    Process side flows ``source_columns → MetabaseQuestion``.
    """
    dbobj_index = dbobj_index or {}
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []

    def _record(ref: dict[str, str] | None) -> None:
        if ref is None:
            return
        key = tuple(ref.values())
        if key in seen:
            return
        seen.add(key)
        out.append(ref)

    for rel in relationships or []:
        # Current shape (plural ``sources``, id-based parent ref).
        sources_plural = rel.get("sources")
        if isinstance(sources_plural, list):
            for src in sources_plural:
                if isinstance(src, dict):
                    _record(
                        _coerce_one_id_based_source(src, dbobj_index, default_vendor)
                    )
            continue  # don't double-process when both shapes coexist

        # Legacy shape (singular ``source`` / ``from`` with inline fields).
        for end in (rel.get("source"), rel.get("from")):
            if isinstance(end, dict):
                _record(_coerce_one_inline_source(end, default_vendor))

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
    initialised with for the scope. When QI's per-record ``dbvendor``
    (e.g. ``dbsnowflake``, ``dbbigquery``) is present it overrides
    ``default_vendor`` — that value reflects the SQL dialect Gudusoft
    actually parsed against, which is the most accurate connectorType
    for the upstream Tables.
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

    # QI emits ``dbvendor`` like ``dbsnowflake``, ``dbbigquery``,
    # ``dbpostgresql``, ``dbmssql`` (Gudusoft's vendor-prefixed enum).
    # Strip the ``db`` prefix so the value matches the Atlan connector
    # type token used in publish-app's component key (e.g. ``snowflake``).
    # This becomes the ``connectorType`` in the arsIdentity components
    # map for every Table/Column ref produced from this record.
    # Without it, publish-app's ``build_partial_qualified_name`` returns
    # an empty QN (atlan-publish-app/app/lib/partitioning/resolve/macros.py:106),
    # the resolved PartialObject lands with ``qualifiedName: null``,
    # and Atlas rejects with INVALID_OBJECT_ID.
    raw_vendor = str(parsed.get("dbvendor") or "")
    if raw_vendor.lower().startswith("db"):
        raw_vendor = raw_vendor[2:]
    effective_vendor = raw_vendor.lower() or default_vendor

    dbobjs = parsed.get("dbobjs") or []
    source_tables: list[dict[str, str]] = []
    for obj in dbobjs:
        if not isinstance(obj, dict):
            continue
        ref = _coerce_table_ref(obj, default_vendor=effective_vendor)
        if ref is not None:
            source_tables.append(ref)

    relationships = parsed.get("relationships") or []
    # ``dbobj_index`` is the id-to-dbobj map ``_coerce_column_refs`` uses
    # to resolve the ``parentId``-based source refs emitted by the
    # current Gudusoft output. Pre-fix, this was unbuilt and every
    # ``parentId`` lookup missed, dropping all column lineage silently.
    dbobj_index = _build_dbobj_index(dbobjs)
    source_columns = _coerce_column_refs(
        relationships, dbobj_index=dbobj_index, default_vendor=effective_vendor
    )

    return query_id, sql, source_tables, source_columns
