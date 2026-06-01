"""Pure functions mapping typed Metabase records to pyatlan_v9 assets.

Replaces the v2 ``MetabaseTransformer``/YAML/Daft pipeline. Each ``map_*``
function takes a typed record + connection context and returns the
populated pyatlan asset. Sync-stamp metadata (last_sync_*, tenant_id) is
applied via :func:`_apply_sync_metadata` so every mapper carries the same
shape.

All functions are pure: no I/O, no side effects, deterministic. Temporal
may replay them during retries; the activity boundary owns I/O.

Serialization uses :func:`serialize_entity` to emit the
``{typeName, status, attributes}`` shape the publish layer reads — same
shape the v2 transformer produced, so this is a drop-in swap.
"""

from __future__ import annotations

import json
from typing import Any

from pyatlan_v9.model.assets import (
    BIProcess,
    MetabaseCollection,
    MetabaseDashboard,
    MetabaseQuestion,
)
from pyatlan_v9.model.assets.metabase_related import (
    RelatedMetabaseCollection,
    RelatedMetabaseDashboard,
    RelatedMetabaseQuestion,
)

from app.api_types import (
    BIProcessLineageRecord,
    CollectionRecord,
    DashboardRecord,
    QuestionRecord,
)

# ---------------------------------------------------------------------------
# QN builders — single source of truth for every Metabase QN format
# ---------------------------------------------------------------------------

# Mirrors the YAML ``concat(connection_qualified_name, '/<segment>/', id)``
# templates. Keeping them as helpers (not f-strings inlined per mapper) means
# the BIProcess input/output refs can build the same QNs without drift.


def _collection_qn(connection_qn: str, collection_id: Any) -> str:
    return f"{connection_qn}/collections/{collection_id}"


def _dashboard_qn(connection_qn: str, dashboard_id: Any) -> str:
    return f"{connection_qn}/dashboards/{dashboard_id}"


def _question_qn(connection_qn: str, question_id: Any) -> str:
    return f"{connection_qn}/questions/{question_id}"


def _bi_process_qn(connection_qn: str, question_id: Any) -> str:
    return f"{connection_qn}/questions_dashboards/{question_id}"


# ---------------------------------------------------------------------------
# Shared sync-stamp helper
# ---------------------------------------------------------------------------


def _apply_sync_metadata(
    asset: Any,
    *,
    connector_name: str,
    connection_name: str,
    workflow_id: str,
    workflow_run_id: str,
    last_sync_run_at_ms: int,
    tenant_id: str,
) -> None:
    """Stamp the sync metadata every Metabase asset carries.

    The publish layer keys cache state off ``last_sync_run`` /
    ``last_sync_workflow_name`` and the connection identity fields, so this
    must be applied uniformly across all four mappers.
    """
    asset.connector_name = connector_name
    asset.connection_name = connection_name
    asset.last_sync_workflow_name = workflow_id
    asset.last_sync_run = workflow_run_id
    asset.last_sync_run_at = last_sync_run_at_ms
    asset.tenant_id = tenant_id


# ---------------------------------------------------------------------------
# Mappers — one per Atlan asset type
# ---------------------------------------------------------------------------


def map_collection(
    record: CollectionRecord,
    *,
    connection_qualified_name: str,
    connection_name: str,
    connector_name: str,
    workflow_id: str,
    workflow_run_id: str,
    last_sync_run_at_ms: int,
    tenant_id: str,
) -> MetabaseCollection:
    asset = MetabaseCollection(
        name=record.name,
        qualified_name=_collection_qn(connection_qualified_name, record.id),
        connection_qualified_name=connection_qualified_name,
    )
    if record.description is not None:
        asset.description = record.description
    if record.source_url is not None:
        asset.source_url = record.source_url
    if record.slug is not None:
        asset.metabase_slug = record.slug
    if record.color is not None:
        asset.metabase_color = record.color
    if record.namespace is not None:
        asset.metabase_namespace = record.namespace
    asset.metabase_is_personal_collection = record.is_personal
    _apply_sync_metadata(
        asset,
        connector_name=connector_name,
        connection_name=connection_name,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        last_sync_run_at_ms=last_sync_run_at_ms,
        tenant_id=tenant_id,
    )
    return asset


