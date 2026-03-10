"""Metabase REST API client with session-token authentication."""

from typing import Any, Optional

from application_sdk.clients.base import BaseClient
from application_sdk.observability.logger_adaptor import get_logger

from app.constants import MetabaseUrls
from app.models import MetabaseCredentials

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
        """Initialize the client with credentials and obtain a session token.

        Args:
            **kwargs: Must include a ``credentials`` dict with Metabase
                connection details (``host``, ``port``, ``username``,
                ``password``).

        Raises:
            Exception: If authentication fails or no session token is returned.
        """
        raw_credentials = kwargs.get("credentials", {})
        creds = MetabaseCredentials.model_validate(raw_credentials)

        self.host: str = creds.host
        self.port: int = creds.port
        self.username: Optional[str] = creds.username
        self.password: Optional[str] = creds.password
        self.session_token: Optional[str] = None

        await self._authenticate()

        self.http_headers = {
            "X-Metabase-Session": self.session_token,
            "Content-Type": "application/json",
        }
        logger.info(f"MetabaseApiClient loaded for host: {self.host}")

    async def _authenticate(self) -> None:
        """Obtain a Metabase session token via ``POST /api/session``.

        Translates the ``restCredentialTemplate`` curl:

            curl --request POST '{{host}}:{{port}}/api/session'
                 --header 'Content-Type: application/json'
                 --data-raw '{"username": "{{username}}", "password": "{{password}}"}'

        The successful response body is ``{"id": "<session-token>"}``.

        Raises:
            Exception: If the request fails or the response is unsuccessful.
        """
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
        """Verify that authentication succeeded and a session token is held.

        Returns:
            ``True`` if a session token is present.

        Raises:
            Exception: If no session token is available (authentication failed).
        """
        if not self.session_token:
            raise Exception(
                "No session token available — authentication did not succeed"
            )
        return True
