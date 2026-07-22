"""Unit tests for app.handler.MetabaseHandler (v3 typed contracts)."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from application_sdk.handler.contracts import (
    AuthInput,
    AuthStatus,
    BaseMetadataConfig,
    HandlerCredential,
    MetadataInput,
    PreflightCheck,
    PreflightInput,
    PreflightStatus,
)

from app.client import MetabaseApiClient
from app.errors import (
    MetabaseClientNotInitializedError,
    MetabaseCollectionAccessError,
    MetabaseNativeQueryPermissionError,
    MetabaseSessionAuthError,
    MetabaseSourceUnavailableError,
)
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
        """test_auth returns FAILED with a stable message when test_connection raises.

        The raw exception text must NOT leak into the typed ``message`` field
        (E019) — the detail goes to the application logs instead.
        """
        mock_client.test_connection = AsyncMock(
            side_effect=Exception("No session token available")
        )

        result = await handler.test_auth(AuthInput(credentials=_creds()))

        assert result.status == AuthStatus.FAILED
        assert (
            result.message == "Authentication failed — see application logs for detail"
        )
        assert "No session token available" not in result.message

    async def test_auth_no_client_raises(self, handler_no_client):
        """test_auth returns FAILED when there is no client and no credentials."""
        result = await handler_no_client.test_auth(AuthInput(credentials=[]))

        assert result.status == AuthStatus.FAILED
        assert (
            result.message == "Authentication failed — see application logs for detail"
        )


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
    async def test_validate_collection_count_api_failure_returns_typed_source_error(
        self, mock_fetch, mock_client
    ):
        """An unexpected fetch error becomes a typed SOURCE_UNAVAILABLE failure."""
        mock_fetch.side_effect = Exception("connection refused")

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.passed is False
        assert result.error is not None
        assert result.error.category.name == "SOURCE_UNAVAILABLE"
        assert "connection refused" not in result.message

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validate_collection_count_403_maps_to_permission(
        self, mock_fetch, mock_client
    ):
        """A 401/403 on /api/collection maps to a PERMISSION failure, not source."""
        mock_fetch.side_effect = MetabaseSourceUnavailableError(
            message="Failed to fetch collections — HTTP 403",
            source_type="metabase",
            endpoint="/api/collection",
            http_status=403,
        )

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.passed is False
        assert result.error is not None
        assert result.error.category.name == "PERMISSION"
        assert result.error.code == "PERMISSION_METABASE_COLLECTION"

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
        assert result.error is not None
        assert result.error.category.name == "SOURCE_UNAVAILABLE"

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
        assert result.error is not None
        assert result.error.category.name == "SOURCE_UNAVAILABLE"

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
        assert result.error is not None
        assert result.error.code == "PERMISSION_METABASE_NATIVE_QUERY"
        assert result.error.category.name == "PERMISSION"
        assert result.error.audience.name == "USER"
        assert "BigQuery" in result.error.evidence["missing_databases"]
        assert result.error.suggested_action is not None
        # DB names ride in evidence, never the user-facing message.
        assert "BigQuery" not in result.message

    async def test_validate_native_query_permission_api_failure_returns_failure(
        self, mock_client
    ):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_native_query_permission(mock_client)

        assert result.passed is False
        assert result.error is not None
        assert result.error.category.name == "SOURCE_UNAVAILABLE"

    async def test_validate_native_query_permission_accepts_bare_list_response(
        self, mock_client
    ):
        """Older Metabase returns a bare list (no ``{"data": ...}``) — handle it."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Snowflake", "native_permissions": "write"},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await MetabaseHandler._validate_native_query_permission(mock_client)

        assert result.passed is True
        assert "Check successful" in result.message

    # duration measurement -----------------------------------------------------

    @patch.object(MetabaseHandler, "_elapsed_ms", return_value=42.0)
    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_validator_stamps_measured_duration_ms(
        self, mock_fetch, mock_elapsed, mock_client
    ):
        """A check stamps the measured elapsed time onto ``duration_ms``."""
        mock_fetch.return_value = [{"id": 1, "personal_owner_id": None}]

        result = await MetabaseHandler._validate_collection_count(mock_client, {}, {})

        assert result.duration_ms == 42.0
        mock_elapsed.assert_called_once()


