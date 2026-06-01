"""Metabase credential model, routing, and parsing.

A single home for credential concerns shared between the handler (which sees
``list[HandlerCredential]`` from the HTTP layer) and the connector (which
receives ``CredentialRef`` from PKL or an inline ``dict`` resolved from the
secret store). Three primitives:

- :class:`MetabaseCredential` — the typed model the API client consumes.
- :func:`parse_metabase_credentials` — normalize any inbound shape (list of
  pairs, nested dict with ``extra``, already-typed credential) into the model.
- :func:`build_credential_ref` — route ``MetabaseInput``'s three credential
  channels (PKL ``CredentialRef``, legacy GUID, inline payload) into the
  ``(ref, inline_dict)`` shape that downstream ``@task`` inputs carry.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from application_sdk.credentials.ref import CredentialRef
from application_sdk.credentials.types import BasicCredential
from application_sdk.handler.contracts import HandlerCredential
from pydantic import ConfigDict

if TYPE_CHECKING:
    from app.contracts import MetabaseInput


class MetabaseCredential(BasicCredential, frozen=True):
    """Username + password credential plus Metabase host/port.

    ``host`` is stored with its protocol prefix (e.g.
    ``https://acme.metabaseapp.com``) — the v2 ``restCredentialTemplate``
    writes the URL as ``{{host}}:{{port}}/...`` without prepending a scheme,
    and the e2e Docker pipeline targets ``http://localhost:3000``.
    """

    model_config = ConfigDict(frozen=True)

    host: str = ""
    port: int = 443

    # Override BasicCredential's required fields so the model can be
    # constructed empty (e.g. as a default_factory) and populated later.
    username: str = ""
    password: str = ""

    @property
    def credential_type(self) -> str:  # type: ignore[override]
        return "basic"


def parse_metabase_credentials(
    raw: list[HandlerCredential] | dict[str, Any] | MetabaseCredential,
) -> MetabaseCredential:
    """Coerce any supported inbound credential payload into a typed model.

    Accepts:
    - ``list[HandlerCredential]`` — v3 normalized ``[{key, value}]`` pairs
      from the HTTP layer (frontend / the platform). Keys prefixed with
      ``extra.`` are flattened (``extra.username`` → ``username``).
    - ``dict[str, Any]`` — legacy v2 nested shape ``{host, port, extra:
      {username, password}}`` OR the flat shape ``{host, port, username,
      password}``. ``extra`` may also arrive as a JSON-encoded string.
    - ``MetabaseCredential`` — already-typed credential, returned as-is.

    Empty/missing fields fall through to the model defaults.
    """
    if isinstance(raw, MetabaseCredential):
        return raw

    if isinstance(raw, list):
        flat: dict[str, Any] = {}
        for cred in raw:
            key = cred.key
            value = cred.value
            if key.startswith("extra."):
                flat[key[len("extra.") :]] = value
            else:
                flat[key] = value
        raw = flat

    if not isinstance(raw, dict):
        raise ValueError(
            f"Unsupported credentials payload type: {type(raw).__name__}"
        )

    if not raw:
        return MetabaseCredential()

    flat = dict(raw)
    extra = raw.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra) or {}
        except (json.JSONDecodeError, ValueError):
            extra = {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            flat.setdefault(k, v)

    port_raw = flat.get("port", 443)
    try:
        port = int(port_raw) if port_raw not in (None, "") else 443
    except (TypeError, ValueError):
        port = 443

    return MetabaseCredential(
        host=str(flat.get("host", "") or ""),
        port=port,
        username=str(flat.get("username", "") or ""),
        password=str(flat.get("password", "") or ""),
    )


def build_credential_ref(
    input: MetabaseInput,
) -> tuple[CredentialRef | None, dict[str, Any]]:
    """Route ``MetabaseInput``'s three credential channels into (ref, inline).

    Exactly one of the returned values is populated:

    - ``credential_ref`` — from ``input.metabase_credential`` (PKL contract)
      or constructed from ``input.credential_guid`` (legacy GUID).
    - ``inline_credentials`` — from ``input.credentials`` (list[{key,value}]
      from the HTTP service layer, or a flat dict for local dev).

    Tasks read ``credential_ref`` first; if absent they fall back to inline.
    """
    if input.metabase_credential is not None:
        return input.metabase_credential, {}
    if input.credential_guid:
        ref = CredentialRef(
            name=input.credential_guid,
            credential_type="basic",
            credential_guid=input.credential_guid,
        )
        return ref, {}

    inline: dict[str, Any] = {}
    creds = input.credentials
    if isinstance(creds, list):
        for item in creds:
            if isinstance(item, dict) and "key" in item:
                inline[item["key"]] = item.get("value", "")
    elif isinstance(creds, dict):
        inline = creds
    return None, inline
