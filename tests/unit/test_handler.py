"""Unit tests for app.handler.MetabaseHandler (v3 typed contracts)."""

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
        assert result.status == PreflightStatus.NOT_READY
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
        assert result.status == PreflightStatus.NOT_READY
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
        assert result.status == PreflightStatus.PARTIAL
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
        assert result.status == PreflightStatus.PARTIAL
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
        assert result.status == PreflightStatus.NOT_READY
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
        assert result.status == PreflightStatus.NOT_READY
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
    (blocking) → dashboard/question (advisory).

    Observation window (CNCT-81): blocking intent is recorded per-check
    (``status == NOT_READY``) but the overall verdict is softened so the gate
    never aborts the run — the aggregate is PARTIAL on any failure path and
    READY only when everything passes; it is never NOT_READY. The hard-fail flip
    later reverts only the aggregate on the blocking short-circuits, so these
    per-check ``status`` assertions stay unchanged across the flip.
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
    def _check(name, passed, message="", error=None, status=None) -> PreflightCheck:
        return PreflightCheck(
            name=name, passed=passed, message=message, error=error, status=status
        )

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
        # Passed checks derive READY automatically (no explicit stamp needed).
        assert all(c.status == PreflightStatus.READY for c in result.checks)

    async def test_auth_failure_bad_credentials_blocks_and_short_circuits(
        self, handler, mock_client
    ):
        """Rejected credentials → blocking intent NOT_READY on the check, aggregate
        softened to PARTIAL, no later checks."""
        mock_client.test_connection = AsyncMock(
            side_effect=MetabaseSessionAuthError(
                auth_method="session-token",
                principal="u",
                failure_reason="401",
            )
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert [c.name for c in result.checks] == ["authenticationCheck"]
        assert result.checks[0].status == PreflightStatus.NOT_READY
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
        """A transport error → check NOT_READY with a SOURCE_UNAVAILABLE error,
        aggregate softened to PARTIAL, no leak."""
        mock_client.test_connection = AsyncMock(
            side_effect=ConnectionError("connection refused")
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert [c.name for c in result.checks] == ["authenticationCheck"]
        assert result.checks[0].status == PreflightStatus.NOT_READY
        assert result.checks[0].error.category.name == "SOURCE_UNAVAILABLE"
        assert "connection refused" not in result.checks[0].message

    @patch.object(
        MetabaseHandler, "_validate_native_query_permission", new_callable=AsyncMock
    )
    @patch.object(MetabaseHandler, "_validate_collection_count", new_callable=AsyncMock)
    async def test_collection_failure_blocks_and_short_circuits(
        self, mock_collection, mock_native, handler
    ):
        """Collection read failure → blocking intent NOT_READY on the check,
        aggregate softened to PARTIAL; native-query check never runs."""
        mock_collection.return_value = self._check(
            "collectionCountCheck",
            False,
            "denied",
            error=MetabaseCollectionAccessError().to_failure_details(),
            status=PreflightStatus.NOT_READY,
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert [c.name for c in result.checks] == [
            "authenticationCheck",
            "collectionCountCheck",
        ]
        assert result.checks[1].status == PreflightStatus.NOT_READY
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
        """Native-query permission is blocking: check NOT_READY, aggregate softened
        to PARTIAL, advisory checks never run."""
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
            status=PreflightStatus.NOT_READY,
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert [c.name for c in result.checks] == [
            "authenticationCheck",
            "collectionCountCheck",
            "nativeQueryPermissionCheck",
        ]
        assert result.checks[2].status == PreflightStatus.NOT_READY
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
            status=PreflightStatus.PARTIAL,
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert len(result.checks) == 5
        dashboard = next(c for c in result.checks if c.name == "dashboardCountCheck")
        assert dashboard.passed is False
        assert dashboard.status == PreflightStatus.PARTIAL

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
        mock_dashboard.return_value = self._check(
            "dashboardCountCheck", False, "down", status=PreflightStatus.PARTIAL
        )
        mock_question.return_value = self._check(
            "questionCountCheck", False, "down", status=PreflightStatus.PARTIAL
        )

        result = await handler.preflight_check(PreflightInput(credentials=_creds()))

        assert result.status == PreflightStatus.PARTIAL
        assert len(result.checks) == 5
        advisory = [
            c
            for c in result.checks
            if c.name in ("dashboardCountCheck", "questionCountCheck")
        ]
        assert all(c.status == PreflightStatus.PARTIAL for c in advisory)

    async def test_no_client_no_credentials_blocks_at_authentication(
        self, handler_no_client
    ):
        """No client and no credentials → blocked at the authentication tier.

        The typed message stays a clean sentence — no raw exception text (E019).
        """
        result = await handler_no_client.preflight_check(PreflightInput(credentials=[]))

        assert result.status == PreflightStatus.PARTIAL
        assert result.checks[0].name == "authenticationCheck"
        assert result.checks[0].passed is False
        assert result.checks[0].status == PreflightStatus.NOT_READY
        assert result.checks[0].error is not None
        assert "not initialized" in result.checks[0].message.lower()

    async def test_window_invariant_no_failure_path_returns_not_ready(
        self, handler, mock_client
    ):
        """Observation-window invariant (CNCT-81): no matter which checks fail —
        including every blocking check — the overall verdict is never NOT_READY.

        This is also the guard against a premature de-pin: if the ``status=``
        stamps were dropped by a pre-feature SDK, the blocking short-circuits would
        still be softened here, but the per-check assertions elsewhere would fail
        first. Deleted at the hard-fail flip.
        """
        failed = {
            "collectionCountCheck": PreflightStatus.NOT_READY,
            "nativeQueryPermissionCheck": PreflightStatus.NOT_READY,
            "dashboardCountCheck": PreflightStatus.PARTIAL,
            "questionCountCheck": PreflightStatus.PARTIAL,
        }

        def _fail(name):
            return AsyncMock(
                return_value=self._check(name, False, "down", status=failed[name])
            )

        # Auth failing (real path) also softens to PARTIAL.
        mock_client.test_connection = AsyncMock(side_effect=ConnectionError("refused"))
        result = await handler.preflight_check(PreflightInput(credentials=_creds()))
        assert result.status != PreflightStatus.NOT_READY

        # Every downstream check failing, with auth restored.
        mock_client.test_connection = AsyncMock(return_value=True)
        with (
            patch.object(
                MetabaseHandler,
                "_validate_collection_count",
                _fail("collectionCountCheck"),
            ),
            patch.object(
                MetabaseHandler,
                "_validate_native_query_permission",
                _fail("nativeQueryPermissionCheck"),
            ),
            patch.object(
                MetabaseHandler,
                "_validate_dashboard_count",
                _fail("dashboardCountCheck"),
            ),
            patch.object(
                MetabaseHandler, "_validate_question_count", _fail("questionCountCheck")
            ),
        ):
            result = await handler.preflight_check(PreflightInput(credentials=_creds()))
        assert result.status != PreflightStatus.NOT_READY

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