class TestMetabaseHandlerPreflightCheck:
    """Verdict-path tests for MetabaseHandler.preflight_check().

    The gate tree is: authentication → collection (blocking) → native-query
    (blocking) → dashboard/question (advisory). NOT_READY only via a
    short-circuit; PARTIAL if an advisory check fails; READY otherwise.
    """

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        # Authentication tier passes by default; failure tests override this.
        client.test_connection = AsyncMock(return_value=True)
        return client

    @pytest.fixture
    def handler(self, mock_client):
        return MetabaseHandler(client=mock_client)

    @pytest.fixture
    def handler_no_client(self):
        return MetabaseHandler(client=None)

    @staticmethod
    def _check(name, passed, message="", error=None) -> PreflightCheck:
        return PreflightCheck(name=name, passed=passed, message=message, error=error)

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_all_pass_returns_ready_in_tier_order(
        self, mock_collection, mock_dashboard, mock_question, mock_native, handler
    ):
        """All checks pass → READY, five checks in canonical tier order."""
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
        assert [c.name for c in result.checks] == [
            "authenticationCheck",
            "collectionCountCheck",
            "nativeQueryPermissionCheck",
            "dashboardCountCheck",
            "questionCountCheck",
        ]
        assert all(c.passed for c in result.checks)

    async def test_auth_failure_bad_credentials_blocks_and_short_circuits(
        self, handler, mock_client
    ):
        """Rejected credentials → NOT_READY with a typed AUTH/USER error, no later checks."""
        mock_client.test_connection = AsyncMock(
            side_effect=MetabaseSessionAuthError(
                auth_method="session-token",
                principal="u",
                failure_reason="401",
            )
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.NOT_READY
        assert [c.name for c in result.checks] == ["authenticationCheck"]
        err = result.checks[0].error
        assert err is not None
        assert err.category.name == "AUTH"
        assert err.audience.name == "USER"
        # Clean default message renders on the gate; status stays out of it.
        assert err.message == "Metabase authentication failed."
        assert err.suggested_action is not None
        assert "401" not in err.message

    async def test_auth_failure_unreachable_maps_to_source_unavailable(
        self, handler, mock_client
    ):
        """A transport error → NOT_READY with a SOURCE_UNAVAILABLE error, no leak."""
        mock_client.test_connection = AsyncMock(
            side_effect=ConnectionError("connection refused")
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.NOT_READY
        assert [c.name for c in result.checks] == ["authenticationCheck"]
        assert result.checks[0].error.category.name == "SOURCE_UNAVAILABLE"
        assert "connection refused" not in result.checks[0].message

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_collection_failure_blocks_and_short_circuits(
        self, mock_collection, mock_native, handler
    ):
        """Collection read failure → NOT_READY; native-query check never runs."""
        mock_collection.return_value = self._check(
            "collectionCountCheck",
            False,
            "denied",
            error=MetabaseCollectionAccessError().to_failure_details(),
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.NOT_READY
        assert [c.name for c in result.checks] == [
            "authenticationCheck",
            "collectionCountCheck",
        ]
        mock_native.assert_not_called()
        assert result.checks[1].error.category.name == "PERMISSION"

    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_native_query_failure_blocks_before_advisory_tier(
        self, mock_collection, mock_native, mock_dashboard, mock_question, handler
    ):
        """Native-query permission is blocking: NOT_READY, advisory checks never run."""
        mock_collection.return_value = self._check(
            "collectionCountCheck", True, "Total collections: 3"
        )
        mock_native.return_value = self._check(
            "nativeQueryPermissionCheck",
            False,
            "missing native query permission",
            error=MetabaseNativeQueryPermissionError(
                missing_databases=["BigQuery"]
            ).to_failure_details(),
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.NOT_READY
        assert [c.name for c in result.checks] == [
            "authenticationCheck",
            "collectionCountCheck",
            "nativeQueryPermissionCheck",
        ]
        mock_dashboard.assert_not_called()
        mock_question.assert_not_called()
        err = result.checks[2].error
        assert err.code == "PERMISSION_METABASE_NATIVE_QUERY"
        assert "BigQuery" in err.evidence["missing_databases"]
        assert err.suggested_action is not None

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_dashboard_advisory_failure_returns_partial_and_proceeds(
        self, mock_collection, mock_dashboard, mock_question, mock_native, handler
    ):
        """An advisory dashboard failure downgrades to PARTIAL — the run proceeds."""
        mock_collection.return_value = self._check(
            "collectionCountCheck", True, "Total collections: 3"
        )
        mock_native.return_value = self._check(
            "nativeQueryPermissionCheck", True, "Check successful"
        )
        mock_question.return_value = self._check(
            "questionCountCheck", True, "Total questions: 5"
        )
        mock_dashboard.return_value = self._check(
            "dashboardCountCheck",
            False,
            "dashboards unavailable",
            error=MetabaseSourceUnavailableError(
                message="Failed to fetch Metabase dashboards.", source_type="metabase"
            ).to_failure_details(),
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert len(result.checks) == 5
        dashboard = next(c for c in result.checks if c.name == "dashboardCountCheck")
        assert dashboard.passed is False

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_both_advisory_failures_return_partial(
        self, mock_collection, mock_dashboard, mock_question, mock_native, handler
    ):
        """Both advisory checks failing still only downgrades to PARTIAL."""
        mock_collection.return_value = self._check(
            "collectionCountCheck", True, "Total collections: 3"
        )
        mock_native.return_value = self._check(
            "nativeQueryPermissionCheck", True, "Check successful"
        )
        mock_dashboard.return_value = self._check("dashboardCountCheck", False, "down")
        mock_question.return_value = self._check("questionCountCheck", False, "down")

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert len(result.checks) == 5

    async def test_no_client_no_credentials_blocks_at_authentication(
        self, handler_no_client
    ):
        """No client and no credentials → blocked at the authentication tier.

        The typed message stays a clean sentence — no raw exception text (E019).
        """
        result = await handler_no_client.preflight_check(PreflightInput(credentials=[]))

        assert result.status == PreflightStatus.NOT_READY
        assert result.checks[0].name == "authenticationCheck"
        assert result.checks[0].passed is False
        assert result.checks[0].error is not None
        assert "not initialized" in result.checks[0].message.lower()

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_gate_path_reads_underscore_filter_contract_fields(
        self, mock_collection, mock_dashboard, mock_question, mock_native, handler
    ):
        """Regression for the silent-drift audit.

        On the gate path the typed input carries only the underscore contract
        fields (``include_collections`` / ``exclude_collections``); the handler
        must parse them and pass them to the validators.
        """
        for mock, name in (
            (mock_collection, "collectionCountCheck"),
            (mock_dashboard, "dashboardCountCheck"),
            (mock_question, "questionCountCheck"),
            (mock_native, "nativeQueryPermissionCheck"),
        ):
            mock.return_value = self._check(name, True, "ok")

        gate_input = PreflightInput(
            credentials=_creds(),
            metadata=BaseMetadataConfig.model_validate(
                {
                    "include_collections": {"1": "Engineering"},
                    "exclude_collections": {"9": "Archived"},
                }
            ),
        )

        await handler.preflight_check(gate_input)

        include_arg = mock_collection.call_args.args[1]
        exclude_arg = mock_collection.call_args.args[2]
        assert include_arg == {"1": "Engineering"}
        assert exclude_arg == {"9": "Archived"}


# ---------------------------------------------------------------------------
# Mutation-hardening tests: pin exact values, exact collaborator calls, and
# exact log calls so seeded bugs (mutants) in app/handler.py fail loudly.
# ---------------------------------------------------------------------------

_HOST = "https://myinstance.metabaseapp.com"


def _api_client(**overrides):
    """MagicMock MetabaseApiClient with host/port and async close()."""
    client = MagicMock(spec=MetabaseApiClient)
    client.host = _HOST
    client.port = 443
    client.close = AsyncMock()
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


class TestMetabaseHandlerTestAuthContract:
    """Exact-contract tests for test_auth's build-client path and logging."""

    async def test_auth_builds_client_from_parsed_credentials_and_closes_it(self):
        """No pre-built client: parse creds → build client → test → close."""
        handler = MetabaseHandler(client=None)
        built = _api_client(test_connection=AsyncMock(return_value=True))
        sentinel_credential = object()
        creds = _creds()

        with (
            patch(
                "app.handler.parse_metabase_credentials",
                return_value=sentinel_credential,
            ) as mock_parse,
            patch(
                "app.handler.build_client", new_callable=AsyncMock, return_value=built
            ) as mock_build,
        ):
            result = await handler.test_auth(AuthInput(credentials=creds))

        assert result.status == AuthStatus.SUCCESS
        assert result.message == "Authentication successful"
        mock_parse.assert_called_once_with(creds)
        mock_build.assert_awaited_once_with(sentinel_credential)
        built.test_connection.assert_awaited_once_with()
        built.close.assert_awaited_once_with()

    async def test_auth_no_credentials_raises_typed_error_with_exact_fields(self):
        """The not-initialized error is constructed with exact message/field."""
        handler = MetabaseHandler(client=None)

        with patch("app.handler.MetabaseClientNotInitializedError") as mock_err_cls:
            result = await handler.test_auth(AuthInput(credentials=[]))

        assert result.status == AuthStatus.FAILED
        mock_err_cls.assert_called_once_with(
            message="Metabase client not initialized",
            field="credentials",
        )

    async def test_auth_failure_logs_warning_with_exc_info(self):
        """The swallowed failure is logged verbatim with the traceback attached."""
        client = _api_client(test_connection=AsyncMock(side_effect=Exception("boom")))
        handler = MetabaseHandler(client=client)

        with patch("app.handler.logger") as mock_logger:
            result = await handler.test_auth(AuthInput(credentials=_creds()))

        assert result.status == AuthStatus.FAILED
        mock_logger.warning.assert_called_once_with(
            "Metabase auth failed", exc_info=True
        )


class TestMetabaseHandlerFetchMetadataContract:
    """Client ownership, defaults, and logging contract of fetch_metadata."""

    @staticmethod
    def _response(collections):
        response = MagicMock()
        response.is_success = True
        response.json.return_value = collections
        return response

    async def test_fetch_metadata_builds_client_from_credentials_and_closes_it(self):
        """Without a pre-built client the handler builds one and closes it."""
        built = _api_client()
        built.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [
                    {"id": 1, "name": "Engineering", "personal_owner_id": None},
                    {"id": 2, "name": "Personal", "personal_owner_id": 99},
                ]
            )
        )
        handler = MetabaseHandler(client=None)
        creds = _creds()

        with (
            patch.object(
                MetabaseHandler,
                "_client_for",
                new_callable=AsyncMock,
                return_value=built,
            ) as mock_client_for,
            patch("app.handler.logger") as mock_logger,
        ):
            result = await handler.fetch_metadata(MetadataInput(credentials=creds))

        assert [o.value for o in result.objects] == ["1"]
        mock_client_for.assert_awaited_once_with(creds)
        built.close.assert_awaited_once_with()
        mock_logger.info.assert_called_once_with(
            "fetch_metadata: returning %d non-personal collections "
            "(filtered from %d total)",
            1,
            2,
        )

    async def test_fetch_metadata_does_not_close_prebuilt_client(self):
        """A pre-built (injected) client is never closed by the handler."""
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [{"id": 1, "name": "Engineering", "personal_owner_id": None}]
            )
        )
        handler = MetabaseHandler(client=client)

        await handler.fetch_metadata(MetadataInput(credentials=_creds()))

        client.close.assert_not_called()

    async def test_fetch_metadata_missing_name_defaults_to_empty_title(self):
        """A collection without a name maps to title '' (not 'None')."""
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response([{"id": 3, "personal_owner_id": None}])
        )
        handler = MetabaseHandler(client=client)

        result = await handler.fetch_metadata(MetadataInput(credentials=_creds()))

        assert result.objects[0].title == ""
        assert result.objects[0].value == "3"


