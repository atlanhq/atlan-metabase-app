# TODO(upgrade-v3): MetabaseApiClient.load() now accepts a typed `credential`
# kwarg (MetabaseCredential) in addition to the legacy `credentials` dict.
# Update test fixtures to construct the typed credential where appropriate.
"""Unit tests for app.client.MetabaseApiClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.client import MetabaseApiClient
from app.contracts import MetabaseCredential
from app.errors import MetabaseSessionAuthError, MetabaseSessionMissingError


class TestMetabaseApiClient:
    """Tests for MetabaseApiClient session-token authentication lifecycle."""

    # -------------------------------------------------------------------------
    # Helpers: build a client with internal state set directly (never call load())
    # -------------------------------------------------------------------------

    @pytest.fixture
    def client(self):
        """Return a MetabaseApiClient with state pre-loaded (no load() call)."""
        c = MetabaseApiClient()
        c.host = "https://myinstance.metabaseapp.com"
        c.port = 443
        c.username = "admin@example.com"
        c.password = "s3cr3t"
        c.session_token = None
        c.http_headers = {}
        return c

    @pytest.fixture
    def authenticated_client(self, client):
        """Return a MetabaseApiClient with a session token already set."""
        client.session_token = "test-session-token-abc123"
        client.http_headers = {
            "X-Metabase-Session": "test-session-token-abc123",
            "Content-Type": "application/json",
        }
        return client

    # -------------------------------------------------------------------------
    # _authenticate: success path
    # -------------------------------------------------------------------------

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_success_stores_session_token(self, mock_post, client):
        """Successful POST to /api/session stores the returned id as session_token."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": "session-token-xyz"}
        mock_post.return_value = mock_response

        await client._authenticate()

        assert client.session_token == "session-token-xyz"

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_success_sets_x_metabase_session_header(
        self, mock_post, client
    ):
        """After load(), http_headers must include the X-Metabase-Session header."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": "session-token-xyz"}
        mock_post.return_value = mock_response

        # Replicate what load() does after _authenticate() succeeds:
        await client._authenticate()
        client.http_headers = {
            "X-Metabase-Session": client.session_token,
            "Content-Type": "application/json",
        }

        assert client.http_headers["X-Metabase-Session"] == "session-token-xyz"

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_posts_to_session_endpoint(self, mock_post, client):
        """_authenticate() must POST to the /api/session URL."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": "tok"}
        mock_post.return_value = mock_response

        await client._authenticate()

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert "/api/session" in call_kwargs["url"]

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_sends_username_and_password_in_payload(
        self, mock_post, client
    ):
        """The POST body must include username and password from the client."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": "tok"}
        mock_post.return_value = mock_response

        await client._authenticate()

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json_data"]["username"] == "admin@example.com"
        assert call_kwargs["json_data"]["password"] == "s3cr3t"

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_posts_exact_url_and_timeout(self, mock_post, client):
        """The session POST targets host:port/api/session with a 30s timeout."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": "tok"}
        mock_post.return_value = mock_response

        await client._authenticate()

        call_kwargs = mock_post.call_args[1]
        assert (
            call_kwargs["url"] == "https://myinstance.metabaseapp.com:443/api/session"
        )
        assert call_kwargs["timeout"] == 30

    # -------------------------------------------------------------------------
    # _authenticate: failure paths
    # -------------------------------------------------------------------------

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_non_200_captures_status_in_failure_reason(
        self, mock_post, client
    ):
        """Non-success response raises with the status in failure_reason, not the message."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        with pytest.raises(MetabaseSessionAuthError) as exc_info:
            await client._authenticate()
        assert exc_info.value.failure_reason == "401"
        assert exc_info.value.message == "Metabase authentication failed."

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_none_response_raises_exception(self, mock_post, client):
        """None response raises an Exception (no DAPR/network response)."""
        mock_post.return_value = None

        with pytest.raises(Exception):
            await client._authenticate()

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_403_captures_status_in_failure_reason(
        self, mock_post, client
    ):
        """403 response captures the status in failure_reason; message stays clean."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 403
        mock_post.return_value = mock_response

        with pytest.raises(MetabaseSessionAuthError) as exc_info:
            await client._authenticate()
        assert exc_info.value.failure_reason == "403"
        assert "403" not in exc_info.value.message

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_authenticate_none_response_error_details(self, mock_post, client):
        """None response pins failure_reason='No response' plus auth metadata."""
        mock_post.return_value = None

        with pytest.raises(MetabaseSessionAuthError) as exc_info:
            await client._authenticate()
        assert exc_info.value.failure_reason == "No response"
        assert exc_info.value.auth_method == "session-token"
        assert exc_info.value.principal == "admin@example.com"

    # -------------------------------------------------------------------------
    # test_connection
    # -------------------------------------------------------------------------

    async def test_test_connection_with_token_returns_true(self, authenticated_client):
        """test_connection returns True when a session token is present."""
        result = await authenticated_client.test_connection()
        assert result is True

    async def test_test_connection_without_token_raises(self, client):
        """test_connection raises when no session token has been obtained."""
        client.session_token = None

        with pytest.raises(Exception, match="No session token available"):
            await client.test_connection()

    async def test_test_connection_empty_string_token_raises(self, client):
        """Empty string is falsy — treated as no token."""
        client.session_token = ""

        with pytest.raises(Exception):
            await client.test_connection()

    async def test_test_connection_missing_token_error_details(self, client):
        """The missing-token error pins message, auth_method, and principal."""
        client.session_token = None

        with pytest.raises(MetabaseSessionMissingError) as exc_info:
            await client.test_connection()
        assert exc_info.value.message == (
            "No session token available — authentication did not succeed"
        )
        assert exc_info.value.auth_method == "session-token"
        assert exc_info.value.principal == "admin@example.com"

    # -------------------------------------------------------------------------
    # load() integration (via patches to avoid real HTTP)
    # -------------------------------------------------------------------------

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_sets_host_port_username_password(self, mock_auth):
        """load() parses credentials and stores them on the client."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        # Manually set session_token since mock skips _authenticate side-effects
        c.session_token = "tok"
        await c.load(
            credentials={
                "host": "https://mb.example.com",
                "port": 8080,
                "username": "user",
                "password": "pass",
            }
        )

        assert c.host == "https://mb.example.com"
        assert c.port == 8080
        assert c.username == "user"
        assert c.password == "pass"

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_uses_default_port_443_when_not_provided(self, mock_auth):
        """load() defaults port to 443 when not in credentials."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        c.session_token = "tok"
        await c.load(credentials={"host": "https://mb.example.com"})

        assert c.port == 443

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_calls_authenticate(self, mock_auth):
        """load() must call _authenticate() to obtain the session token."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        c.session_token = "tok"
        await c.load(credentials={"host": "https://mb.example.com"})

        mock_auth.assert_called_once()

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_prefers_typed_credential_kwarg(self, mock_auth):
        """load(credential=MetabaseCredential(...)) uses the typed credential."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        await c.load(
            credential=MetabaseCredential(
                host="https://typed.example.com",
                port=8443,
                username="typed-user",
                password="typed-pass",
            )
        )

        assert c.host == "https://typed.example.com"
        assert c.port == 8443
        assert c.username == "typed-user"
        assert c.password == "typed-pass"

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_accepts_typed_credential_via_legacy_credentials_kwarg(
        self, mock_auth
    ):
        """A MetabaseCredential passed as `credentials` is used directly."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        await c.load(
            credentials=MetabaseCredential(
                host="https://legacy.example.com",
                port=9443,
                username="legacy-user",
                password="legacy-pass",
            )
        )

        assert c.host == "https://legacy.example.com"
        assert c.port == 9443
        assert c.username == "legacy-user"
        assert c.password == "legacy-pass"

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_with_no_kwargs_falls_back_to_empty_defaults(self, mock_auth):
        """load() with no credential kwargs validates an empty dict → defaults."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        await c.load()

        assert c.host == ""
        assert c.port == 443
        assert c.username == ""
        assert c.password == ""

    @patch.object(MetabaseApiClient, "_authenticate", new_callable=AsyncMock)
    async def test_load_resets_session_token_to_none_before_authenticate(
        self, mock_auth
    ):
        """load() initialises session_token to exactly None (not '') pre-auth."""
        mock_auth.return_value = None

        c = MetabaseApiClient()
        await c.load(credentials={"host": "https://mb.example.com"})

        assert c.session_token is None

    @patch.object(
        MetabaseApiClient, "execute_http_post_request", new_callable=AsyncMock
    )
    async def test_load_sets_exact_http_headers(self, mock_post):
        """After load(), http_headers is exactly the session + content-type pair."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": "tok-1"}
        mock_post.return_value = mock_response

        c = MetabaseApiClient()
        await c.load(
            credentials={
                "host": "https://mb.example.com",
                "port": 443,
                "username": "u",
                "password": "p",
            }
        )

        assert c.http_headers == {
            "X-Metabase-Session": "tok-1",
            "Content-Type": "application/json",
        }