def map_dashboard(
    record: DashboardRecord,
    *,
    connection_qualified_name: str,
    connection_name: str,
    connector_name: str,
    workflow_id: str,
    workflow_run_id: str,
    last_sync_run_at_ms: int,
    tenant_id: str,
) -> MetabaseDashboard:
    asset = MetabaseDashboard(
        name=record.name,
        qualified_name=_dashboard_qn(connection_qualified_name, record.id),
        connection_qualified_name=connection_qualified_name,
    )
    if record.description is not None:
        asset.description = record.description
    if record.source_url is not None:
        asset.source_url = record.source_url
    if record.certificate_status is not None:
        asset.certificate_status = record.certificate_status
    if record.certificate_status_message is not None:
        asset.certificate_status_message = record.certificate_status_message
    if record.source_created_at is not None:
        asset.source_created_at = record.source_created_at
    if record.source_updated_at is not None:
        asset.source_updated_at = record.source_updated_at
    if record.source_updated_by is not None:
        asset.source_updated_by = record.source_updated_by
    asset.metabase_question_count = record.cards_count
    if record.collection_id is not None:
        collection_qn = _collection_qn(connection_qualified_name, record.collection_id)
        asset.metabase_collection_qualified_name = collection_qn
        if record.collection_name is not None:
            asset.metabase_collection_name = record.collection_name
        asset.metabase_collection = RelatedMetabaseCollection(
            qualified_name=collection_qn
        )
    _apply_sync_metadata(
        asset,
        connector_name=connector_name,
        connection_name=connection_name,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        last_sync_run_at_ms=last_sync_run_at_ms,
        tenant_id=tenant_id,
    )
    return asset


def map_question(
    record: QuestionRecord,
    *,
    connection_qualified_name: str,
    connection_name: str,
    connector_name: str,
    workflow_id: str,
    workflow_run_id: str,
    last_sync_run_at_ms: int,
    tenant_id: str,
) -> tuple[MetabaseQuestion, dict[str, Any]]:
    """Build a MetabaseQuestion plus the QI-specific extra attributes.

    Returns ``(asset, extras)``. ``extras`` carries Atlan custom attributes
    the platform's QueryIntelligenceNode reads via JSONPath on
    ``attributes.*``:

    - ``metabaseSourceDatabaseName`` — catalog scope for SQL parsing.
    - ``metabaseSourceSchemaName`` — schema scope for SQL parsing.
    - ``metabaseSourceEngine`` — per-query SQL dialect (snowflake,
      redshift, postgres, h2, …) read via ``vendorKey`` so QI picks the
      right parser. Without this, the contract's ``vendorName = "metabase"``
      falls through to the Oracle default and ``LIMIT n`` syntax explodes.

    None of these fields are in the pyatlan_v9 model, so the transform task
    injects them after :func:`serialize_entity`.
    """
    asset = MetabaseQuestion(
        name=record.name,
        qualified_name=_question_qn(connection_qualified_name, record.id),
        connection_qualified_name=connection_qualified_name,
    )
    if record.description is not None:
        asset.description = record.description
    if record.source_url is not None:
        asset.source_url = record.source_url
    if record.metabase_query is not None:
        asset.metabase_query = record.metabase_query
    if record.query_type is not None:
        asset.metabase_query_type = record.query_type
    if record.certificate_status is not None:
        asset.certificate_status = record.certificate_status
    if record.certificate_status_message is not None:
        asset.certificate_status_message = record.certificate_status_message
    if record.source_created_at is not None:
        asset.source_created_at = record.source_created_at
    if record.source_updated_at is not None:
        asset.source_updated_at = record.source_updated_at
    if record.source_created_by is not None:
        asset.source_created_by = record.source_created_by
    if record.source_updated_by is not None:
        asset.source_updated_by = record.source_updated_by
    asset.metabase_dashboard_count = record.dashboard_count

    if record.collection_id is not None:
        collection_qn = _collection_qn(connection_qualified_name, record.collection_id)
        asset.metabase_collection_qualified_name = collection_qn
        if record.collection_name is not None:
            asset.metabase_collection_name = record.collection_name
        asset.metabase_collection = RelatedMetabaseCollection(
            qualified_name=collection_qn
        )

    if record.dashboard_ids:
        asset.metabase_dashboards = [
            RelatedMetabaseDashboard(
                qualified_name=_dashboard_qn(connection_qualified_name, did)
            )
            for did in record.dashboard_ids
        ]

    _apply_sync_metadata(
        asset,
        connector_name=connector_name,
        connection_name=connection_name,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        last_sync_run_at_ms=last_sync_run_at_ms,
        tenant_id=tenant_id,
    )
    extras: dict[str, Any] = {}
    if record.metabase_database_name is not None:
        extras["metabaseSourceDatabaseName"] = record.metabase_database_name
    if record.metabase_schema_name is not None:
        extras["metabaseSourceSchemaName"] = record.metabase_schema_name
    if record.metabase_source_engine:
        extras["metabaseSourceEngine"] = record.metabase_source_engine
    return asset, extras


