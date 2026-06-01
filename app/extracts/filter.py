"""Metabase filter functions for collections, dashboards, and questions.

Pure filter functions — no API calls, no I/O.  These are called by the
``filter_data`` Temporal activity in ``app/activities/metadata_extraction.py``
to scope the raw extracted data to the collections the user selected via the
``include-collections`` / ``exclude-collections`` workflow parameters.

Legacy reference:
    marketplace_scripts/marketplace_scripts/metabase/filter.py
"""

import json
from typing import Any, Dict, List, Set

from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)


def parse_filter_arg(value: Any) -> Dict:
    """Parse a filter arg that may be a JSON string, dict, or None.

    Args:
        value: The raw filter value from workflow args.  May be a JSON string
            (e.g. ``'{"1": "My Collection"}'``), a plain dict, an empty string,
            or ``None``.

    Returns:
        A plain Python dict.  Returns ``{}`` for any falsy or unparseable input.
    """
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def filter_collections(
    collections: List[Dict],
    include_collections: Any = None,
    exclude_collections: Any = None,
) -> List[Dict]:
    """Filter collections by include/exclude collection ID maps.

    Mirrors the legacy ``generate_collections_map`` logic:

    - A collection is *excluded* if its string id is a key in
      ``exclude_collections``.
    - A collection is *excluded* if ``include_collections`` is non-empty and
      its string id is **not** a key in ``include_collections``.
    - When both maps are empty every collection passes through unchanged.

    The root collection has id ``"root"`` (string).  Numeric ids are
    normalised to strings for the comparison.

    Args:
        collections: List of raw collection dicts from the extraction stage.
            Each dict must contain at minimum an ``"id"`` field (int or the
            string ``"root"``).
        include_collections: JSON string or dict ``{id: name}`` of collection
            IDs to include.  An empty / falsy value means "include all".
        exclude_collections: JSON string or dict ``{id: name}`` of collection
            IDs to exclude.

    Returns:
        Filtered list of collection dicts.
    """
    include_map = parse_filter_arg(include_collections)
    exclude_map = parse_filter_arg(exclude_collections)

    # Stringify keys so comparisons are consistent regardless of whether the
    # workflow stored them as integers or strings.
    include_keys: Set[str] = {str(k) for k in include_map.keys()}
    exclude_keys: Set[str] = {str(k) for k in exclude_map.keys()}

    # When no filters are active, pass everything through.
    if not include_keys and not exclude_keys:
        return collections

    result: List[Dict] = []
    for collection in collections:
        cid = str(collection.get("id", "root") or "root")

        # Exclude takes precedence (matches legacy behaviour).
        if cid in exclude_keys:
            continue

        if include_keys and cid not in include_keys:
            continue

        result.append(collection)

    logger.info(
        "filter_collections: %d → %d (include=%s, exclude=%s)",
        len(collections),
        len(result),
        list(include_keys) if include_keys else "all",
        list(exclude_keys) if exclude_keys else "none",
    )
    return result


def filter_dashboards(
    dashboards: List[Dict],
    accepted_collection_ids: Set[str],
) -> List[Dict]:
    """Filter dashboards to those whose collection is in *accepted_collection_ids*.

    Mirrors the legacy ``filter_assets`` dashboard loop:

        collection_id = dashboard['collection_id'] if dashboard['collection_id'] else 'root'
        if collection_id not in collections: continue

    Note: collection ids are normalised to strings (matching
    :func:`build_accepted_collection_ids`) so that ``"1"`` and ``1`` are
    treated identically.

    Args:
        dashboards: List of raw dashboard dicts.  Each must contain a
            ``"collection_id"`` field (int or ``None`` for the root collection).
        accepted_collection_ids: Set of **string** collection ids that passed
            the :func:`filter_collections` stage.  An *empty* set means no
            filtering — every dashboard is kept (e.g. when the user provided
            no include/exclude filters).

    Returns:
        Filtered list of dashboard dicts.
    """
    if not accepted_collection_ids:
        return dashboards

    result: List[Dict] = []
    for dashboard in dashboards:
        cid = str(dashboard.get("collection_id", "root") or "root")
        if cid in accepted_collection_ids:
            result.append(dashboard)

    logger.info("filter_dashboards: %d → %d", len(dashboards), len(result))
    return result


def filter_questions(
    questions: List[Dict],
    accepted_collection_ids: Set[str],
) -> List[Dict]:
    """Filter questions (cards) to those whose collection is in *accepted_collection_ids*.

    Mirrors the legacy ``filter_assets`` questions loop:

        collection_id = question['collection_id'] if question['collection_id'] else 'root'
        if collection_id not in collections: continue

    Args:
        questions: List of raw question dicts.  Each must contain a
            ``"collection_id"`` field (int or ``None``).
        accepted_collection_ids: Set of **string** collection ids from the
            :func:`build_accepted_collection_ids` helper.  An *empty* set
            means no filtering — every question is kept.

    Returns:
        Filtered list of question dicts.
    """
    if not accepted_collection_ids:
        return questions

    result: List[Dict] = []
    for question in questions:
        cid = str(question.get("collection_id", "root") or "root")
        if cid in accepted_collection_ids:
            result.append(question)

    logger.info("filter_questions: %d → %d", len(questions), len(result))
    return result


def build_accepted_collection_ids(filtered_collections: List[Dict]) -> Set[str]:
    """Build a set of accepted collection IDs from the post-filter collection list.

    After :func:`filter_collections` has been applied, pass its output to this
    helper to obtain the set of string IDs that dashboards and questions can be
    checked against.

    Args:
        filtered_collections: List of collection dicts that survived the
            include/exclude filter.

    Returns:
        Set of string collection ids (e.g. ``{"1", "4", "root"}``).
    """
    return {str(c.get("id", "root") or "root") for c in filtered_collections}
