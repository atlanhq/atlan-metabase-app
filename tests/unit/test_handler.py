"""Unit tests for app.handler.MetabaseHandler (v3 typed contracts)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from application_sdk.handler.contracts import (
    AuthInput,
    AuthStatus,
    HandlerCredential,
    MetadataInput,
    PreflightCheck,
    PreflightInput,
    PreflightStatus,
)

from app.client import MetabaseApiClient
from app.handler import MetabaseHandler


def _creds(**overrides):
    """Build a HandlerCredential list with sensible defaults for handler tests."""
    defaults = {
        "host": "https://myinstance.metabaseapp.com",
        "port": "443",
        "username": "u",
        "password": "p",
    }
    defaults.update(overrides)
    return [HandlerCredential(key=k, value=str(v)) for k, v in defaults.items()]


class TestMetabaseHandlerTestAuth:
    """Tests for MetabaseHandler.test_auth()."""

    @pytest.fixture
    def mock_client(self):
        return MagicMock(spec=MetabaseApiClient)

    @pytest.fixture
    def handler(self, mock_client):
        return MetabaseHandler(client=mock_client)

    @pytest.fixture
    def handler_no_client(self):
        return MetabaseHandler(client=None)

    async def test_auth_success_returns_true(self, handler, mock_client):
        """test_auth returns SUCCESS when the client authenticates."""
        mock_client.test_connection = AsyncMock(return_value=True)

        result = await handler.test_auth(AuthInput(credentials=_creds()))

        assert result.status == AuthStatus.SUCCESS
        assert result.message == "Authentication successful"
        mock_client.test_connection.assert_called_once()

    async def test_auth_failure_raises_when_no_token(self, handler, mock_client):
        """test_auth returns FAILED when test_connection raises."""
        mock_client.test_connection = AsyncMock(
            side_effect=Exception("No session token available")
        )

        result = await handler.test_auth(AuthInput(credentials=_creds()))

        assert result.status == AuthStatus.FAILED
        assert "No session token available" in result.message

    async def test_auth_no_client_raises(self, handler_no_client):
        """test_auth returns FAILED when there is no client and no credentials."""
        result = await handler_no_client.test_auth(AuthInput(credentials=[]))

        assert result.status == AuthStatus.FAILED
        assert "Metabase client not initialized" in result.message


class TestMetabaseHandlerFetchMetadata:
    """Tests for MetabaseHandler.fetch_metadata()."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    @pytest.fixture
    def handler(self, mock_client):
        return MetabaseHandler(client=mock_client)

    @pytest.fixture
    def handler_no_client(self):
        return MetabaseHandler(client=None)

    async def test_fetch_metadata_returns_value_title_children_shape(
        self, handler, mock_client
    ):
        """fetch_metadata returns ApiMetadataOutput with value/title/node_type per object."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Marketing", "personal_owner_id": None},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata(MetadataInput(credentials=_creds()))

        assert len(result.objects) == 2
        for obj in result.objects:
            assert obj.value
            assert obj.title
            assert obj.node_type == "collection"

    async def test_fetch_metadata_maps_id_to_value(self, handler, mock_client):
        """The 'value' field is the stringified collection id."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 42, "name": "My Collection", "personal_owner_id": None},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata(MetadataInput(credentials=_creds()))

        assert result.objects[0].value == "42"

    async def test_fetch_metadata_maps_name_to_title(self, handler, mock_client):
        """The 'title' field is the collection name."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata(MetadataInput(credentials=_creds()))

        assert result.objects[0].title == "Engineering"

    async def test_fetch_metadata_filters_out_personal_collections(
        self, handler, mock_client
    ):
        """Personal collections (personal_owner_id is not None) are excluded."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Personal", "personal_owner_id": 99},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata(MetadataInput(credentials=_creds()))

        assert len(result.objects) == 1
        assert result.objects[0].value == "1"

    async def test_fetch_metadata_api_failure_raises(self, handler, mock_client):
        """Non-success API response raises an exception."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with pytest.raises(Exception):
            await handler.fetch_metadata(MetadataInput(credentials=_creds()))

    async def test_fetch_metadata_no_client_raises(self, handler_no_client):
        """fetch_metadata raises when there's no client AND no credentials."""
        with pytest.raises(Exception, match="Metabase client not initialized"):
            await handler_no_client.fetch_metadata(MetadataInput(credentials=[]))


