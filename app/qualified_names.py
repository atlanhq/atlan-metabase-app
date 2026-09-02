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


# The three grammars below are byte-identical to the ones pyatlan_v9's own
# creators build — MetabaseCollection.creator (metabase_collection.py:328),
# MetabaseDashboard.creator (metabase_dashboard.py:335) and
# MetabaseQuestion.creator (metabase_question.py:343) — so asset identity here
# already matches the pyatlan-owned grammar; these are not a divergent
# invention. Verify that pairing before editing either side.
#
# Not routed through those creators, for two reasons. Most callers need the
# qualifiedName STRING, not an asset: a parent reference
# (`metabase_collection_qualified_name`), a Related*(qualified_name=...) edge,
# or a "qualifiedName" key in ARS JSON — and `.creator()` demands a `name` the
# caller does not have for an asset it is only referencing. And at the four
# sites that DO construct assets, `.creator()` calls
# validate_required_fields, which raises on a blank name; api_types.py defaults
# a missing Metabase name to "" on purpose, so adopting the creator there would
# convert a source data quirk into a failed run.


def collection_qn(connection_qn: str, collection_id: Any) -> str:
    # conformance: ignore[P028] matches MetabaseCollection.creator's grammar byte-for-byte (pyatlan_v9 metabase_collection.py:328); callers need the string, not an asset — see the note above.
    return f"{connection_qn}/collections/{collection_id}"


def dashboard_qn(connection_qn: str, dashboard_id: Any) -> str:
    # conformance: ignore[P028] matches MetabaseDashboard.creator's grammar byte-for-byte (pyatlan_v9 metabase_dashboard.py:335); callers need the string, not an asset — see the note above.
    return f"{connection_qn}/dashboards/{dashboard_id}"


def question_qn(connection_qn: str, question_id: Any) -> str:
    # conformance: ignore[P028] matches MetabaseQuestion.creator's grammar byte-for-byte (pyatlan_v9 metabase_question.py:343); callers need the string, not an asset — see the note above.
    return f"{connection_qn}/questions/{question_id}"


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
