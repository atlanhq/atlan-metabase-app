"""Metabase credential model and parsing.

A single home for credential concerns shared between the handler (which sees
``list[HandlerCredential]`` from the HTTP layer) and the connector (which
receives the raw dict ``resolve_credential_raw_or_inline`` returns). Two
primitives:

- :class:`MetabaseCredential` — the typed model the API client consumes.
- :func:`parse_metabase_credentials` — normalize any inbound shape (list of
  pairs, nested dict with ``extra``, already-typed credential) into the model.

Routing a workflow input's credential channels (``metabase_credential``,
``credential_guid``, ``agent_json``, inline ``credentials``) into a
``(ref, inline)`` pair is the SDK's job: ``route_credentials`` in
``application_sdk.credentials``, called from the entry point.
"""

from __future__ import annotations

from typing import Any

import orjson
from application_sdk.credentials.types import BasicCredential
from application_sdk.handler.contracts import HandlerCredential
from application_sdk.observability.logger_adaptor import get_logger
from pydantic import ConfigDict

from app.errors import UnsupportedCredentialsPayloadError

logger = get_logger(__name__)


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
      from the HTTP layer. Keys prefixed with ``extra.`` are flattened
      (``extra.username`` → ``username``).
    - ``dict[str, Any]`` — legacy v2 nested shape ``{host, port, extra:
      {username, password}}`` OR the flat shape ``{host, port, username,
      password}``. ``extra`` may also arrive as a JSON-encoded string.

      Both task paths hand the nested form in: ``_build_client`` passes
      whatever ``resolve_credential_raw_or_inline`` returns, and the SDK
      expands inline credentials (which cross ``@task`` boundaries as flat
      dotted keys, ``extra.username``) back to the nested ``extra`` shape the
      credential-ref path produces.
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
        raise UnsupportedCredentialsPayloadError(
            message=f"Unsupported credentials payload type: {type(raw).__name__}",
            field="credentials",
        )

    if not raw:
        return MetabaseCredential()

    flat = dict(raw)
    extra = raw.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = orjson.loads(extra) or {}
        except orjson.JSONDecodeError:
            # DEBUG, not WARNING: this is recovery, not a preflight failure —
            # the malformed block is ignored and parsing continues. A WARNING
            # from inside a preflight-reachable path is invisible under the
            # customer's default ERROR filter anyway, and duplicates whatever
            # verdict row the gate emits (F005 / FND-901). No ``exc_info``:
            # this function reads the credentials, so traceback frame locals
            # can carry the password under loguru's ``diagnose`` (F014).
            logger.debug("Credential 'extra' field is not valid JSON; ignoring")
            extra = {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            flat.setdefault(k, v)

    port_raw = flat.get("port", 443)
    try:
        port = int(port_raw) if port_raw not in (None, "") else 443
    except (TypeError, ValueError):
        # Same as above: recovery, not a verdict, and no traceback from a
        # function that holds the credentials in its locals (F005 / F014).
        logger.debug(
            "Credential port %r is not a valid integer; defaulting to 443",
            port_raw,
        )
        port = 443

    return MetabaseCredential(
        host=str(flat.get("host", "") or ""),
        port=port,
        username=str(flat.get("username", "") or ""),
        password=str(flat.get("password", "") or ""),
    )
