"""Single source of truth for every Metabase ``qualifiedName`` grammar.

Atlan's ``qualifiedName`` is the identity primitive for every asset (dedup,
lineage, linking). Building it by hand with an f-string scatters the grammar
(segments, order, separator) across the connector — the asset mappers, the
lineage builder, and the BIProcess transformer records would each re-encode it,
so a single grammar change would break them independently. Centralising the
grammar here keeps one definition per QN shape.

This lives in its own module (not ``asset_mapper``) so the lineage/transform
layer can import the grammar without pulling in the pyatlan asset-construction
imports that ``asset_mapper`` carries.

The Metabase asset QNs (:func:`collection_qn`, :func:`dashboard_qn`,
:func:`question_qn`, :func:`bi_process_qn`) still trip conformance P028 because
pyatlan_v9 ships no ``.creator()`` for the Metabase asset family yet (see
BLDX-1558 / atlan-python#975); once it does, these become thin wrappers over the
creators. The lineage-process QNs (:func:`process_qn`,
:func:`column_process_qn`) are a bespoke ARS identity grammar with a content
hash — there is no pyatlan asset factory for them — so they carry a justified
P028 suppression.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Metabase asset qualifiedNames
# ---------------------------------------------------------------------------


def collection_qn(connection_qn: str, collection_id: Any) -> str:
    return f"{connection_qn}/collections/{collection_id}"


def dashboard_qn(connection_qn: str, dashboard_id: Any) -> str:
    return f"{connection_qn}/dashboards/{dashboard_id}"


def question_qn(connection_qn: str, question_id: Any) -> str:
    return f"{connection_qn}/questions/{question_id}"


def bi_process_qn(connection_qn: str, question_id: Any) -> str:
    return f"{connection_qn}/questions_dashboards/{question_id}"


# ---------------------------------------------------------------------------
# Lineage-process qualifiedNames (bespoke ARS identity — no pyatlan creator)
# ---------------------------------------------------------------------------


def process_qn(connection_qn: str, question_id: Any, process_hash: str) -> str:
    # conformance: ignore[P028] bespoke lineage-Process qualifiedName (question_tables/{id}/{hash}) — an ARS identity with a content hash, not a pyatlan-owned asset grammar; centralised here as the single source of truth.
    return f"{connection_qn}/question_tables/{question_id}/{process_hash}"


def column_process_qn(connection_qn: str, question_id: Any, cp_hash: str) -> str:
    # conformance: ignore[P028] bespoke lineage-ColumnProcess qualifiedName (question_columns/{id}/{hash}) — an ARS identity with a content hash, not a pyatlan-owned asset grammar; centralised here as the single source of truth.
    return f"{connection_qn}/question_columns/{question_id}/{cp_hash}"
