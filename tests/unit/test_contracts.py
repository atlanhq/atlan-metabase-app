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

import pytest
from pydantic import ValidationError

from app.contracts import FilterInput, MetabaseInput


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


class TestCredentialsPayloadSafety:
    """``MetabaseInput.credentials`` is bounded, so the contract needs no opt-out.

    The field used to be ``list[dict[str, Any]] | dict[str, Any]`` behind
    ``allow_unbounded_fields=True``. ``Any`` is refused by payload safety
    regardless of ``MaxItems``, so the opt-out was the only thing keeping the
    contract importable — and it disabled the 2MB-payload guard for every
    other field too (ADR-0008). Both inline wire shapes must keep working
    now that the value type is the concrete ``CredentialValue`` union.
    """

    def test_contract_does_not_opt_out_of_payload_safety(self) -> None:
        assert getattr(MetabaseInput, "_allow_unbounded_fields", False) is False

    def test_inline_dict_credentials_accepted(self) -> None:
        model = MetabaseInput(credentials={"host": "h", "port": 3000, "tls": True})
        assert model.credentials == {"host": "h", "port": 3000, "tls": True}

    def test_inline_list_credentials_accepted(self) -> None:
        model = MetabaseInput(credentials=[{"key": "username", "value": "u"}])
        assert model.credentials == [{"key": "username", "value": "u"}]

    def test_default_is_empty_list(self) -> None:
        assert MetabaseInput().credentials == []

    def test_none_valued_credential_accepted(self) -> None:
        """``None`` is in ``CredentialValue`` — an unset optional must survive."""
        model = MetabaseInput(credentials={"password": None})
        assert model.credentials == {"password": None}

    def test_non_scalar_credential_value_rejected(self) -> None:
        """The narrowing is load-bearing, not cosmetic.

        A nested container is what made the field unbounded in the first
        place; it must now be refused rather than silently crossing a task
        boundary.
        """
        with pytest.raises(ValidationError):
            MetabaseInput(credentials={"nested": {"a": "b"}})  # type: ignore[dict-item]