class TestMetabaseHandlerClientFor:
    """Direct contract tests for the _client_for helper."""

    async def test_client_for_no_credentials_raises_with_exact_message_and_field(self):
        handler = MetabaseHandler(client=None)

        with pytest.raises(MetabaseClientNotInitializedError) as exc_info:
            await handler._client_for([])

        exc = exc_info.value
        assert exc.message == "Metabase client not initialized"
        assert exc.field == "credentials"

    async def test_client_for_parses_credentials_and_builds_client(self):
        handler = MetabaseHandler(client=None)
        sentinel_credential = object()
        built = _api_client()
        creds = _creds()

        with (
            patch(
                "app.handler.parse_metabase_credentials",
                return_value=sentinel_credential,
            ) as mock_parse,
            patch(
                "app.handler.build_client", new_callable=AsyncMock, return_value=built
            ) as mock_build,
        ):
            result = await handler._client_for(creds)

        assert result is built
        mock_parse.assert_called_once_with(creds)
        mock_build.assert_awaited_once_with(sentinel_credential)


class TestMetabaseHandlerAuthenticationCheck:
    """Direct contract tests for the _authentication_check tier."""

    async def test_success_returns_exact_check_and_the_client(self):
        client = _api_client(test_connection=AsyncMock(return_value=True))
        handler = MetabaseHandler(client=client)

        with patch.object(MetabaseHandler, "_elapsed_ms", return_value=42.0):
            check, returned = await handler._authentication_check(_creds())

        assert returned is client
        assert check.name == "authenticationCheck"
        assert check.passed is True
        assert check.message == "Authentication successful"
        assert check.duration_ms == 42.0

    async def test_generic_failure_maps_to_source_unavailable_with_exact_fields(self):
        client = _api_client(
            test_connection=AsyncMock(side_effect=ConnectionError("refused"))
        )
        handler = MetabaseHandler(client=client)

        with patch("app.handler.logger") as mock_logger:
            check, returned = await handler._authentication_check(_creds())

        assert returned is None
        assert check.passed is False
        assert check.error is not None
        assert check.error.message == "Could not reach the Metabase host."
        assert check.error.evidence == {
            "source_type": "metabase",
            "endpoint": None,
            "http_status": None,
            "network_error": None,
        }
        assert check.error.cause_repr == "ConnectionError: refused"
        mock_logger.warning.assert_called_once_with(
            "authenticationCheck failed", exc_info=True
        )

    async def test_failure_closes_freshly_built_client(self):
        """A client built by this check is closed when the check fails."""
        built = _api_client(
            test_connection=AsyncMock(side_effect=ConnectionError("refused"))
        )
        handler = MetabaseHandler(client=None)
        creds = _creds()

        with patch.object(
            MetabaseHandler,
            "_client_for",
            new_callable=AsyncMock,
            return_value=built,
        ) as mock_client_for:
            check, returned = await handler._authentication_check(creds)

        assert returned is None
        mock_client_for.assert_awaited_once_with(creds)
        built.close.assert_awaited_once_with()

    async def test_failure_does_not_close_prebuilt_client(self):
        """An injected client survives a failed authentication check."""
        client = _api_client(
            test_connection=AsyncMock(side_effect=ConnectionError("refused"))
        )
        handler = MetabaseHandler(client=client)

        check, returned = await handler._authentication_check(_creds())

        assert returned is None
        client.close.assert_not_called()


