"""Metabase credential model, routing, and parsing.

A single home for credential concerns shared between the handler (which sees
``list[HandlerCredential]`` from the HTTP layer) and the connector (which
receives ``CredentialRef`` from PKL or an inline ``dict`` resolved from the
secret store). Three primitives:

- :class:`MetabaseCredential` — the typed model the API client consumes.
- :func:`parse_metabase_credentials` — normalize any inbound shape (list of
  pairs, nested dict with ``extra``, already-typed credential) into the model.
- :func:`build_credential_ref` — thin wrapper over
  :meth:`CredentialRef.resolve` from the SDK, which handles both direct
  (``credential_guid``) and agent (``agent_json``) routing natively. Falls
  back to inline credentials when neither is present, for local-dev runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import orjson
from application_sdk.credentials.errors import CredentialRoutingError
from application_sdk.credentials.ref import CredentialRef
from application_sdk.credentials.types import BasicCredential
from application_sdk.handler.contracts import HandlerCredential
from application_sdk.observability.logger_adaptor import get_logger
from pydantic import ConfigDict

from app.errors import UnsupportedCredentialsPayloadError

if TYPE_CHECKING:
    from app.contracts import MetabaseInput

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
            logger.warning(
                "Credential 'extra' field is not valid JSON; ignoring", exc_info=True
            )
            extra = {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            flat.setdefault(k, v)

    port_raw = flat.get("port", 443)
    try:
        port = int(port_raw) if port_raw not in (None, "") else 443
    except (TypeError, ValueError):
        logger.warning(
            "Credential port %r is not a valid integer; defaulting to 443",
            port_raw,
            exc_info=True,
        )
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
    """Route ``MetabaseInput``'s credential channels into (ref, inline).

    Exactly one of the returned values is populated:

    - ``credential_ref`` — built by :meth:`CredentialRef.resolve` from the
      SDK, which inspects ``input.extraction_method`` + ``input.agent_json``
      + ``input.credential_guid`` and returns the right ref for either
      direct or agent mode. Mysql gets this same routing for free via its
      SDK base class (:meth:`SQLAppE2ETest._resolve_credential_ref`);
      metabase wires it in explicitly here because the REST connector has
      no equivalent SDK base. Also handles the PKL-contract path
      (``input.metabase_credential``) for backward compat.
    - ``inline_credentials`` — from ``input.credentials`` (list[{key,value}]
      from the HTTP service layer, or a flat dict for local dev). Used
      when neither direct nor agent routing applies (e.g. unit tests).

    Tasks read ``credential_ref`` first; if absent they fall back to inline.
    """
    # PKL-contract path — explicit ref already constructed upstream.
    if input.metabase_credential is not None:
        return input.metabase_credential, {}

    # SDK-canonical routing — covers direct (credential_guid) AND agent
    # (agent_json) modes via the CredentialResolvable protocol. Raises
    # CredentialRoutingError when neither field is set, which means we
    # should fall through to inline.
    try:
        return CredentialRef.resolve(input), {}
    except CredentialRoutingError as e:
        logger.debug(
            "No credential routing fields set, falling through to inline credentials: %s",
            e,
        )

    inline: dict[str, Any] = {}
    creds = input.credentials
    if isinstance(creds, list):
        for item in creds:
            if isinstance(item, dict) and "key" in item:
                inline[item["key"]] = item.get("value", "")
    elif isinstance(creds, dict):
        inline = creds
    return None, inline
