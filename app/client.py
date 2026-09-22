"""Metabase REST API client with session-token authentication."""

from __future__ import annotations

from typing import Any, Optional

from application_sdk.clients.base import BaseClient
from application_sdk.observability.logger_adaptor import get_logger

from app.constants import MetabaseUrls
from app.contracts import MetabaseCredential
from app.errors import MetabaseSessionAuthError, MetabaseSessionMissingError

logger = get_logger(__name__)


class MetabaseApiClient(BaseClient):
    """Client for Metabase API interactions using session-token authentication.

    Authentication flow (mirrors ``restCredentialTemplate`` curl):

    1. ``load()`` calls ``_authenticate()``, which POSTs credentials to
       ``/api/session`` and stores the returned session token.
    2. All subsequent requests include the header
       ``X-Metabase-Session: <token>`` via ``self.http_headers``.
    3. ``test_connection()`` verifies that a session token was obtained.
    """

    #: Deadline for the session POST, in seconds. A class attribute, not set
    #: only in ``load()``, so ``_authenticate`` is well-defined for a client
    #: built directly (the extraction tasks and this app's client tests both
    #: do that). ``load(timeout=...)`` overrides it per instance.
    auth_timeout: int = 30

    async def load(self, **kwargs: Any) -> None:
        """Initialize the client with a typed ``MetabaseCredential``.

        Accepts either ``credential`` (preferred, typed) or ``credentials``
        (legacy dict). The legacy dict form is kept for backward compatibility
        with tests; new code paths should pass the typed credential.
        """
        credential = kwargs.get("credential")
        if credential is None:
            raw_credentials = kwargs.get("credentials", {})
            if isinstance(raw_credentials, MetabaseCredential):
                credential = raw_credentials
            else:
                credential = MetabaseCredential.model_validate(raw_credentials)

        self.host: str = credential.host
        self.port: int = credential.port
        self.username: Optional[str] = credential.username
        self.password: Optional[str] = credential.password
        self.session_token: Optional[str] = None
        # Preflight hands down what remains of ``PreflightInput.timeout_seconds``
        # so authentication is bounded by the gate's budget like every other
        # probe; every other caller keeps the class default.
        self.auth_timeout = int(kwargs.get("timeout", type(self).auth_timeout))

        await self._authenticate()

        self.http_headers = {
            "X-Metabase-Session": self.session_token,
            "Content-Type": "application/json",
        }
        logger.info("MetabaseApiClient loaded for host: %s", self.host)

    async def _authenticate(self) -> None:
        """Obtain a Metabase session token via ``POST /api/session``."""
        url = MetabaseUrls.session(self.host, self.port)
        payload = {"username": self.username, "password": self.password}

        response = await self.execute_http_post_request(
            url=url,
            json_data=payload,
            timeout=self.auth_timeout,
        )

        if response is None or not response.is_success:
            status = response.status_code if response else "No response"
            raise MetabaseSessionAuthError(
                auth_method="session-token",
                principal=self.username,
                failure_reason=str(status),
            )

        self.session_token = response.json()["id"]
        logger.info("Metabase session token obtained successfully")

    async def test_connection(self) -> bool:
        """Verify that authentication succeeded and a session token is held."""
        if not self.session_token:
            raise MetabaseSessionMissingError(
                message="No session token available — authentication did not succeed",
                auth_method="session-token",
                principal=self.username,
            )
        return True

    async def close(self) -> None:
        """Best-effort close — Metabase has no logout endpoint; clear the token.

        Called from ``MetabaseApp.dispose_client`` in the @entrypoint ``finally``
        block to drop the cached session token at the end of a run.
        """
        self.session_token = None


# ---------------------------------------------------------------------------
# Module-level factory — single source of truth used by handler and app.
# ---------------------------------------------------------------------------


async def build_client(
    credential: MetabaseCredential, *, timeout: int = 30
) -> MetabaseApiClient:
    """Build and authenticate a :class:`MetabaseApiClient` from a typed credential.

    Defined at module level (not on the handler / app) so the handler and the
    workflow tasks share one credential → client path. Reviewers on the MSSQL
    v3 PR flagged a duplicated ``_build_client`` body as a top-finding;
    this helper avoids that.

    ``timeout`` bounds the session POST. Preflight passes what remains of the
    gate's budget so authentication cannot outlive it; extraction tasks keep
    the 30s default.
    """
    client = MetabaseApiClient()
    await client.load(credential=credential, timeout=timeout)
    return client