class TestMetabaseHandlerResolveFilters:
    """_resolve_filters must prefer metadata and only then connection_config."""

    def test_metadata_include_only_wins_over_connection_config(self):
        handler = MetabaseHandler(client=None)
        input = PreflightInput.model_validate(
            {
                "credentials": [],
                "metadata": {"include_collections": {"1": "Engineering"}},
                "connection_config": {"include-collections": {"7": "Other"}},
            }
        )

        include, exclude = handler._resolve_filters(input)

        assert include == {"1": "Engineering"}
        assert exclude == {}

    def test_metadata_exclude_only_wins_over_connection_config(self):
        handler = MetabaseHandler(client=None)
        input = PreflightInput.model_validate(
            {
                "credentials": [],
                "metadata": {"exclude_collections": {"9": "Archived"}},
                "connection_config": {"exclude-collections": {"7": "Other"}},
            }
        )

        include, exclude = handler._resolve_filters(input)

        assert include == {}
        assert exclude == {"9": "Archived"}

    def test_empty_metadata_falls_back_to_connection_config(self):
        handler = MetabaseHandler(client=None)
        input = PreflightInput.model_validate(
            {
                "credentials": [],
                "connection_config": {
                    "include-collections": {"1": "Engineering"},
                    "exclude-collections": {"9": "Archived"},
                },
            }
        )

        include, exclude = handler._resolve_filters(input)

        assert include == {"1": "Engineering"}
        assert exclude == {"9": "Archived"}

    def test_input_without_connection_config_attribute_yields_empty_filters(self):
        """Older inputs may lack connection_config entirely — tolerate it."""
        handler = MetabaseHandler(client=None)

        duck_input = cast("PreflightInput", SimpleNamespace(metadata=None))

        include, exclude = handler._resolve_filters(duck_input)

        assert include == {}
        assert exclude == {}


