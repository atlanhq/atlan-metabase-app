"""Build ARS 2.0 Process + ColumnProcess records for Metabase BI lineage.

Metabase is a BI connector and its lineage is *cross-connector* — a
MetabaseQuestion's native SQL references tables in another connector's
connection (Postgres / Snowflake / BigQuery / …). The Atlan publish app's
Asset Resolution Service (ARS) resolves these cross-connector refs at
publish time.

This module emits records on the **ARS 2.0 contract** (modern). The
``arsIdentity`` schema is defined in publish-app's
``app/lib/partitioning/duckdb_sql.py::ARS_IDENTITY_STRUCT_SCHEMA`` —
publish-app's edge resolver consumes ``attributes.arsIdentity`` and
``attributes.arsNestedLookupFields`` directly without going through the
legacy-translator shim. Producers on the 1.0 contract (``arsEntityConfig``
+ ``arsAttributes`` + ``publishTransformationHandling``) were funnelled
through ``legacy_translator.py``; this connector skips that path entirely
by emitting 2.0 records inline.

Each record carries:

  attributes:
    name, qualifiedName, …scalar fields…
    inputs:  [ <ref with arsIdentity> ]
    outputs: [ <plain ObjectId or ref with arsIdentity> ]
    arsIdentity:
      components: {connectorType, databaseName, schemaName, tableName, …}
      matchTypeNames: ["Table", "View", …]    # types eligible for lookup
      fallbackQualifiedName: "<qn>"             # used when noMatchAction = use_fallback
      fallbackTypeName: "Table"                 # type for PartialObject creation
      noMatchAction: "use_fallback" | "create_partial" | "drop"
      lookupResultHandling: "pick_first"
      parentComponentsKeys: [...]               # for Column → Table parent lookup
      parentMatchTypeNames: ["Table", "View"]
      parentTypeNames: ["Table"]
    arsNestedLookupFields: ["inputs", "outputs"]   # tells resolver which fields contain nested refs

  relationshipAttributes:
    inputs: [...]    # mirror, required by Atlas wire format
    outputs: [...]

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
_CONNECTOR_NAME = "metabase"


def _truncate(s: str, max_len: int = _NAME_MAX_LEN) -> str:
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _hash(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _components(**kwargs: str) -> dict[str, str]:
    """Build an arsIdentity.components map, dropping empty values.

    The ARS resolver filters out empty/null component values at JOIN time
    (see ``legacy_translator.py::_legacy_pipe_components_json`` — only
    pairs with non-empty value survive). Emit only populated keys so the
    component set matches what the resolver expects.
    """
    return {k: v for k, v in kwargs.items() if v}


# ---------------------------------------------------------------------------
# Cross-connector Table reference (Process.inputs[])
# ---------------------------------------------------------------------------


def build_partial_table_ref(
    *,
    vendor_name: str,
    database: str,
    schema: str,
    table_name: str,
) -> dict[str, Any]:
    """Build a Table ref for use as a Process input.

    The ref carries an ``arsIdentity`` block on the ARS 2.0 contract.
    The publish-app resolver looks the table up by components; on miss
    (``noMatchAction = "create_partial"``) it creates a PartialObject
    with ``fallbackQualifiedName`` and points the lineage edge at that.

    Args:
        vendor_name: Source engine connector type (e.g. ``"snowflake"``).
            Omitted from the components map when empty so the resolver
            doesn't filter by connectorType.
        database, schema, table_name: 3-part source identifier parsed
            from the question's native SQL.
    """
    qn = "/".join(p for p in (database, schema, table_name) if p)
    return {
        "typeName": "Table",
        "attributes": {
            "name": table_name,
            "qualifiedName": qn,
            "arsIdentity": {
                "components": _components(
                    connectorType=vendor_name,
                    databaseName=database,
                    schemaName=schema,
                    tableName=table_name,
                ),
                "matchTypeNames": ["Table", "View"],
                "fallbackQualifiedName": qn,
                "fallbackTypeName": "Table",
                "noMatchAction": "create_partial",
                "lookupResultHandling": "pick_first",
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
    """Build a Column ref for use as a ColumnProcess input.

    Carries parent-table context via ``parentComponentsKeys`` so the
    resolver can locate the parent Table when synthesizing a PartialField
    on miss.
    """
    qn = "/".join(p for p in (database, schema, table_name, column_name) if p)
    return {
        "typeName": "Column",
        "attributes": {
            "name": column_name,
            "qualifiedName": qn,
            "arsIdentity": {
                "components": _components(
                    connectorType=vendor_name,
                    databaseName=database,
                    schemaName=schema,
                    tableName=table_name,
                    columnName=column_name,
                ),
                "matchTypeNames": ["Column"],
                "fallbackQualifiedName": qn,
                "fallbackTypeName": "Column",
                "noMatchAction": "create_partial",
                "lookupResultHandling": "pick_first",
                # Parent table context — required for PartialField creation
                # so the synthesized Column has a parent reference. The
                # resolver derives the parent's qualifiedName by joining
                # the components subset selected by parentComponentsKeys
                # against the same fallbackQualifiedName component shape;
                # it does not take a separate parentFallback field.
                "parentComponentsKeys": [
                    "connectorType",
                    "databaseName",
                    "schemaName",
                    "tableName",
                ],
                "parentMatchTypeNames": ["Table", "View"],
                "parentTypeNames": ["Table"],
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
    """Build a Process ARS 2.0 record: source_tables → MetabaseQuestion.

    Args:
        connection_qualified_name: ``default/metabase/<conn-id>`` — the
            Metabase connection's qualified name.
        connection_name: User-visible connection name (used in identity).
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
    p_hash = _hash(str(question_id), sql)
    process_qn = f"{connection_qualified_name}/question_tables/{question_id}/{p_hash}"
    process_name = _truncate(question_name or f"Question {question_id}")

    inputs = [build_partial_table_ref(**t) for t in source_tables]
    outputs = [
        {
            "typeName": "MetabaseQuestion",
            "uniqueAttributes": {"qualifiedName": question_qn},
        }
    ]

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
            "inputs": inputs,
            "outputs": outputs,
            # The Process itself doesn't get looked up (this connector
            # owns its qualifiedName); use_fallback short-circuits the
            # ARS lookup and uses ``fallbackQualifiedName`` directly.
            "arsIdentity": {
                "components": _components(
                    connectorType=_CONNECTOR_NAME,
                    connectionName=connection_name,
                    name=process_name,
                ),
                "matchTypeNames": ["Process"],
                "fallbackQualifiedName": process_qn,
                "fallbackTypeName": "Process",
                "noMatchAction": "use_fallback",
                "lookupResultHandling": "pick_first",
            },
            # Tells the resolver which fields contain nested refs that
            # need per-edge ARS resolution. ``outputs`` here is a plain
            # ObjectId (MetabaseQuestion already published by us), but
            # listing it is harmless — the resolver short-circuits refs
            # that have no nested arsIdentity.
            "arsNestedLookupFields": ["inputs", "outputs"],
        },
        # Atlas wire format requires inputs/outputs in relationshipAttributes
        # as well. See app/asset_mapper.py::serialize_entity for the
        # equivalent hoist pattern used for BIProcess.
        "relationshipAttributes": {
            "inputs": inputs,
            "outputs": outputs,
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
    """Build a ColumnProcess ARS 2.0 record.

    ``parent_process_hash`` must match the hash used in the corresponding
    :func:`build_process` call so the publish app can wire the column-level
    process under its parent table-level Process.
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

    inputs = [build_partial_column_ref(**c) for c in source_columns]
    outputs = [
        {
            "typeName": "MetabaseQuestion",
            "uniqueAttributes": {"qualifiedName": question_qn},
        }
    ]
    process_ref = {
        "typeName": "Process",
        "uniqueAttributes": {"qualifiedName": parent_process_qn},
    }

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
            "process": process_ref,
            "inputs": inputs,
            "outputs": outputs,
            "arsIdentity": {
                "components": _components(
                    connectorType=_CONNECTOR_NAME,
                    connectionName=connection_name,
                    name=cp_name,
                ),
                "matchTypeNames": ["ColumnProcess"],
                "fallbackQualifiedName": cp_qn,
                "fallbackTypeName": "ColumnProcess",
                "noMatchAction": "use_fallback",
                "lookupResultHandling": "pick_first",
            },
            "arsNestedLookupFields": ["inputs", "outputs"],
        },
        "relationshipAttributes": {
            "inputs": inputs,
            "outputs": outputs,
            "process": process_ref,
        },
    }


def process_hash(question_id: str | int, sql: str) -> str:
    """Public helper so callers can compute the same hash both build_*
    functions use, for cross-record consistency."""
    return _hash(str(question_id), sql)
