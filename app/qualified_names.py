"""Single source of truth for every Metabase ``qualifiedName`` grammar.

Atlan's ``qualifiedName`` is the identity primitive for every asset (dedup,
lineage, linking). Building it by hand with an f-string scatters the grammar
(segments, order, separator) across the connector — the asset mappers, the
lineage builder, and the BIProcess transformer records would each re-encode it,
so a single grammar change would break them independently. Centralising the
grammar here keeps one definition per QN shape.

This lives in its own module (not ``asset_mapper``) so the lineage/transform
layer can import the grammar without depending on the asset mappers.

The Metabase asset QNs (:func:`collection_qn`, :func:`dashboard_qn`,
:func:`question_qn`) are derived from the pyatlan_v9 ``.creator()`` factories,
so pyatlan owns their grammar; they serve callers that need the string for a
reference to an asset they are not building. :func:`bi_process_qn` and the
lineage-process QNs (:func:`process_qn`, :func:`column_process_qn`) are bespoke
grammars with no pyatlan asset factory, so they carry a justified P028
suppression.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyatlan_v9.model.assets import (
    MetabaseCollection,
    MetabaseDashboard,
    MetabaseQuestion,
)

# ---------------------------------------------------------------------------
# Metabase asset qualifiedNames
# ---------------------------------------------------------------------------


# Derived via ``.creator()`` and memoized, the same way atlan-mysql-app derives
# its parent QNs: the same collection / dashboard / question QN is re-derived
# for every child record and lineage edge that references it, so building a
# throwaway asset per call just to read ``qualified_name`` would be real waste.
#
# The creators key the qualifiedName on ``metabase_id`` alone; ``name`` is a
# required, non-blank argument that plays no part in it. The caller only has the
# id of an asset it is referencing, so the id is passed as the name too.


@lru_cache(maxsize=4096)
def collection_qn(connection_qn: str, collection_id: Any) -> str:
    qn = MetabaseCollection.creator(
        name=str(collection_id),
        connection_qualified_name=connection_qn,
        metabase_id=str(collection_id),
    ).qualified_name
    assert isinstance(qn, str)
    return qn


@lru_cache(maxsize=4096)
def dashboard_qn(connection_qn: str, dashboard_id: Any) -> str:
    qn = MetabaseDashboard.creator(
        name=str(dashboard_id),
        connection_qualified_name=connection_qn,
        metabase_id=str(dashboard_id),
    ).qualified_name
    assert isinstance(qn, str)
    return qn


@lru_cache(maxsize=4096)
def question_qn(connection_qn: str, question_id: Any) -> str:
    qn = MetabaseQuestion.creator(
        name=str(question_id),
        connection_qualified_name=connection_qn,
        metabase_id=str(question_id),
    ).qualified_name
    assert isinstance(qn, str)
    return qn


def bi_process_qn(connection_qn: str, question_id: Any) -> str:
    # conformance: ignore[P028] bespoke BIProcess qualifiedName (questions_dashboards/{id}) — no pyatlan_v9 creator owns this grammar (the string appears nowhere in pyatlan_v9); centralised here as the single source of truth.
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
