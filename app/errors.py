"""Domain-specific typed error subclasses for the Metabase connector.

The SDK's categorical leaf errors (``AuthError``, ``InvalidInputError``,
``SourceUnavailableError``, ...) are deliberately generic — each app is
expected to subclass the leaf that matches its failure's category and
override ``code`` so failures group meaningfully on dashboards instead of
collapsing every raise site of a category into one bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from application_sdk.errors import (
    AppPermissionDeniedError,
    AuthError,
    InvalidInputError,
    SourceUnavailableError,
)


@dataclass(kw_only=True)
class MetabaseSessionAuthError(AuthError):
    """``POST /api/session`` did not return a successful response.

    Carries a clean default ``message`` so the gate surfaces a stable sentence;
    the HTTP status rides in ``failure_reason`` / the ``cause`` chain, never the
    user-facing message.
    """

    message: str = "Metabase authentication failed."
    suggested_action: str | None = (
        "Verify the Metabase host, port, and credentials, then re-run preflight."
    )
    code: ClassVar[str] = "AUTH_METABASE_SESSION"


@dataclass(kw_only=True)
class MetabaseSessionMissingError(AuthError):
    """No Metabase session token is held (authentication did not complete)."""

    code: ClassVar[str] = "AUTH_METABASE_SESSION_MISSING"


@dataclass(kw_only=True)
class MetabaseClientNotInitializedError(InvalidInputError):
    """A Metabase client was requested but no credentials were supplied."""

    code: ClassVar[str] = "INVALID_INPUT_METABASE_CLIENT_NOT_INITIALIZED"


@dataclass(kw_only=True)
class MetabaseCredentialInputError(InvalidInputError):
    """Neither ``credential_ref`` nor ``inline_credentials`` was supplied."""

    code: ClassVar[str] = "INVALID_INPUT_METABASE_CREDENTIAL"


@dataclass(kw_only=True)
class UnsupportedCredentialsPayloadError(InvalidInputError):
    """The credentials payload is not a supported shape (dict / list)."""

    code: ClassVar[str] = "INVALID_INPUT_CREDENTIALS_PAYLOAD"


@dataclass(kw_only=True)
class MissingTypenameInputError(InvalidInputError):
    """``transform_data`` was called without a ``typename``."""

    code: ClassVar[str] = "INVALID_INPUT_MISSING_TYPENAME"


@dataclass(kw_only=True)
class MissingOutputPathInputError(InvalidInputError):
    """``transform_data`` was called without an ``output_path``."""

    code: ClassVar[str] = "INVALID_INPUT_MISSING_OUTPUT_PATH"


@dataclass(kw_only=True)
class MetabaseSourceUnavailableError(SourceUnavailableError):
    """A Metabase REST API call failed with a non-success HTTP response.

    Metabase is the customer-controlled source system this connector
    extracts from — not an Atlan-internal platform dependency — so this
    subclasses ``SourceUnavailableError`` (routes to ``Audience.USER``,
    retryable) rather than ``DependencyUnavailableError``.
    """

    code: ClassVar[str] = "SOURCE_UNAVAILABLE_METABASE"


@dataclass(kw_only=True)
class MetabaseCollectionAccessError(AppPermissionDeniedError):
    """Metabase rejected the collection listing with 401/403 during preflight.

    Read access to ``/api/collection`` is the authorization gate for the whole
    run — collections are the root asset type. A permission failure here is
    the customer's to fix, so this inherits ``AppPermissionDeniedError``'s
    PERMISSION category / USER audience.
    """

    message: str = "Metabase denied access to the collection listing."
    suggested_action: str | None = (
        "Grant the connector's user read access to collections in Metabase, "
        "then re-run preflight."
    )
    code: ClassVar[str] = "PERMISSION_METABASE_COLLECTION"


@dataclass(kw_only=True)
class MetabaseNativeQueryPermissionError(AppPermissionDeniedError):
    """One or more databases lack native-query ``write`` permission.

    The connector needs native query editing permission to extract question
    SQL; a missing grant is customer-fixable, so this inherits
    ``AppPermissionDeniedError``'s PERMISSION category / USER audience. The
    offending database names ride in ``evidence`` via ``missing_databases``.
    """

    message: str = (
        "One or more Metabase databases are missing native query editing permission."
    )
    suggested_action: str | None = (
        "Grant native query editing (write) permission on the affected "
        "databases in Metabase, then re-run preflight."
    )
    code: ClassVar[str] = "PERMISSION_METABASE_NATIVE_QUERY"

    missing_databases: list[str] | None = None