class TestMetabaseHandlerElapsedAndFailedCheck:
    """Numeric and defaulting contracts of the static helpers."""

    def test_elapsed_ms_is_rounded_millisecond_delta(self):
        with patch("app.handler.time.perf_counter", return_value=2.123456789):
            result = MetabaseHandler._elapsed_ms(1.0)

        assert result == 1123.46

    def test_failed_check_tolerates_error_without_message_attribute(self):
        details = MetabaseSourceUnavailableError(message="detail").to_failure_details()

        class _DetailOnlyError:
            def to_failure_details(self):
                return details

        with patch.object(MetabaseHandler, "_elapsed_ms", return_value=42.0):
            check = MetabaseHandler._failed_check("someCheck", _DetailOnlyError(), 0.0)

        assert check.name == "someCheck"
        assert check.passed is False
        assert check.message == ""
        assert check.error is details
        assert check.duration_ms == 42.0


class TestMetabaseHandlerReadFilters:
    """Wire-shape tolerance of the _read_filters static helper."""

    def test_parses_json_string_filters(self):
        include, exclude = MetabaseHandler._read_filters(
            {
                "include-collections": '{"1": "Engineering"}',
                "exclude-collections": '{"9": "Archived"}',
            }
        )

        assert include == {"1": "Engineering"}
        assert exclude == {"9": "Archived"}

    def test_invalid_json_string_treated_as_empty_and_logged(self):
        with patch("app.handler.logger") as mock_logger:
            include, exclude = MetabaseHandler._read_filters(
                {"include-collections": "not json"}
            )

        assert include == {}
        assert exclude == {}
        mock_logger.warning.assert_called_once_with(
            "Collection filter %r is not valid JSON; treating as empty",
            "not json",
            exc_info=True,
        )

    def test_reads_hyphenated_keys_from_plain_dict(self):
        include, exclude = MetabaseHandler._read_filters(
            {
                "include-collections": {"1": "Engineering"},
                "exclude-collections": {"9": "Archived"},
            }
        )

        assert include == {"1": "Engineering"}
        assert exclude == {"9": "Archived"}

    def test_non_dict_metadata_yields_empty_filters(self):
        assert MetabaseHandler._read_filters(42) == ({}, {})

    def test_none_metadata_yields_empty_filters(self):
        assert MetabaseHandler._read_filters(None) == ({}, {})


class TestMetabaseHandlerFetchCollections:
    """Request and failure contract of _fetch_collections."""

    async def test_success_requests_collection_url_and_returns_body(self):
        client = _api_client()
        body = [{"id": 1, "name": "Engineering"}]
        response = MagicMock()
        response.is_success = True
        response.json.return_value = body
        client.execute_http_get_request = AsyncMock(return_value=response)

        result = await MetabaseHandler._fetch_collections(client)

        assert result == body
        client.execute_http_get_request.assert_awaited_once_with(
            url=f"{_HOST}:443/api/collection", timeout=30
        )

    async def test_http_failure_raises_with_exact_typed_fields(self):
        client = _api_client()
        response = MagicMock()
        response.is_success = False
        response.status_code = 500
        client.execute_http_get_request = AsyncMock(return_value=response)

        with pytest.raises(MetabaseSourceUnavailableError) as exc_info:
            await MetabaseHandler._fetch_collections(client)

        exc = exc_info.value
        assert exc.message == "Failed to fetch collections — HTTP 500"
        assert exc.source_type == "metabase"
        assert exc.endpoint == "/api/collection"
        assert exc.http_status == 500

    async def test_missing_response_raises_no_response_status(self):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(return_value=None)

        with pytest.raises(MetabaseSourceUnavailableError) as exc_info:
            await MetabaseHandler._fetch_collections(client)

        exc = exc_info.value
        assert exc.message == "Failed to fetch collections — HTTP No response"
        assert exc.http_status is None


