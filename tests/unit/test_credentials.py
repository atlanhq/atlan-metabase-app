"""Unit tests for app/credentials.py."""

from __future__ import annotations

import pytest
from application_sdk.credentials.ref import CredentialRef
from application_sdk.errors import InvalidInputError
from application_sdk.handler.contracts import HandlerCredential

from app.contracts import MetabaseInput
from app.credentials import (
    MetabaseCredential,
    build_credential_ref,
    parse_metabase_credentials,
)


class TestBuildCredentialRef:
    def test_metabase_credential_ref_takes_precedence(self):
        ref = CredentialRef(name="x", credential_type="basic", credential_guid="g")
        inp = MetabaseInput(metabase_credential=ref)
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is ref
        assert inline == {}

    def test_credential_guid_creates_ref(self):
        inp = MetabaseInput(credential_guid="guid-123")
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is not None
        assert out_ref.name == "guid-123"
        assert out_ref.credential_type == "basic"
        assert out_ref.credential_guid == "guid-123"
        assert inline == {}

    def test_credentials_list_flattens_to_inline(self):
        inp = MetabaseInput(
            credentials=[
                {"key": "host", "value": "http://localhost"},
                {"key": "port", "value": "3000"},
                {"key": "username", "value": "u"},
                {"key": "password", "value": "p"},
            ]
        )
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is None
        assert inline == {
            "host": "http://localhost",
            "port": "3000",
            "username": "u",
            "password": "p",
        }

    def test_credentials_dict_passes_through(self):
        inp = MetabaseInput(credentials={"host": "h", "port": 3000})
        out_ref, inline = build_credential_ref(inp)
        assert out_ref is None
        assert inline == {"host": "h", "port": 3000}

    def test_no_credentials_returns_empty_inline(self):
        out_ref, inline = build_credential_ref(MetabaseInput())
        assert out_ref is None
        assert inline == {}


class TestParseMetabaseCredentials:
    def test_empty_dict_returns_default_credential(self):
        cred = parse_metabase_credentials({})
        assert isinstance(cred, MetabaseCredential)
        assert cred.host == ""
        assert cred.port == 443
        assert cred.username == ""
        assert cred.password == ""

    def test_flat_dict_shape(self):
        cred = parse_metabase_credentials(
            {"host": "http://x", "port": 3000, "username": "u", "password": "p"}
        )
        assert cred.host == "http://x"
        assert cred.port == 3000
        assert cred.username == "u"
        assert cred.password == "p"

    def test_nested_extra_dict_shape(self):
        cred = parse_metabase_credentials(
            {"host": "h", "extra": {"username": "u", "password": "p"}}
        )
        assert cred.host == "h"
        assert cred.username == "u"
        assert cred.password == "p"

    def test_string_json_extra_is_decoded(self):
        cred = parse_metabase_credentials(
            {"host": "h", "extra": '{"username": "u", "password": "p"}'}
        )
        assert cred.username == "u"
        assert cred.password == "p"

    def test_invalid_string_extra_is_ignored(self):
        cred = parse_metabase_credentials(
            {"host": "h", "username": "u", "extra": "not-json"}
        )
        assert cred.host == "h"
        assert cred.username == "u"

    def test_port_under_extra_is_picked_up(self):
        cred = parse_metabase_credentials(
            {"host": "h", "extra": {"port": "8080", "username": "u"}}
        )
        assert cred.port == 8080
        assert cred.username == "u"

    def test_none_port_falls_back_to_443(self):
        cred = parse_metabase_credentials({"host": "h", "port": None})
        assert cred.port == 443

    def test_unparseable_port_falls_back_to_443(self):
        cred = parse_metabase_credentials({"host": "h", "port": "abc"})
        assert cred.port == 443

    def test_handler_credential_list_with_extra_prefix(self):
        raw = [
            HandlerCredential(key="host", value="h"),
            HandlerCredential(key="extra.username", value="u"),
            HandlerCredential(key="extra.password", value="p"),
            HandlerCredential(key="extra.port", value="9000"),
        ]
        cred = parse_metabase_credentials(raw)
        assert cred.host == "h"
        assert cred.username == "u"
        assert cred.password == "p"
        assert cred.port == 9000

    def test_handler_credential_list_flat_keys(self):
        raw = [
            HandlerCredential(key="host", value="h"),
            HandlerCredential(key="username", value="u"),
        ]
        cred = parse_metabase_credentials(raw)
        assert cred.host == "h"
        assert cred.username == "u"

    def test_already_typed_credential_passes_through(self):
        original = MetabaseCredential(host="h", username="u", password="p", port=1234)
        result = parse_metabase_credentials(original)
        assert result is original

    def test_unsupported_payload_type_raises(self):
        with pytest.raises(InvalidInputError, match="Unsupported credentials payload"):
            parse_metabase_credentials(42)  # type: ignore[arg-type]
