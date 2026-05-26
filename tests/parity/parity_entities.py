"""Shared entity metadata for parity generation, comparison, and reporting.

Metabase produces six asset types (no SQL Database/Schema/Table/Column shape
the MSSQL gold reference uses). The ``ENTITY_TYPES`` list mirrors the six
typename folders the connector writes under ``transformed/``:

    METABASECOLLECTION / METABASEDASHBOARD / METABASEQUESTION /
    BIPROCESS / PROCESS / COLUMNPROCESS

Lower-case keys here so the parity output layout
(``output/<scenario>/v3/<entity>.jsonl``) and the summarizer's table
headers read cleanly. ``ENTITY_TO_TYPENAME`` maps back to the upper-case
Atlas typeNames the connector emits inside JSONL records.
"""

from __future__ import annotations

ENTITY_TYPES = (
    "collection",
    "dashboard",
    "question",
    "biprocess",
    "process",
    "columnprocess",
)

RESULT_COUNT_FIELDS = {
    "collection": "collections_extracted",
    "dashboard": "dashboards_extracted",
    "question": "questions_extracted",
    "biprocess": "biprocesses_extracted",
    "process": "processes_extracted",
    "columnprocess": "columnprocesses_extracted",
}

RECORD_COUNT_FIELDS = tuple(RESULT_COUNT_FIELDS[entity] for entity in ENTITY_TYPES)

# Atlas typeName for each entity. Comparisons key off this rather than the
# emitting filename because typeName is the stable contract; folder names
# may change without breaking the diff.
ENTITY_TO_TYPENAME = {
    "collection": "MetabaseCollection",
    "dashboard": "MetabaseDashboard",
    "question": "MetabaseQuestion",
    "biprocess": "BIProcess",
    "process": "Process",
    "columnprocess": "ColumnProcess",
}
