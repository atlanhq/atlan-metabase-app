"""Metabase REST API client with session-token authentication."""

from __future__ import annotations

from typing import Any, Optional

from application_sdk.clients.base import BaseClient
from application_sdk.observability.logger_adaptor import get_logger

from app.constants import MetabaseUrls
from app.contracts import MetabaseCredential

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

        await self._authenticate()

        self.http_headers = {
            "X-Metabase-Session": self.session_token,
            "Content-Type": "application/json",
        }
        logger.info(f"MetabaseApiClient loaded for host: {self.host}")

    async def _authenticate(self) -> None:
        """Obtain a Metabase session token via ``POST /api/session``."""
        url = MetabaseUrls.session(self.host, self.port)
        payload = {"username": self.username, "password": self.password}

        response = await self.execute_http_post_request(
            url=url,
            json_data=payload,
            timeout=30,
        )

        if response is None or not response.is_success:
            status = response.status_code if response else "No response"
            raise Exception(f"Metabase authentication failed with status: {status}")

        self.session_token = response.json()["id"]
        logger.info("Metabase session token obtained successfully")

    async def test_connection(self) -> bool:
        """Verify that authentication succeeded and a session token is held."""
        if not self.session_token:
            raise Exception(
                "No session token available — authentication did not succeed"
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


async def build_client(credential: MetabaseCredential) -> MetabaseApiClient:
    """Build and authenticate a :class:`MetabaseApiClient` from a typed credential.

    Defined at module level (not on the handler / app) so the handler and the
    workflow tasks share one credential → client path. Reviewers on the MSSQL
    v3 PR flagged a duplicated ``_build_client`` body as a top-finding;
    this helper avoids that.
    """
    client = MetabaseApiClient()
    await client.load(credential=credential)
    return client
