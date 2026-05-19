# TODO(upgrade-v3): MetabaseHandler v3 signatures use typed contracts (AuthInput,
# PreflightInput, MetadataInput) instead of *args/**kwargs and return AuthOutput /
# PreflightOutput / ApiMetadataOutput rather than raw bool/dict. Update fixtures
# and assertions in this file to match the new shapes.
"""Unit tests for app.handler.MetabaseHandler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.client import MetabaseApiClient
from app.handler import MetabaseHandler
from app.models import PreflightCheckResult


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

    # -------------------------------------------------------------------------
    # test_auth: success
    # -------------------------------------------------------------------------

    async def test_auth_success_returns_true(self, handler, mock_client):
        """test_auth returns True when the client has a valid session token."""
        mock_client.test_connection = AsyncMock(return_value=True)

        result = await handler.test_auth()

        assert result is True
        mock_client.test_connection.assert_called_once()

    # -------------------------------------------------------------------------
    # test_auth: failure — no token
    # -------------------------------------------------------------------------

    async def test_auth_failure_raises_when_no_token(self, handler, mock_client):
        """test_auth propagates exceptions raised by test_connection."""
        mock_client.test_connection = AsyncMock(
            side_effect=Exception("No session token available")
        )

        with pytest.raises(Exception, match="No session token available"):
            await handler.test_auth()

    # -------------------------------------------------------------------------
    # test_auth: no client
    # -------------------------------------------------------------------------

    async def test_auth_no_client_raises(self, handler_no_client):
        """test_auth raises when no client is initialized."""
        with pytest.raises(Exception, match="Metabase client not initialized"):
            await handler_no_client.test_auth()


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

    # -------------------------------------------------------------------------
    # fetch_metadata: success
    # -------------------------------------------------------------------------

    async def test_fetch_metadata_returns_value_title_children_shape(
        self, handler, mock_client
    ):
        """fetch_metadata returns list of dicts with value, title, children keys."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Marketing", "personal_owner_id": None},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata()

        assert isinstance(result, list)
        assert len(result) == 2
        for item in result:
            assert "value" in item
            assert "title" in item
            assert "children" in item
            assert item["children"] == []

    async def test_fetch_metadata_maps_id_to_value(self, handler, mock_client):
        """The 'value' field in the output is the collection id."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 42, "name": "My Collection", "personal_owner_id": None},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata()

        assert result[0]["value"] == 42

    async def test_fetch_metadata_maps_name_to_title(self, handler, mock_client):
        """The 'title' field in the output is the collection name."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await handler.fetch_metadata()

        assert result[0]["title"] == "Engineering"

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

        result = await handler.fetch_metadata()

        assert len(result) == 1
        assert result[0]["value"] == 1

    # -------------------------------------------------------------------------
    # fetch_metadata: empty response raises
    # -------------------------------------------------------------------------

    async def test_fetch_metadata_api_failure_raises(self, handler, mock_client):
        """Non-success API response raises an exception."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with pytest.raises(Exception):
            await handler.fetch_metadata()

    # -------------------------------------------------------------------------
    # fetch_metadata: no client
    # -------------------------------------------------------------------------

    async def test_fetch_metadata_no_client_raises(self, handler_no_client):
        """fetch_metadata raises when no client is initialized."""
        with pytest.raises(Exception, match="Metabase client not initialized"):
            await handler_no_client.fetch_metadata()


class TestMetabaseHandlerValidators:
    """Tests for MetabaseHandler preflight check static validators."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    # -------------------------------------------------------------------------
    # _validate_collection_count
    # -------------------------------------------------------------------------

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_success(self, mock_fetch, mock_client):
        """Returns success with count when collections are available."""
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Marketing", "personal_owner_id": None},
        ]

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.success is True
        assert "2" in result.successMessage

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_skips_personal_collections(
        self, mock_fetch, mock_client
    ):
        """Personal collections do not count toward the total."""
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Personal", "personal_owner_id": 99},
        ]

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.success is True
        assert "1" in result.successMessage

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_with_include_filter(
        self, mock_fetch, mock_client
    ):
        """Include filter restricts the count to matching IDs."""
        mock_fetch.return_value = [
            {"id": 1, "name": "Engineering", "personal_owner_id": None},
            {"id": 2, "name": "Marketing", "personal_owner_id": None},
        ]

        result = await MetabaseHandler._validate_collection_count(
            mock_client, {"1": "Engineering"}, {}
        )

        assert result.success is True
        assert "1" in result.successMessage

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_api_failure_returns_failure(
        self, mock_fetch, mock_client
    ):
        """API failure returns a failed PreflightCheckResult."""
        mock_fetch.side_effect = Exception("Failed to fetch collections")

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.success is False
        assert "Collection count check failed" in result.failureMessage

    # -------------------------------------------------------------------------
    # _validate_dashboard_count
    # -------------------------------------------------------------------------

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_dashboard_count_success(self, mock_fetch, mock_client):
        """Returns success with dashboard count."""
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

        assert result.success is True
        assert "2" in result.successMessage

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_dashboard_count_excludes_personal_collection_dashboards(
        self, mock_fetch, mock_client
    ):
        """Dashboards in personal collections are excluded from count."""
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

        assert result.success is True
        assert "1" in result.successMessage

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_dashboard_count_api_failure_returns_failure(
        self, mock_fetch, mock_client
    ):
        """API failure for dashboard list returns a failed result."""
        mock_fetch.return_value = []
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_dashboard_count(mock_client, {}, {})

        assert result.success is False

    # -------------------------------------------------------------------------
    # _validate_question_count
    # -------------------------------------------------------------------------

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_question_count_success(self, mock_fetch, mock_client):
        """Returns success with question count."""
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

        assert result.success is True
        assert "3" in result.successMessage

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

        assert result.success is False
        assert "Question count check failed" in result.failureMessage

    # -------------------------------------------------------------------------
    # _validate_native_query_permission
    # -------------------------------------------------------------------------

    async def test_validate_native_query_permission_all_write_returns_success(
        self, mock_client
    ):
        """All databases with native_permissions='write' → success."""
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

        assert result.success is True
        assert "Check successful" in result.successMessage

    async def test_validate_native_query_permission_missing_write_returns_failure(
        self, mock_client
    ):
        """Database without write permission is listed in failure message."""
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

        assert result.success is False
        assert "BigQuery" in result.failureMessage

    async def test_validate_native_query_permission_api_failure_returns_failure(
        self, mock_client
    ):
        """API failure returns a failed result."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_native_query_permission(mock_client)

        assert result.success is False


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

    # -------------------------------------------------------------------------
    # All checks pass
    # -------------------------------------------------------------------------

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
        """When all validators succeed, all four keys are present in the result."""
        mock_collection.return_value = PreflightCheckResult(
            success=True, successMessage="Total collections: 3"
        )
        mock_dashboard.return_value = PreflightCheckResult(
            success=True, successMessage="Total dashboards: 2"
        )
        mock_question.return_value = PreflightCheckResult(
            success=True, successMessage="Total questions: 5"
        )
        mock_native.return_value = PreflightCheckResult(
            success=True, successMessage="Check successful"
        )

        result = await handler.preflight_check()

        assert result["collectionCountCheck"]["success"] is True
        assert result["dashboardCountCheck"]["success"] is True
        assert result["questionCountCheck"]["success"] is True
        assert result["nativeQueryPermissionCheck"]["success"] is True

    # -------------------------------------------------------------------------
    # Short-circuit when collection check fails
    # -------------------------------------------------------------------------

    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_preflight_check_short_circuits_when_collection_check_fails(
        self, mock_collection, handler
    ):
        """When collectionCountCheck fails, subsequent checks are not run."""
        mock_collection.return_value = PreflightCheckResult(
            success=False,
            failureMessage="Collection count check failed: connection refused",
        )

        result = await handler.preflight_check()

        assert result["collectionCountCheck"]["success"] is False
        # Subsequent checks should not be present because of short-circuit
        assert "dashboardCountCheck" not in result
        assert "questionCountCheck" not in result
        assert "nativeQueryPermissionCheck" not in result

    # -------------------------------------------------------------------------
    # No client
    # -------------------------------------------------------------------------

    async def test_preflight_check_no_client_returns_failure(self, handler_no_client):
        """preflight_check returns a structured failure when no client is set."""
        result = await handler_no_client.preflight_check()

        assert "collectionCountCheck" in result
        assert result["collectionCountCheck"]["success"] is False
        assert (
            "Metabase client not initialized"
            in result["collectionCountCheck"]["failureMessage"]
        )
