"""Unit tests for app.extracts.databases."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app.client import MetabaseApiClient
from app.extracts.databases import (
    fetch_database_metadata,
    fetch_databases_details,
    fetch_databases_summaries,
)


def _read_residual_failures(output_path):
    path = os.path.join(output_path, "residual", "failures.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        return [orjson.loads(line) for line in fh if line.strip()]


class TestFetchDatabasesSummaries:
    """Tests for fetch_databases_summaries()."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    # -------------------------------------------------------------------------
    # Success: response with 'data' key → unwrap and return
    # -------------------------------------------------------------------------

    async def test_success_unwraps_data_key_and_returns_list(
        self, mock_client, tmp_path
    ):
        """Response with 'data' key is unwrapped; the inner list is returned."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "data": [
                {"id": 1, "name": "Snowflake", "engine": "snowflake"},
                {"id": 2, "name": "Postgres", "engine": "postgres"},
            ]
        }
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["name"] == "Postgres"

    async def test_success_calls_database_endpoint(self, mock_client, tmp_path):
        """The correct /api/database URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"data": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_databases_summaries(mock_client, str(tmp_path))

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/database" in called_url

    async def test_success_requests_exact_url_and_timeout(self, mock_client, tmp_path):
        """The URL is exactly host:port/api/database and timeout is exactly 60."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"data": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_databases_summaries(mock_client, str(tmp_path))

        call_kwargs = mock_client.execute_http_get_request.call_args[1]
        assert (
            call_kwargs["url"] == "https://myinstance.metabaseapp.com:443/api/database"
        )
        assert call_kwargs["timeout"] == 60

    async def test_success_logs_fetched_count(self, mock_client, tmp_path):
        """The success path logs the exact message with the record count."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"data": [{"id": 1}, {"id": 2}]}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.databases.logger") as mock_logger:
            await fetch_databases_summaries(mock_client, str(tmp_path))

        mock_logger.info.assert_called_once_with("Fetched %d databases", 2)

    async def test_success_preserves_database_fields(self, mock_client, tmp_path):
        """Fields in each database record are preserved intact."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "data": [
                {
                    "id": 3,
                    "name": "BigQuery",
                    "engine": "bigquery-cloud-sdk",
                    "native_permissions": "write",
                    "details": {"db": "my_dataset"},
                }
            ]
        }
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert result[0]["engine"] == "bigquery-cloud-sdk"
        assert result[0]["native_permissions"] == "write"

    # -------------------------------------------------------------------------
    # Missing 'data' key → return []
    # -------------------------------------------------------------------------

    async def test_response_without_data_key_returns_empty_list(
        self, mock_client, tmp_path
    ):
        """Response without a 'data' key returns []."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"other_key": "value"}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert result == []

    async def test_empty_data_list_returns_empty_list(self, mock_client, tmp_path):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"data": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert result == []

    # -------------------------------------------------------------------------
    # Failure paths
    # -------------------------------------------------------------------------

    async def test_non_200_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "databases_fetch_failed"
        assert failures[0]["http_status"] == 500
        assert failures[0]["endpoint"] == "/api/database"

    async def test_failure_logs_warning_with_status_code(self, mock_client, tmp_path):
        """A non-success response logs the exact warning with the status code."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.databases.logger") as mock_logger:
            await fetch_databases_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch databases: %s", 500
        )

    async def test_none_response_logs_no_response_sentinel(self, mock_client, tmp_path):
        """A None response logs the exact 'No response' sentinel string."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        with patch("app.extracts.databases.logger") as mock_logger:
            await fetch_databases_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch databases: %s", "No response"
        )

    async def test_401_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 401
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1

    async def test_none_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_databases_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1


