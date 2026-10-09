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
and the lineage-process QNs (:func:`process_qn`, :func:`column_process_qn`)
from ``Process`` / ``ColumnProcess.generate_qualified_name``, so pyatlan owns
their grammar; they serve callers that need the string for a reference to an
asset they are not building. The BIProcess qualifiedName has no helper here:
``map_bi_process`` builds the asset with ``BIProcess.creator``, which owns it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyatlan_v9.model.assets import (
    ColumnProcess,
    MetabaseCollection,
    MetabaseDashboard,
    MetabaseQuestion,
    Process,
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


# ---------------------------------------------------------------------------
# Lineage-process qualifiedNames
# ---------------------------------------------------------------------------


# Derived via pyatlan_v9's ``generate_qualified_name`` with an explicit
# ``process_id``, which owns the ``{connection_qn}/{process_id}`` grammar. On
# that path pyatlan returns before reading ``inputs`` / ``outputs`` / ``parent``,
# but still requires them non-empty; the caller only has the ids of a process it
# is building or referencing as a raw ARS record, so placeholders stand in, the
# same trade as passing the id as ``name`` above.
_UNUSED_REFS: list[Any] = [None]


def process_qn(connection_qn: str, question_id: Any, process_hash: str) -> str:
    return Process.generate_qualified_name(
        name=str(question_id),
        connection_qualified_name=connection_qn,
        inputs=_UNUSED_REFS,
        outputs=_UNUSED_REFS,
        process_id=f"question_tables/{question_id}/{process_hash}",
    )


def column_process_qn(connection_qn: str, question_id: Any, cp_hash: str) -> str:
    return ColumnProcess.generate_qualified_name(
        name=str(question_id),
        connection_qualified_name=connection_qn,
        inputs=_UNUSED_REFS,
        outputs=_UNUSED_REFS,
        parent=_UNUSED_REFS,
        process_id=f"question_columns/{question_id}/{cp_hash}",
    )