class TestMetabaseHandlerValidators:
    """Tests for MetabaseHandler preflight check static validators (return PreflightCheck)."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    # _validate_collection_count -------------------------------------------------

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_success(self, mock_fetch, mock_client):
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Marketing", "personal_owner_id": None},
        ]

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert isinstance(result, PreflightCheck)
        assert result.name == "collectionCountCheck"
        assert result.passed is True
        assert "2" in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_skips_personal_collections(
        self, mock_fetch, mock_client
    ):
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Personal", "personal_owner_id": 99},
        ]

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.passed is True
        assert "1" in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_with_include_filter(
        self, mock_fetch, mock_client
    ):
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Marketing", "personal_owner_id": None},
        ]

        result = await MetabaseHandler._validate_collection_count(
            mock_client, {"1": "Engineering"}, {}
        )

        assert result.passed is True
        assert "1" in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_api_failure_returns_failure(
        self, mock_fetch, mock_client
    ):
        mock_fetch.side_effect = Exception("Failed to fetch collections")

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.passed is False
        assert "Collection count check failed" in result.message

    # _validate_dashboard_count -------------------------------------------------

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_dashboard_count_success(self, mock_fetch, mock_client):
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
        ]
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 10, "name": "Sales Dash", "collection_id": 1},
            {"id": 11, "name": "Ops Dash", "collection_id": 1},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_dashboard_count(mock_client, {}, {})

        assert result.name == "dashboardCountCheck"
        assert result.passed is True
        assert "2" in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_dashboard_count_excludes_personal_collection_dashboards(
        self, mock_fetch, mock_client
    ):
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 99, "name": "Personal", "personal_owner_id": 5},
        ]
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 10, "name": "Sales Dash", "collection_id": 1},
            {"id": 11, "name": "Personal Dash", "collection_id": 99},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_dashboard_count(mock_client, {}, {})

        assert result.passed is True
        assert "1" in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_dashboard_count_api_failure_returns_failure(
        self, mock_fetch, mock_client
    ):
        mock_fetch.return_value = []
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_dashboard_count(mock_client, {}, {})

        assert result.passed is False

    # _validate_question_count -------------------------------------------------

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_question_count_success(self, mock_fetch, mock_client):
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
        ]
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 20, "name": "Revenue Q", "collection_id": 1},
            {"id": 21, "name": "Retention Q", "collection_id": 1},
            {"id": 22, "name": "Churn Q", "collection_id": 1},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_question_count(mock_client, {}, {})

        assert result.name == "questionCountCheck"
        assert result.passed is True
        assert "3" in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_question_count_api_failure_returns_failure(
        self, mock_fetch, mock_client
    ):
        mock_fetch.return_value = []
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 403
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_question_count(mock_client, {}, {})

        assert result.passed is False
        assert "Question count check failed" in result.message

    # _validate_native_query_permission ----------------------------------------

    async def test_validate_native_query_permission_all_write_returns_success(
        self, mock_client
    ):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "data": [
                {"id": 1, "name": "Snowflake", "native_permissions": "write"},
                {"id": 2, "name": "Postgres", "native_permissions": "write"},
            ]
        }
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_native_query_permission(mock_client)

        assert result.name == "nativeQueryPermissionCheck"
        assert result.passed is True
        assert "Check successful" in result.message

    async def test_validate_native_query_permission_missing_write_returns_failure(
        self, mock_client
    ):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "data": [
                {"id": 1, "name": "Snowflake", "native_permissions": "write"},
                {"id": 2, "name": "BigQuery", "native_permissions": "read"},
            ]
        }
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_native_query_permission(mock_client)

        assert result.passed is False
        assert "BigQuery" in result.message

    async def test_validate_native_query_permission_api_failure_returns_failure(
        self, mock_client
    ):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_native_query_permission(mock_client)

        assert result.passed is False


class TestMetabaseHandlerPreflightCheck:
    """Integration tests for MetabaseHandler.preflight_check()."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    @pytest.fixture
    def handler(self, mock_client):
        return MetabaseHandler(client=mock_client)

    @pytest.fixture
    def handler_no_client(self):
        return MetabaseHandler(client=None)

    @staticmethod
    def _check(name: str, passed: bool, message: str = "") -> PreflightCheck:
        return PreflightCheck(name=name, passed=passed, message=message)

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_preflight_check_all_pass_returns_all_results(
        self,
        mock_collection,
        mock_dashboard,
        mock_question,
        mock_native,
        handler,
    ):
        """When all validators succeed, all four checks are present in the result."""
        mock_collection.return_value = self._check(
            "collectionCountCheck", True, "Total collections: 3"
        )
        mock_dashboard.return_value = self._check(
            "dashboardCountCheck", True, "Total dashboards: 2"
        )
        mock_question.return_value = self._check(
            "questionCountCheck", True, "Total questions: 5"
        )
        mock_native.return_value = self._check(
            "nativeQueryPermissionCheck", True, "Check successful"
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.READY
        assert len(result.checks) == 4
        names = {c.name for c in result.checks}
        assert names == {
            "collectionCountCheck",
            "dashboardCountCheck",
            "questionCountCheck",
            "nativeQueryPermissionCheck",
        }
        assert all(c.passed for c in result.checks)

    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_preflight_check_short_circuits_when_collection_check_fails(
        self, mock_collection, handler
    ):
        """When collectionCountCheck fails, subsequent checks are not run."""
        mock_collection.return_value = self._check(
            "collectionCountCheck",
            False,
            "Collection count check failed: connection refused",
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.NOT_READY
        # Short-circuit: only the failed collection check is present.
        assert len(result.checks) == 1
        assert result.checks[0].name == "collectionCountCheck"
        assert result.checks[0].passed is False

    async def test_preflight_check_no_client_returns_failure(self, handler_no_client):
        """preflight_check returns NOT_READY when no client AND no credentials."""
        result = await handler_no_client.preflight_check(PreflightInput(credentials=[]))

        assert result.status == PreflightStatus.NOT_READY
        assert len(result.checks) >= 1
        assert result.checks[0].name == "collectionCountCheck"
        assert result.checks[0].passed is False
        assert "Metabase client not initialized" in result.checks[0].message
