"""Unit tests for app.contracts — typed Input/Output contracts.

# pyright: reportArgumentType=false
The validator tests deliberately pass off-type values (strings where
``CollectionFilter`` expects a dict) to verify the ``mode="before"``
coercion path. Suppress pyright's argument-type checks file-wide so the
tests can exercise the wire shape the validator is designed to handle.
"""

# pyright: reportArgumentType=false

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from app.contracts import FilterInput, MetabaseInput, _coerce_collection_filter


class TestCollectionFilterCoercion:
    """Recover the workflow from upstream object→string serialization.

    The frontend → AE → SDK pipeline can hand us the literal string
    "[object Object]" for ``include_collections`` / ``exclude_collections``
    when a JS object got stringified via ``String(value)`` instead of
    ``JSON.stringify(value)``. The validator coerces that (and JSON-encoded
    variants) back to a dict so the workflow doesn't fail validation on an
    upstream defect. See app/contracts.py for the diagnostic.
    """

    @pytest.mark.parametrize("field", ["include_collections", "exclude_collections"])
    def test_stringified_object_sentinel_coerced_to_empty_dict(
        self, field: str
    ) -> None:
        kwargs: dict[str, Any] = {field: "[object Object]"}
        model = MetabaseInput(**kwargs)
        assert getattr(model, field) == {}

    @pytest.mark.parametrize("field", ["include_collections", "exclude_collections"])
    def test_empty_string_coerced_to_empty_dict(self, field: str) -> None:
        kwargs: dict[str, Any] = {field: ""}
        model = MetabaseInput(**kwargs)
        assert getattr(model, field) == {}

    @pytest.mark.parametrize("field", ["include_collections", "exclude_collections"])
    def test_json_encoded_dict_coerced_to_dict(self, field: str) -> None:
        kwargs: dict[str, Any] = {field: '{"42": {}}'}
        model = MetabaseInput(**kwargs)
        assert "42" in getattr(model, field)

    @pytest.mark.parametrize("field", ["include_collections", "exclude_collections"])
    def test_dict_passes_through_unchanged(self, field: str) -> None:
        kwargs: dict[str, Any] = {field: {"7": {}}}
        model = MetabaseInput(**kwargs)
        assert "7" in getattr(model, field)

    def test_filter_input_applies_same_coercion(self) -> None:
        kwargs: dict[str, Any] = {"exclude_collections": "[object Object]"}
        model = FilterInput(**kwargs)
        assert model.exclude_collections == {}


class TestCollectionFilterDiagnostics:
    """The coercion warnings are the only trace of the upstream serializer
    bug — pin the exact message + args so the diagnostic (and its pointer to
    the contracts.py write-up) can't silently rot."""

    def test_sentinel_coercion_warns_with_exact_diagnostic(self) -> None:
        with mock.patch("app.contracts._logger") as logger:
            assert _coerce_collection_filter("[object Object]") == {}
        logger.warning.assert_called_once_with(
            "Coerced stringified collection filter to empty dict "
            "(upstream serializer emitted %r). This is a workaround "
            "for a frontend/AE substitution bug — see contracts.py.",
            "[object Object]",
        )

    def test_invalid_json_passes_through_and_warns_with_exact_diagnostic(
        self,
    ) -> None:
        with mock.patch("app.contracts._logger") as logger:
            assert _coerce_collection_filter("{not json") == "{not json"
        logger.warning.assert_called_once_with(
            "Collection filter %r is not valid JSON; passing through as-is",
            "{not json",
            exc_info=True,
        )
