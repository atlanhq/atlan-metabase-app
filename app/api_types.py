"""Typed intermediate records bridging raw Metabase API dicts and pyatlan assets. 

The extract/process layer hands the transform layer dictionaries directly
loaded from Metabase API responses (plus a few enrichment fields appended
by ``app/extracts/process.py``). These typed records make the per-attribute
contract explicit: every field the asset mapper reads is declared here, with
its expected type.

Records are constructed via :func:`from_dict` factories that accept the raw
dict shape and pull out only the fields each mapper needs. Unknown keys are
ignored — the upstream record may carry many more Metabase API fields we
don't surface as Atlan attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CollectionRecord:
    """A Metabase collection enriched by ``generate_collections_map``."""

    id: Any  # int or the string "root"
    name: str
    description: str | None
    slug: str | None
    color: str | None
    namespace: str | None
    source_url: str | None
    is_personal: bool

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CollectionRecord:
        return cls(
            id=raw["id"],
            name=str(raw.get("name") or ""),
            description=raw.get("description"),
            slug=raw.get("slug"),
            color=raw.get("color"),
            namespace=raw.get("namespace"),
            source_url=raw.get("sourceURL"),
            # Metabase API marks personal collections via personal_owner_id.
            is_personal=raw.get("personal_owner_id") is not None,
        )


@dataclass(frozen=True)
class DashboardRecord:
    """A Metabase dashboard enriched by ``process_assets``."""

    id: Any
    name: str
    description: str | None
    source_url: str | None
    cards_count: int
    collection_id: Any | None
    collection_name: str | None
    certificate_status: str | None
    certificate_status_message: str | None
    source_created_at: int | None
    source_updated_at: int | None
    source_updated_by: str | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DashboardRecord:
        collection = raw.get("collection") or {}
        return cls(
            id=raw["id"],
            name=str(raw.get("name") or ""),
            description=raw.get("description"),
            source_url=raw.get("sourceURL"),
            cards_count=int(raw.get("cards_count") or 0),
            collection_id=collection.get("id"),
            collection_name=collection.get("name"),
            certificate_status=raw.get("certificate_status"),
            certificate_status_message=raw.get("certificate_status_message"),
            source_created_at=_to_millis(raw.get("created_at")),
            source_updated_at=_to_millis(raw.get("updated_at")),
            source_updated_by=raw.get("last_edit_info_user") or None,
        )


@dataclass(frozen=True)
class QuestionRecord:
    """A Metabase question enriched by ``process_assets``."""

    id: Any
    name: str
    description: str | None
    source_url: str | None
    metabase_query: str | None
    query_type: str | None
    metabase_database_name: str | None
    metabase_schema_name: str | None
    # Atlan-mapped SQL dialect (snowflake, redshift, postgres, h2, …).
    # Surfaced to QI via ``attributes.metabaseSourceEngine`` so the parser
    # routes by per-record vendor instead of the broken
    # ``vendorName = "metabase"`` default that collapsed to Oracle.
    metabase_source_engine: str | None
    collection_id: Any | None
    collection_name: str | None
    dashboard_count: int
    dashboard_ids: list[Any] = field(default_factory=list)
    certificate_status: str | None = None
    certificate_status_message: str | None = None
    source_created_at: int | None = None
    source_updated_at: int | None = None
    source_created_by: str | None = None
    source_updated_by: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionRecord:
        collection = raw.get("collection") or {}
        dashboards = raw.get("dashboards") or []
        dashboard_ids = [
            d["id"] for d in dashboards if isinstance(d, dict) and "id" in d
        ]
        return cls(
            id=raw["id"],
            name=str(raw.get("name") or ""),
            description=raw.get("description"),
            source_url=raw.get("sourceURL"),
            metabase_query=raw.get("metabase_query"),
            query_type=raw.get("query_type"),
            metabase_database_name=raw.get("metabase_database_name"),
            metabase_schema_name=raw.get("metabase_schema_name"),
            metabase_source_engine=raw.get("metabase_source_engine"),
            collection_id=collection.get("id"),
            collection_name=collection.get("name"),
            dashboard_count=len(dashboard_ids),
            dashboard_ids=dashboard_ids,
            certificate_status=raw.get("certificate_status"),
            certificate_status_message=raw.get("certificate_status_message"),
            source_created_at=_to_millis(raw.get("created_at")),
            source_updated_at=_to_millis(raw.get("updated_at")),
            source_created_by=raw.get("creator_id") and str(raw["creator_id"]) or None,
            source_updated_by=raw.get("last_edit_info_user") or None,
        )


@dataclass(frozen=True)
class BIProcessLineageRecord:
    """One question→dashboards lineage edge emitted by ``process_assets``.

    A single BIProcess instance per question that appears on at least one
    dashboard. ``question_id`` is used to construct the BIProcess's own
    qualifiedName; the question + dashboard refs are constructed by the
    mapper from the connection-qualified-name context.
    """

    name: str
    question_id: Any
    dashboard_ids: list[Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BIProcessLineageRecord:
        # process_assets emits `inputs`/`outputs` as Atlas refs already, but
        # the mapper rebuilds them from typed ids to keep the contract
        # symmetrical across mappers — refs in the wire format are then a
        # serialization detail, not part of the typed record.
        dashboard_ids: list[Any] = []
        for ref in raw.get("outputs") or []:
            qn = (ref.get("uniqueAttributes") or {}).get("qualifiedName") or ""
            if qn:
                dashboard_ids.append(qn.rsplit("/", 1)[-1])
        return cls(
            name=str(raw.get("name") or ""),
            question_id=raw["question_id"],
            dashboard_ids=dashboard_ids,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_millis(value: Any) -> int | None:
    """Best-effort coercion of Metabase's ISO-8601 timestamps to epoch ms.

    Metabase returns ``created_at`` / ``updated_at`` as ISO strings (e.g.
    ``"2024-03-04T11:22:33.456Z"``). Atlan attributes expect epoch
    milliseconds. Return ``None`` when parsing fails so the attribute stays
    unset rather than carrying a misleading value.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        from datetime import datetime, timezone

        try:
            # Metabase emits "...Z" — Python's fromisoformat accepts "+00:00".
            normalized = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None