class TestFetchDatabaseMetadata:
    """Tests for fetch_database_metadata() single-ID wrapper."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_success_returns_metadata_dict(self, mock_client, tmp_path):
        """Successful GET returns the metadata dict."""
        expected = {
            "id": 1,
            "name": "Snowflake",
            "tables": [{"id": 100, "name": "orders", "schema": "public"}],
        }
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = expected
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_database_metadata(mock_client, 1, str(tmp_path))

        assert result is not None
        assert result["id"] == 1
        assert "tables" in result

    async def test_success_calls_metadata_endpoint(self, mock_client, tmp_path):
        """The URL must contain /api/database/<id>/metadata."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 7}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_database_metadata(mock_client, 7, str(tmp_path))

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/database/7/metadata" in called_url

    async def test_success_requests_exact_url_and_timeout(self, mock_client, tmp_path):
        """The URL is exactly host:port/api/database/<id>/metadata, timeout 60."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 7}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_database_metadata(mock_client, 7, str(tmp_path))

        call_kwargs = mock_client.execute_http_get_request.call_args[1]
        assert (
            call_kwargs["url"]
            == "https://myinstance.metabaseapp.com:443/api/database/7/metadata"
        )
        assert call_kwargs["timeout"] == 60

    async def test_failure_logs_warning_and_records_full_residual(
        self, mock_client, tmp_path
    ):
        """A 404 logs the exact warning args and records endpoint + http_status."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.databases.logger") as mock_logger:
            result = await fetch_database_metadata(mock_client, 99, str(tmp_path))

        assert result is None
        mock_logger.warning.assert_called_once_with(
            "Failed to fetch database metadata for id=%s: %s", 99, 404
        )
        failures = _read_residual_failures(str(tmp_path))
        assert failures[0]["endpoint"] == "/api/database"
        assert failures[0]["http_status"] == 404

    async def test_none_response_logs_no_response_sentinel(self, mock_client, tmp_path):
        """A None response logs the exact 'No response' sentinel string."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        with patch("app.extracts.databases.logger") as mock_logger:
            await fetch_database_metadata(mock_client, 1, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch database metadata for id=%s: %s", 1, "No response"
        )
        failures = _read_residual_failures(str(tmp_path))
        assert failures[0]["http_status"] is None

    async def test_non_200_returns_none_and_records_residual(
        self, mock_client, tmp_path
    ):
        """Non-success response returns None and records a residual failure."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_database_metadata(mock_client, 99, str(tmp_path))

        assert result is None
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "database_metadata_fetch_failed"
        assert failures[0]["record_id"] == 99

    async def test_none_response_returns_none_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_database_metadata(mock_client, 1, str(tmp_path))

        assert result is None
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1


class TestFetchDatabasesDetails:
    """Tests for fetch_databases_details() batch metadata fetcher."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_returns_metadata_for_each_summary(self, mock_client, tmp_path):
        """One metadata record is fetched per summary with an id."""
        summaries = [{"id": 1}, {"id": 2}]

        meta_1 = {"id": 1, "tables": []}
        meta_2 = {"id": 2, "tables": [{"name": "orders"}]}

        async def fake_get(url, **kwargs):
            mock = MagicMock()
            mock.is_success = True
            if "/api/database/1/metadata" in url:
                mock.json.return_value = meta_1
            else:
                mock.json.return_value = meta_2
            return mock

        mock_client.execute_http_get_request = fake_get

        result = await fetch_databases_details(mock_client, summaries, str(tmp_path))

        assert len(result) == 2

    async def test_logs_fetched_count(self, mock_client, tmp_path):
        """The batch fetch logs the exact message with the record count."""
        summaries = [{"id": 1}, {"id": 2}]
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 1, "tables": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.databases.logger") as mock_logger:
            await fetch_databases_details(mock_client, summaries, str(tmp_path))

        mock_logger.info.assert_called_once_with(
            "Fetched %d database metadata records", 2
        )

    async def test_skips_summaries_without_id(self, mock_client, tmp_path):
        """Summary records without 'id' are silently skipped."""
        summaries = [{"name": "No ID DB"}]
        mock_client.execute_http_get_request = AsyncMock()

        result = await fetch_databases_details(mock_client, summaries, str(tmp_path))

        assert result == []
        mock_client.execute_http_get_request.assert_not_called()

    async def test_empty_summaries_returns_empty_list(self, mock_client, tmp_path):
        result = await fetch_databases_details(mock_client, [], str(tmp_path))
        assert result == []

    async def test_api_failure_for_one_db_is_skipped_and_recorded(
        self, mock_client, tmp_path
    ):
        """API failure for a single database is skipped (not aborted) and
        recorded as a residual — the rest of the batch still completes."""
        summaries = [{"id": 1}, {"id": 2}]

        async def fake_get(url, **kwargs):
            mock = MagicMock()
            if "/api/database/1/metadata" in url:
                mock.is_success = False
                mock.status_code = 500
            else:
                mock.is_success = True
                mock.json.return_value = {"id": 2, "tables": []}
            return mock

        mock_client.execute_http_get_request = fake_get

        result = await fetch_databases_details(mock_client, summaries, str(tmp_path))

        # Only id=2 should be present
        assert len(result) == 1
        assert result[0]["id"] == 2
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["record_id"] == 1