class TestMetabaseHandlerCollectionCountContract:
    """Exact filtering, error-mapping, and logging of _validate_collection_count."""

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_counts_every_matching_collection_exactly(self, mock_fetch):
        """Skips (personal / excluded / not-included) must not stop the loop."""
        client = _api_client()
        mock_fetch.return_value = [
            {"id": 99, "name": "Personal", "personal_owner_id": 5},
            {"id": 50, "name": "NotIncluded", "personal_owner_id": None},
            {"id": 60, "name": "Excluded", "personal_owner_id": None},
            {"id": 1, "name": "KeepA", "personal_owner_id": None},
            {"id": 2, "name": "KeepB", "personal_owner_id": None},
        ]

        result = await MetabaseHandler._validate_collection_count(
            client, {"1": "KeepA", "2": "KeepB", "60": "Excluded"}, {"60": "Excluded"}
        )

        mock_fetch.assert_called_once_with(client)
        assert result.passed is True
        assert result.message == "Total collections: 2"

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_collection_without_id_uses_empty_string_key(self, mock_fetch):
        """A missing id coerces to '' (not 'None') for filter matching."""
        client = _api_client()
        mock_fetch.return_value = [
            {"name": "NoId", "personal_owner_id": None},
            {"id": 1, "name": "Keep", "personal_owner_id": None},
        ]

        result = await MetabaseHandler._validate_collection_count(
            client, {}, {"": "NoId"}
        )

        assert result.passed is True
        assert result.message == "Total collections: 1"

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_401_maps_to_permission_error_with_cause(self, mock_fetch):
        source = MetabaseSourceUnavailableError(
            message="Failed to fetch collections — HTTP 401",
            source_type="metabase",
            endpoint="/api/collection",
            http_status=401,
        )
        mock_fetch.side_effect = source
        client = _api_client()

        result = await MetabaseHandler._validate_collection_count(client, {}, {})

        assert result.passed is False
        assert result.error is not None
        assert result.error.code == "PERMISSION_METABASE_COLLECTION"
        assert result.error.cause_repr is not None
        assert result.error.cause_repr.startswith("MetabaseSourceUnavailableError")

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_non_permission_http_failure_keeps_source_error(self, mock_fetch):
        mock_fetch.side_effect = MetabaseSourceUnavailableError(
            message="Failed to fetch collections — HTTP 500",
            source_type="metabase",
            endpoint="/api/collection",
            http_status=500,
        )
        client = _api_client()

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_collection_count(client, {}, {})

        assert result.name == "collectionCountCheck"
        assert result.passed is False
        assert result.error is not None
        assert result.error.code == "SOURCE_UNAVAILABLE_METABASE"
        mock_logger.warning.assert_called_once_with(
            "collectionCountCheck failed", exc_info=True
        )

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_generic_failure_wraps_with_exact_source_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("boom")
        client = _api_client()

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_collection_count(client, {}, {})

        assert result.name == "collectionCountCheck"
        assert result.passed is False
        assert result.error is not None
        assert result.error.message == "Failed to fetch Metabase collections."
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/collection",
            "http_status": None,
            "network_error": None,
        }
        assert result.error.cause_repr == "RuntimeError: boom"
        mock_logger.warning.assert_called_once_with(
            "collectionCountCheck failed", exc_info=True
        )


class TestMetabaseHandlerDashboardCountContract:
    """Exact filtering, request, and failure contract of _validate_dashboard_count."""

    @staticmethod
    def _response(body, is_success=True, status_code=200):
        response = MagicMock()
        response.is_success = is_success
        response.status_code = status_code
        response.json.return_value = body
        return response

    @patch.object(MetabaseHandler, "_elapsed_ms", return_value=42.0)
    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_requests_dashboard_url_and_counts_exactly(
        self, mock_fetch, mock_elapsed
    ):
        """Skips (excluded / not-included) must not stop the loop."""
        client = _api_client()
        mock_fetch.return_value = []
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [
                    {"id": 10, "collection_id": 7},
                    {"id": 11, "collection_id": 5},
                    {"id": 12, "collection_id": 1},
                    {"id": 13, "collection_id": 2},
                ]
            )
        )

        result = await MetabaseHandler._validate_dashboard_count(
            client, {"1": "KeepA", "2": "KeepB", "7": "Excluded"}, {"7": "Excluded"}
        )

        mock_fetch.assert_called_once_with(client)
        client.execute_http_get_request.assert_awaited_once_with(
            url=f"{_HOST}:443/api/dashboard", timeout=30
        )
        assert result.name == "dashboardCountCheck"
        assert result.passed is True
        assert result.message == "Total dashboards: 2"
        assert result.duration_ms == 42.0

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_personal_collection_without_id_excludes_blank_dashboards(
        self, mock_fetch
    ):
        """A personal collection missing 'id' excludes dashboards with no
        collection_id — both sides coerce to '' (never 'None')."""
        client = _api_client()
        mock_fetch.return_value = [{"name": "Personal", "personal_owner_id": 5}]
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [
                    {"id": 10},
                    {"id": 11, "collection_id": 1},
                ]
            )
        )

        result = await MetabaseHandler._validate_dashboard_count(client, {}, {})

        assert result.passed is True
        assert result.message == "Total dashboards: 1"

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_http_failure_returns_exact_typed_failure(self, mock_fetch):
        client = _api_client()
        mock_fetch.return_value = []
        client.execute_http_get_request = AsyncMock(
            return_value=self._response([], is_success=False, status_code=500)
        )

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_dashboard_count(client, {}, {})

        assert result.name == "dashboardCountCheck"
        assert result.passed is False
        assert result.message == "Failed to fetch dashboards — HTTP 500"
        assert result.error is not None
        assert result.error.message == "Failed to fetch dashboards — HTTP 500"
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/dashboard",
            "http_status": 500,
            "network_error": None,
        }
        mock_logger.warning.assert_called_once_with(
            "dashboardCountCheck failed", exc_info=True
        )

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_missing_response_reports_no_response(self, mock_fetch):
        client = _api_client()
        mock_fetch.return_value = []
        client.execute_http_get_request = AsyncMock(return_value=None)

        result = await MetabaseHandler._validate_dashboard_count(client, {}, {})

        assert result.passed is False
        assert result.error is not None
        assert result.error.message == "Failed to fetch dashboards — HTTP No response"
        assert result.error.evidence["http_status"] is None

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_generic_failure_wraps_with_exact_source_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("boom")
        client = _api_client()

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_dashboard_count(client, {}, {})

        assert result.name == "dashboardCountCheck"
        assert result.passed is False
        assert result.error is not None
        assert result.error.message == "Failed to fetch Metabase dashboards."
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/dashboard",
            "http_status": None,
            "network_error": None,
        }
        assert result.error.cause_repr == "RuntimeError: boom"
        mock_logger.warning.assert_called_once_with(
            "dashboardCountCheck failed", exc_info=True
        )


