"""Build ARS-compatible Process + ColumnProcess records for Metabase BI lineage.

Metabase is a BI connector and its lineage is *cross-connector* — a
MetabaseQuestion's native SQL references tables in another connector's
connection (Postgres / Snowflake / BigQuery / …). The Atlan publish app's
Asset Resolution Service (ARS) resolves these cross-connector refs at
publish time via PARTIAL_OBJECT / PARTIAL_FIELD entity configs.

Pattern mirrors ``atlan-qlik-sense-app/app/lineage/ars_builder.py``;
identity construction follows the canonical
``connectorName|connectionName|databaseName|schemaName|tableName`` shape
documented in the qlik-sense ARS-patterns reference.

This module is consumed by the ``extract_lineage`` @entrypoint on
:class:`MetabaseApp`. The records returned here are written as NDJSON to
``$.extract-lineage.outputs.lineage_stage_prefix`` and consumed by the
``LineagePublishNode`` (atlan-publish-app in lineage mode).
"""

from __future__ import annotations

import hashlib
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NAME_MAX_LEN = 10000
_WILDCARD = "*"
_CONNECTOR_NAME = "metabase"


def _truncate(s: str, max_len: int = _NAME_MAX_LEN) -> str:
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _hash(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _identity(*parts: str) -> str:
    """Build an ARS identity string with wildcards for unknown components."""
    return "|".join(p or _WILDCARD for p in parts)


# ---------------------------------------------------------------------------
# PARTIAL_OBJECT — external Table reference
# ---------------------------------------------------------------------------


def build_partial_table_ref(
    *,
    vendor_name: str,
    database: str,
    schema: str,
    table_name: str,
) -> dict[str, Any]:
    """Build a PARTIAL_OBJECT Table reference for use as a Process input.

    ARS resolves this against any connection matching the identity pattern;
    when no match is found it creates a stub PartialObject so the lineage
    edge survives.

    Args:
        vendor_name: Source engine (e.g. ``"postgres"``). Pass ``""`` if
            unknown — ARS treats it as a wildcard.
        database, schema, table_name: The 3-part source identifier parsed
            out of the question's native SQL (e.g. ``"testdata"``,
            ``"analytics"``, ``"customers"``).
    """
    qn = "/".join(p for p in (database, schema, table_name) if p)
    return {
        "typeName": "Table",
        "attributes": {
            "name": table_name,
            "qualifiedName": qn,
            "arsEntityConfig": {
                "publishTransformationHandling": "PARTIAL_OBJECT",
                "lookupResultHandling": "PICK_FIRST",
                "isRelationship": True,
                "skipLookup": False,
                "isTableViewAgnostic": True,
            },
            "arsAttributes": {
                "identity": _identity(vendor_name, "", database, schema, table_name),
                "identityPattern": (
                    "connectorName|connectionName|databaseName|schemaName|tableName"
                ),
                "identityDelimiter": "|",
                "fallbackQualifiedName": qn,
                "fallbackTypeName": "Table",
            },
        },
    }


def build_partial_column_ref(
    *,
    vendor_name: str,
    database: str,
    schema: str,
    table_name: str,
    column_name: str,
) -> dict[str, Any]:
    """Build a PARTIAL_FIELD Column reference for use as a ColumnProcess input."""
    qn = "/".join(p for p in (database, schema, table_name, column_name) if p)
    parent_qn = "/".join(p for p in (database, schema, table_name) if p)
    return {
        "typeName": "Column",
        "attributes": {
            "name": column_name,
            "qualifiedName": qn,
            "arsEntityConfig": {
                "publishTransformationHandling": "PARTIAL_FIELD",
                "lookupResultHandling": "PICK_FIRST",
                "isRelationship": True,
                "skipLookup": False,
            },
            "arsAttributes": {
                "identity": _identity(
                    vendor_name, "", database, schema, table_name, column_name
                ),
                "identityPattern": (
                    "connectorName|connectionName|databaseName|schemaName"
                    "|tableName|columnName"
                ),
                "identityDelimiter": "|",
                "fallbackQualifiedName": qn,
            },
            "arsParentEntityConfig": {
                "publishTransformationHandling": "PARTIAL_OBJECT",
                "lookupResultHandling": "PICK_FIRST",
                "isRelationship": True,
                "skipLookup": False,
                "isTableViewAgnostic": True,
            },
            "arsParentAttributes": {
                "identity": _identity(vendor_name, "", database, schema, table_name),
                "identityPattern": (
                    "connectorName|connectionName|databaseName|schemaName|tableName"
                ),
                "identityDelimiter": "|",
                "fallbackQualifiedName": parent_qn,
                "fallbackTypeName": "Table",
            },
        },
    }


# ---------------------------------------------------------------------------
# Process — MetabaseQuestion <— source tables
# ---------------------------------------------------------------------------


def build_process(
    *,
    connection_qualified_name: str,
    connection_name: str,
    question_id: str | int,
    question_name: str,
    sql: str,
    source_tables: list[dict[str, str]],
    tenant_id: str = "default",
) -> dict[str, Any] | None:
    """Build a Process ARS record: source_tables → MetabaseQuestion.

    Args:
        connection_qualified_name: ``default/metabase/<conn-id>`` — the
            Metabase connection's qualified name.
        connection_name: User-visible connection name (used in ARS identity).
        question_id: Metabase question id.
        question_name: Question name (used as Process display name).
        sql: The captured native SQL (rendered into the Process ``sql``
            attribute for human inspection).
        source_tables: List of dicts with keys ``vendor_name``, ``database``,
            ``schema``, ``table_name``. Empty list → no Process record.
        tenant_id: Atlan tenant id (default ``"default"``).

    Returns ``None`` when ``source_tables`` is empty (no upstream tables
    means no meaningful lineage edge).
    """
    if not source_tables:
        return None

    question_qn = f"{connection_qualified_name}/questions/{question_id}"
    process_hash = _hash(str(question_id), sql)
    process_qn = (
        f"{connection_qualified_name}/question_tables/{question_id}/{process_hash}"
    )
    process_name = _truncate(question_name or f"Question {question_id}")

    return {
        "typeName": "Process",
        "status": "ACTIVE",
        "attributes": {
            "name": process_name,
            "qualifiedName": process_qn,
            "connectorName": _CONNECTOR_NAME,
            "connectionName": connection_name,
            "connectionQualifiedName": connection_qualified_name,
            "tenantId": tenant_id,
            "sql": sql,
            "inputs": [build_partial_table_ref(**t) for t in source_tables],
            "outputs": [
                {
                    "typeName": "MetabaseQuestion",
                    "uniqueAttributes": {"qualifiedName": question_qn},
                }
            ],
            "arsEntityConfig": {
                "publishTransformationHandling": "LINEAGE_ASSET",
                "lookupResultHandling": "PICK_FIRST",
                "isRelationship": False,
                "skipLookup": True,
            },
            "arsAttributes": {
                "identity": _identity(_CONNECTOR_NAME, connection_name, process_name),
                "identityPattern": "connectorName|connectionName|name",
                "identityDelimiter": "|",
                "fallbackQualifiedName": process_qn,
                "fallbackQualifiedNameDelimiter": "/",
            },
        },
    }


# ---------------------------------------------------------------------------
# ColumnProcess — MetabaseQuestion <— source columns
# ---------------------------------------------------------------------------


def build_column_process(
    *,
    connection_qualified_name: str,
    connection_name: str,
    question_id: str | int,
    question_name: str,
    sql: str,
    source_columns: list[dict[str, str]],
    parent_process_hash: str,
    tenant_id: str = "default",
) -> dict[str, Any] | None:
    """Build a ColumnProcess ARS record: source_columns → MetabaseQuestion.

    ``parent_process_hash`` must match the hash used in the corresponding
    :func:`build_process` call so the publish app can wire the column-level
    process under its parent table-level Process.

    Args:
        source_columns: List of dicts with keys ``vendor_name``, ``database``,
            ``schema``, ``table_name``, ``column_name``. Empty list → no record.
    """
    if not source_columns:
        return None

    question_qn = f"{connection_qualified_name}/questions/{question_id}"
    parent_process_qn = (
        f"{connection_qualified_name}/question_tables/{question_id}/"
        f"{parent_process_hash}"
    )
    cp_hash = _hash(str(question_id), sql, "column")
    cp_qn = f"{connection_qualified_name}/question_columns/{question_id}/{cp_hash}"
    cp_name = _truncate(question_name or f"Question {question_id} columns")

    return {
        "typeName": "ColumnProcess",
        "status": "ACTIVE",
        "attributes": {
            "name": cp_name,
            "qualifiedName": cp_qn,
            "connectorName": _CONNECTOR_NAME,
            "connectionName": connection_name,
            "connectionQualifiedName": connection_qualified_name,
            "tenantId": tenant_id,
            "sql": sql,
            "process": {
                "typeName": "Process",
                "uniqueAttributes": {"qualifiedName": parent_process_qn},
            },
            "inputs": [build_partial_column_ref(**c) for c in source_columns],
            "outputs": [
                {
                    "typeName": "MetabaseQuestion",
                    "uniqueAttributes": {"qualifiedName": question_qn},
                }
            ],
            "arsEntityConfig": {
                "publishTransformationHandling": "LINEAGE_ASSET",
                "lookupResultHandling": "PICK_FIRST",
                "isRelationship": False,
                "skipLookup": True,
            },
            "arsAttributes": {
                "identity": _identity(_CONNECTOR_NAME, connection_name, cp_name),
                "identityPattern": "connectorName|connectionName|name",
                "identityDelimiter": "|",
                "fallbackQualifiedName": cp_qn,
                "fallbackQualifiedNameDelimiter": "/",
            },
        },
    }


def process_hash(question_id: str | int, sql: str) -> str:
    """Public helper so callers can compute the same hash both build_*
    functions use, for cross-record consistency."""
    return _hash(str(question_id), sql)