def map_bi_process(
    record: BIProcessLineageRecord,
    *,
    connection_qualified_name: str,
    connection_name: str,
    connector_name: str,
    workflow_id: str,
    workflow_run_id: str,
    last_sync_run_at_ms: int,
    tenant_id: str,
) -> BIProcess:
    asset = BIProcess(
        name=record.name,
        qualified_name=_bi_process_qn(connection_qualified_name, record.question_id),
        connection_qualified_name=connection_qualified_name,
    )
    asset.inputs = [
        RelatedMetabaseQuestion(
            qualified_name=_question_qn(connection_qualified_name, record.question_id)
        )
    ]
    asset.outputs = [
        RelatedMetabaseDashboard(
            qualified_name=_dashboard_qn(connection_qualified_name, did)
        )
        for did in record.dashboard_ids
    ]
    _apply_sync_metadata(
        asset,
        connector_name=connector_name,
        connection_name=connection_name,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        last_sync_run_at_ms=last_sync_run_at_ms,
        tenant_id=tenant_id,
    )
    return asset


# ---------------------------------------------------------------------------
# Serialization — preserves the v2 wire format the publish layer consumes
# ---------------------------------------------------------------------------


def serialize_entity(
    asset: Any, extra_attributes: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Encode a pyatlan_v9 asset to the JSON shape the publish layer reads.

    Decodes the asset's canonical ``to_nested_bytes`` output back through
    json — that's the form the publish-app's ARS resolver expects (with
    ``attributes`` and ``relationshipAttributes`` top-level keys). Extras
    (e.g. Atlan custom attributes not modelled in pyatlan_v9) are merged
    into ``attributes``.
    """
    nested = json.loads(asset.to_nested_bytes().decode("utf-8"))
    attrs = dict(nested.get("attributes") or {})
    if extra_attributes:
        attrs.update(extra_attributes)
    out: dict[str, Any] = {
        "typeName": nested.get("typeName"),
        "status": "ACTIVE",
        "attributes": attrs,
    }
    rel = nested.get("relationshipAttributes") or {}
    # BIProcess lineage refs must surface as inputs/outputs on attributes —
    # the v2 YAML put them there inline and the publish-app's ARS resolver
    # reads from attributes.inputs/outputs. Hoisting from relationshipAttributes
    # preserves that contract while keeping the typed pyatlan asset canonical.
    if out["typeName"] == "BIProcess":
        for key in ("inputs", "outputs"):
            value = rel.get(key)
            if value is not None:
                out["attributes"][key] = value
    # Keep canonical relationshipAttributes for the publish layer's
    # relationship updates (Question→Collection, Question→Dashboards, etc).
    if rel:
        out["relationshipAttributes"] = rel
    return out