class TestMetabaseHandlerQuestionCountContract:
    """Exact filtering, request, and failure contract of _validate_question_count."""

    @staticmethod
    def _response(body, is_success=True, status_code=200):
        response = MagicMock()
        response.is_success = is_success
        response.status_code = status_code
        response.json.return_value = body
        return response

    @patch.object(MetabaseHandler, "_elapsed_ms", return_value=42.0)
    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_requests_card_url_and_counts_exactly(self, mock_fetch, mock_elapsed):
        """Skips (excluded / not-included) must not stop the loop."""
        client = _api_client()
        mock_fetch.return_value = []
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [
                    {"id": 20, "collection_id": 7},
                    {"id": 21, "collection_id": 5},
                    {"id": 22, "collection_id": 1},
                    {"id": 23, "collection_id": 2},
                ]
            )
        )

        result = await MetabaseHandler._validate_question_count(
            client, {"1": "KeepA", "2": "KeepB", "7": "Excluded"}, {"7": "Excluded"}
        )

        mock_fetch.assert_called_once_with(client)
        client.execute_http_get_request.assert_awaited_once_with(
            url=f"{_HOST}:443/api/card", timeout=30
        )
        assert result.name == "questionCountCheck"
        assert result.passed is True
        assert result.message == "Total questions: 2"
        assert result.duration_ms == 42.0

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_personal_collections_exclude_their_questions_by_exact_id(
        self, mock_fetch
    ):
        """Personal ownership is read from 'personal_owner_id' and the exclusion
        key is the stringified collection id ('' when the id is missing)."""
        client = _api_client()
        mock_fetch.return_value = [
            {"id": 9, "personal_owner_id": 5},
            {"personal_owner_id": 6},
        ]
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [
                    {"id": 20, "collection_id": 9},
                    {"id": 21},
                    {"id": 22, "collection_id": 1},
                ]
            )
        )

        result = await MetabaseHandler._validate_question_count(client, {}, {})

        assert result.passed is True
        assert result.message == "Total questions: 1"

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_http_failure_returns_exact_typed_failure(self, mock_fetch):
        client = _api_client()
        mock_fetch.return_value = []
        client.execute_http_get_request = AsyncMock(
            return_value=self._response([], is_success=False, status_code=403)
        )

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_question_count(client, {}, {})

        assert result.name == "questionCountCheck"
        assert result.passed is False
        assert result.message == "Failed to fetch questions — HTTP 403"
        assert result.error is not None
        assert result.error.message == "Failed to fetch questions — HTTP 403"
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/card",
            "http_status": 403,
            "network_error": None,
        }
        mock_logger.warning.assert_called_once_with(
            "questionCountCheck failed", exc_info=True
        )

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_missing_response_reports_no_response(self, mock_fetch):
        client = _api_client()
        mock_fetch.return_value = []
        client.execute_http_get_request = AsyncMock(return_value=None)

        result = await MetabaseHandler._validate_question_count(client, {}, {})

        assert result.passed is False
        assert result.error is not None
        assert result.error.message == "Failed to fetch questions — HTTP No response"
        assert result.error.evidence["http_status"] is None

    @patch.object(MetabaseHandler, "_fetch_collections", new_callable=AsyncMock)
    async def test_generic_failure_wraps_with_exact_source_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("boom")
        client = _api_client()

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_question_count(client, {}, {})

        assert result.name == "questionCountCheck"
        assert result.passed is False
        assert result.error is not None
        assert result.error.message == "Failed to fetch Metabase questions."
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/card",
            "http_status": None,
            "network_error": None,
        }
        assert result.error.cause_repr == "RuntimeError: boom"
        mock_logger.warning.assert_called_once_with(
            "questionCountCheck failed", exc_info=True
        )


