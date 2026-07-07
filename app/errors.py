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

from application_sdk.errors import AuthError, InvalidInputError, SourceUnavailableError


@dataclass(kw_only=True)
class MetabaseSessionAuthError(AuthError):
    """``POST /api/session`` did not return a successful response."""

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
