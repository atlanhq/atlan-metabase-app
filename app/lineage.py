"""SQL lineage parser — replaces v2 Gudusoft step with in-app ``sqlglot``.

For each question with non-empty native SQL, parse the statement against
the question's database engine dialect, match referenced tables (and
explicit columns) to the cached ``database_metadata`` tree, and emit
``Process`` (table-level) + ``ColumnProcess`` (column-level) records.

Output records mirror the v2 transformer's YAML-driven shape so the same
``transform_data`` Daft pipeline writes the Atlas JSON.
"""

from __future__ import annotations

from typing import Any

import sqlglot
import sqlglot.expressions as exp
from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)


# Map Metabase ``database.engine`` strings to sqlglot dialects.
# Engines absent from this map fall through to sqlglot's default (best-effort
# generic parser); a parse failure increments ``parse_failures`` but does
# not raise.
_ENGINE_TO_DIALECT: dict[str, str] = {
    "postgres": "postgres",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "bigquery-cloud-sdk": "bigquery",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlserver": "tsql",
    "mssql": "tsql",
    "oracle": "oracle",
    "presto": "presto",
    "trino": "trino",
    "athena": "trino",
    "sparksql": "spark",
    "spark": "spark",
    "databricks": "databricks",
    "h2": "",  # H2 isn't a sqlglot dialect; default parser handles most.
    "sqlite": "sqlite",
    "clickhouse": "clickhouse",
    "duckdb": "duckdb",
}


def engine_to_dialect(engine: str | None) -> str:
    """Resolve Metabase engine to sqlglot dialect name; empty for default."""
    if not engine:
        return ""
    return _ENGINE_TO_DIALECT.get(engine.lower(), "")


def _table_qualified_name(
    connection_qn: str, database_name: str, schema: str | None, table: str
) -> str:
    """Build an Atlan qualified name for a referenced source table.

    Mirrors the v2 Gudusoft output: ``{conn_qn}/{database}/{schema}/{table}``
    where schema may be omitted if the database is single-schema.
    """
    parts = [connection_qn, database_name]
    if schema:
        parts.append(schema)
    parts.append(table)
    return "/".join(p for p in parts if p)


def _column_qualified_name(table_qn: str, column: str) -> str:
    return f"{table_qn}/{column}"