class TestMetabaseHandlerNativeQueryContract:
    """Request, parsing, and failure contract of _validate_native_query_permission."""

    @staticmethod
    def _response(body, is_success=True, status_code=200):
        response = MagicMock()
        response.is_success = is_success
        response.status_code = status_code
        response.json.return_value = body
        return response

    @patch.object(MetabaseHandler, "_elapsed_ms", return_value=42.0)
    async def test_requests_database_url_and_returns_exact_success(self, mock_elapsed):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                {
                    "data": [
                        {"id": 1, "name": "Snowflake", "native_permissions": "write"}
                    ]
                }
            )
        )

        result = await MetabaseHandler._validate_native_query_permission(client)

        client.execute_http_get_request.assert_awaited_once_with(
            url=f"{_HOST}:443/api/database", timeout=30
        )
        assert result.name == "nativeQueryPermissionCheck"
        assert result.passed is True
        assert result.message == "Check successful"
        assert result.duration_ms == 42.0

    async def test_bare_list_with_missing_permission_fails(self):
        """Older bare-list responses must still surface missing databases."""
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                [{"id": 1, "name": "Snowflake", "native_permissions": "read"}]
            )
        )

        result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.name == "nativeQueryPermissionCheck"
        assert result.passed is False
        assert result.error is not None
        assert result.error.evidence["missing_databases"] == ["Snowflake"]

    async def test_missing_database_name_falls_back_to_stringified_id(self):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response(
                {"data": [{"id": 7, "native_permissions": "read"}]}
            )
        )

        result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.passed is False
        assert result.error is not None
        assert result.error.evidence["missing_databases"] == ["7"]

    async def test_missing_name_and_id_falls_back_to_empty_string(self):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response({"data": [{"native_permissions": "read"}]})
        )

        result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.passed is False
        assert result.error is not None
        assert result.error.evidence["missing_databases"] == [""]

    async def test_non_list_data_payload_is_treated_as_empty(self):
        """A malformed 'data' payload must not crash — treated as no databases."""
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response({"data": "not-a-list"})
        )

        result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.passed is True
        assert result.message == "Check successful"

    async def test_http_failure_returns_exact_typed_failure(self):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(
            return_value=self._response([], is_success=False, status_code=503)
        )

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.name == "nativeQueryPermissionCheck"
        assert result.passed is False
        assert result.message == "Failed to fetch database list — HTTP 503"
        assert result.error is not None
        assert result.error.message == "Failed to fetch database list — HTTP 503"
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/database",
            "http_status": 503,
            "network_error": None,
        }
        mock_logger.warning.assert_called_once_with(
            "nativeQueryPermissionCheck failed", exc_info=True
        )

    async def test_missing_response_reports_no_response(self):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(return_value=None)

        result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.passed is False
        assert result.error is not None
        assert (
            result.error.message == "Failed to fetch database list — HTTP No response"
        )
        assert result.error.evidence["http_status"] is None

    async def test_generic_failure_wraps_with_exact_source_error(self):
        client = _api_client()
        client.execute_http_get_request = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("app.handler.logger") as mock_logger:
            result = await MetabaseHandler._validate_native_query_permission(client)

        assert result.name == "nativeQueryPermissionCheck"
        assert result.passed is False
        assert result.error is not None
        assert result.error.message == "Failed to fetch the Metabase database list."
        assert result.error.evidence == {
            "source_type": "metabase",
            "endpoint": "/api/database",
            "http_status": None,
            "network_error": None,
        }
        assert result.error.cause_repr == "RuntimeError: boom"
        mock_logger.warning.assert_called_once_with(
            "nativeQueryPermissionCheck failed", exc_info=True
        )


class TestMetabaseHandlerPreflightWiring:
    """preflight_check must pass the exact client and filters to every tier."""

    @staticmethod
    def _passing(name):
        return PreflightCheck(name=name, passed=True, message="ok")

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_validators_receive_exact_client_and_filters(
        self, mock_collection, mock_dashboard, mock_question, mock_native
    ):
        client = _api_client(test_connection=AsyncMock(return_value=True))
        handler = MetabaseHandler(client=client)
        mock_collection.return_value = self._passing("collectionCountCheck")
        mock_native.return_value = self._passing("nativeQueryPermissionCheck")
        mock_dashboard.return_value = self._passing("dashboardCountCheck")
        mock_question.return_value = self._passing("questionCountCheck")
        include = {"1": "Engineering"}
        exclude = {"9": "Archived"}

        result = await handler.preflight_check(
            PreflightInput(
                credentials=_creds(),
                metadata=BaseMetadataConfig.model_validate(
                    {"include_collections": include, "exclude_collections": exclude}
                ),
            )
        )

        assert result.status == PreflightStatus.READY
        mock_collection.assert_called_once_with(client, include, exclude)
        mock_native.assert_called_once_with(client)
        mock_dashboard.assert_called_once_with(client, include, exclude)
        mock_question.assert_called_once_with(client, include, exclude)
        # The injected client is not owned by the handler — never closed.
        client.close.assert_not_called()

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_question_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_dashboard_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    @patch.object(MetabaseHandler, "_authentication_check", new_callable=AsyncMock)
    async def test_owned_client_is_closed_and_auth_gets_exact_credentials(
        self, mock_auth, mock_collection, mock_dashboard, mock_question, mock_native
    ):
        built = _api_client()
        handler = MetabaseHandler(client=None)
        creds = _creds()
        mock_auth.return_value = (
            PreflightCheck(name="authenticationCheck", passed=True, message="ok"),
            built,
        )
        mock_collection.return_value = self._passing("collectionCountCheck")
        mock_native.return_value = self._passing("nativeQueryPermissionCheck")
        mock_dashboard.return_value = self._passing("dashboardCountCheck")
        mock_question.return_value = self._passing("questionCountCheck")

        result = await handler.preflight_check(PreflightInput(credentials=creds))

        assert result.status == PreflightStatus.READY
        mock_auth.assert_awaited_once_with(creds)
        built.close.assert_awaited_once_with()