def _build_database_lookup(
    database_metadata: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Build a lookup: database_id → {name, engine, tables[{schema,name,fields}]}.

    ``database_metadata`` rows come from ``GET /api/database/{id}/metadata``;
    each carries ``id``, ``name``, ``engine``, ``tables: [...]``.
    """
    lookup: dict[int, dict[str, Any]] = {}
    for row in database_metadata or []:
        db_id = row.get("id")
        if db_id is None:
            continue
        lookup[int(db_id)] = row
    return lookup


def _build_table_index(
    db_record: dict[str, Any],
) -> dict[tuple[str | None, str], dict[str, Any]]:
    """Index a database's tables by (schema_lowered, name_lowered).

    Schema can be absent in single-schema engines (e.g. MySQL). When the
    SQL doesn't qualify a table with a schema, we fall back to a
    name-only match across all schemas (first wins; logged).
    """
    index: dict[tuple[str | None, str], dict[str, Any]] = {}
    for t in db_record.get("tables", []) or []:
        name = (t.get("name") or "").lower()
        if not name:
            continue
        schema = (t.get("schema") or "").lower() or None
        index[(schema, name)] = t
    return index


def _resolve_table(
    table_index: dict[tuple[str | None, str], dict[str, Any]],
    schema: str | None,
    name: str,
) -> dict[str, Any] | None:
    """Look up a parsed table ref in the database's table index."""
    key_schema = schema.lower() if schema else None
    key = (key_schema, name.lower())
    if key in table_index:
        return table_index[key]
    # Fallback: name-only match (any schema). First wins.
    for (s, n), t in table_index.items():
        if n == name.lower():
            return t
    return None


def _parse_sql(sql: str, dialect: str) -> exp.Expression | None:
    """Parse one SQL statement; return None on failure (caller logs)."""
    if not sql or not sql.strip():
        return None
    try:
        return sqlglot.parse_one(sql, read=dialect or None)
    except Exception as exc:  # sqlglot.errors.* — keep broad to log + continue
        logger.warning("sqlglot.parse_one failed (dialect=%s): %s", dialect, exc)
        return None


def _extract_table_refs(
    ast: exp.Expression,
) -> list[tuple[str | None, str | None, str]]:
    """Walk the AST and yield distinct (catalog, schema, table) tuples.

    Excludes CTE / subquery aliases (``WITH foo AS ...`` does not produce a
    table ref for ``foo``). Catalog is preserved but unused for matching
    (Metabase doesn't expose cross-database refs).
    """
    seen: set[tuple[str | None, str | None, str]] = set()
    refs: list[tuple[str | None, str | None, str]] = []

    # Collect CTE names so we can skip them.
    cte_names = {
        (c.alias_or_name or "").lower()
        for c in ast.find_all(exp.CTE)
        if c.alias_or_name
    }

    for t in ast.find_all(exp.Table):
        table_name = t.name
        if not table_name or table_name.lower() in cte_names:
            continue
        schema = t.args.get("db")
        catalog = t.args.get("catalog")
        schema_str = schema.name if isinstance(schema, exp.Identifier) else None
        catalog_str = catalog.name if isinstance(catalog, exp.Identifier) else None
        key = (catalog_str, schema_str, table_name)
        if key in seen:
            continue
        seen.add(key)
        refs.append((catalog_str, schema_str, table_name))
    return refs


def _extract_column_refs(ast: exp.Expression) -> list[tuple[str | None, str]]:
    """Yield distinct (table_qualifier, column) pairs from the projection list.

    Conservative: only picks columns that are explicit (i.e. not ``*``).
    Returns the qualifier as the table alias (e.g. ``t.col`` → ``(t, col)``)
    when present; raw column refs return ``(None, col)``.
    """
    seen: set[tuple[str | None, str]] = set()
    out: list[tuple[str | None, str]] = []
    for c in ast.find_all(exp.Column):
        col_name = c.name
        if not col_name or col_name == "*":
            continue
        table_qual = c.table or None
        key = (table_qual, col_name)
        if key not in seen:
            seen.add(key)
            out.append((table_qual, col_name))
    return out


def _table_ref(qn: str) -> dict[str, str]:
    """Atlas reference to a source table — qn is treated as schema-agnostic; the
    publish layer resolves typeName from the qn's owning connector. We don't
    know the upstream typeName at parse time (postgres vs snowflake vs …), so
    we use the generic ``Table`` typeName which the platform up-resolves."""
    return {"typeName": "Table", "uniqueAttributes": {"qualifiedName": qn}}


def _column_ref(qn: str) -> dict[str, str]:
    return {"typeName": "Column", "uniqueAttributes": {"qualifiedName": qn}}


def _question_ref(question_qn: str) -> dict[str, str]:
    return {
        "typeName": "MetabaseQuestion",
        "uniqueAttributes": {"qualifiedName": question_qn},
    }


def _process_record(
    question: dict[str, Any],
    connection_qn: str,
    input_table_qns: list[str],
    sql: str,
) -> dict[str, Any]:
    """Build a Process record matching the ``process.yaml`` transformer shape.

    The transformer YAML reads ``inputs`` and ``outputs`` directly — these
    must be Atlas reference dicts, not bare qualified-name strings.
    """
    q_id = question.get("id") or question.get("metabase_question_id")
    q_name = question.get("name", "")
    question_qn = f"{connection_qn}/questions/{q_id}"
    return {
        "id": q_id,
        "name": q_name,
        "question_id": q_id,
        "question_qualified_name": question_qn,
        "sql": sql,
        "input_table_qualified_names": input_table_qns,
        "inputs": [_table_ref(qn) for qn in input_table_qns],
        "outputs": [_question_ref(question_qn)],
        "connection_qualified_name": connection_qn,
        "connector_name": "metabase",
    }


def _column_process_record(
    question: dict[str, Any],
    connection_qn: str,
    input_column_qns: list[str],
    sql: str,
) -> dict[str, Any]:
    """Build a ColumnProcess record matching ``columnprocess.yaml`` shape."""
    q_id = question.get("id") or question.get("metabase_question_id")
    q_name = question.get("name", "")
    question_qn = f"{connection_qn}/questions/{q_id}"
    process_qn = f"{connection_qn}/question_tables/{q_id}"
    return {
        "id": q_id,
        "name": q_name,
        "question_id": q_id,
        "question_qualified_name": question_qn,
        "process_qualified_name": process_qn,
        "sql": sql,
        "input_column_qualified_names": input_column_qns,
        "inputs": [_column_ref(qn) for qn in input_column_qns],
        "outputs": [_question_ref(question_qn)],
        "process_relationship": {
            "typeName": "Process",
            "uniqueAttributes": {"qualifiedName": process_qn},
        },
        "connection_qualified_name": connection_qn,
        "connector_name": "metabase",
    }


def emit_lineage_records(
    *,
    questions: list[dict[str, Any]],
    database_metadata: list[dict[str, Any]],
    connection_qualified_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Parse each question's SQL and emit (processes, column_processes, failures).

    Skipped silently (no failure counted) when:
    - Question has no native SQL (e.g. MBQL/GUI questions)
    - Question's database is unknown (not in database_metadata)

    Counted as failures when sqlglot can't parse the statement, or when no
    referenced table resolves against the database's table index.
    """
    db_lookup = _build_database_lookup(database_metadata)
    processes: list[dict[str, Any]] = []
    column_processes: list[dict[str, Any]] = []
    failures = 0

    for q in questions or []:
        query_obj = q.get("query") or q.get("query_object") or {}
        sql = (query_obj.get("query") or "").strip()
        if not sql:
            continue

        # Resolve database
        db_id = q.get("database_id")
        if db_id is None:
            continue
        db_record = db_lookup.get(int(db_id))
        if db_record is None:
            continue

        dialect = engine_to_dialect(db_record.get("engine"))
        ast = _parse_sql(sql, dialect)
        if ast is None:
            failures += 1
            continue

        table_index = _build_table_index(db_record)
        db_name = db_record.get("name") or ""

        # Resolve table refs
        input_table_qns: list[str] = []
        input_table_records: list[dict[str, Any]] = []
        for _catalog, schema, table_name in _extract_table_refs(ast):
            resolved = _resolve_table(table_index, schema, table_name)
            if resolved is None:
                continue
            input_table_records.append(resolved)
            qn = _table_qualified_name(
                connection_qualified_name,
                db_name,
                resolved.get("schema") or schema,
                resolved.get("name") or table_name,
            )
            if qn not in input_table_qns:
                input_table_qns.append(qn)

        if not input_table_qns:
            # No tables resolved — the SQL likely references something not
            # in cached metadata (subquery alias, ad-hoc table). Count and
            # move on.
            failures += 1
            continue

        processes.append(
            _process_record(q, connection_qualified_name, input_table_qns, sql)
        )

        # Resolve column refs against the matched tables. Conservative:
        # only emit a ColumnProcess if we can resolve at least one column.
        input_column_qns: list[str] = []
        column_refs = _extract_column_refs(ast)
        if column_refs:
            # Build a column-name → table_qn lookup across matched tables
            col_lookup: dict[str, str] = {}
            for tbl in input_table_records:
                t_qn = _table_qualified_name(
                    connection_qualified_name,
                    db_name,
                    tbl.get("schema"),
                    tbl.get("name", ""),
                )
                for field in tbl.get("fields") or []:
                    fname = (field.get("name") or "").lower()
                    if fname and fname not in col_lookup:
                        col_lookup[fname] = t_qn

            for _table_qual, col_name in column_refs:
                key = col_name.lower()
                if key in col_lookup:
                    cqn = _column_qualified_name(col_lookup[key], col_name)
                    if cqn not in input_column_qns:
                        input_column_qns.append(cqn)

        if input_column_qns:
            column_processes.append(
                _column_process_record(
                    q, connection_qualified_name, input_column_qns, sql
                )
            )

    return processes, column_processes, failures
