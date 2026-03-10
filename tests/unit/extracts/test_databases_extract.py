"""Unit tests for app.extracts.databases."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.client import MetabaseApiClient
from app.extracts.databases import (
    fetch_database_metadata,
    fetch_databases_details,
    fetch_databases_summaries,
)


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

    async def test_success_unwraps_data_key_and_returns_list(self, mock_client):
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

        result = await fetch_databases_summaries(mock_client)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["name"] == "Postgres"

    async def test_success_calls_database_endpoint(self, mock_client):
        """The correct /api/database URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"data": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_databases_summaries(mock_client)

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/database" in called_url

    async def test_success_preserves_database_fields(self, mock_client):
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

        result = await fetch_databases_summaries(mock_client)

        assert result[0]["engine"] == "bigquery-cloud-sdk"
        assert result[0]["native_permissions"] == "write"

    # -------------------------------------------------------------------------
    # Missing 'data' key → return []
    # -------------------------------------------------------------------------

    async def test_response_without_data_key_returns_empty_list(self, mock_client):
        """Response without a 'data' key returns []."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"other_key": "value"}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client)

        assert result == []

    async def test_empty_data_list_returns_empty_list(self, mock_client):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"data": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client)

        assert result == []

    # -------------------------------------------------------------------------
    # Failure paths
    # -------------------------------------------------------------------------

    async def test_non_200_returns_empty_list(self, mock_client):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client)

        assert result == []

    async def test_401_response_returns_empty_list(self, mock_client):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 401
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_databases_summaries(mock_client)

        assert result == []

    async def test_none_response_returns_empty_list(self, mock_client):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_databases_summaries(mock_client)

        assert result == []


class TestFetchDatabaseMetadata:
    """Tests for fetch_database_metadata() single-ID wrapper."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_success_returns_metadata_dict(self, mock_client):
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

        result = await fetch_database_metadata(mock_client, 1)

        assert result is not None
        assert result["id"] == 1
        assert "tables" in result

    async def test_success_calls_metadata_endpoint(self, mock_client):
        """The URL must contain /api/database/<id>/metadata."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 7}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_database_metadata(mock_client, 7)

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/database/7/metadata" in called_url

    async def test_non_200_returns_none(self, mock_client):
        """Non-success response returns None (silent skip)."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_database_metadata(mock_client, 99)

        assert result is None

    async def test_none_response_returns_none(self, mock_client):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_database_metadata(mock_client, 1)

        assert result is None


class TestFetchDatabasesDetails:
    """Tests for fetch_databases_details() batch metadata fetcher."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_returns_metadata_for_each_summary(self, mock_client):
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

        result = await fetch_databases_details(mock_client, summaries)

        assert len(result) == 2

    async def test_skips_summaries_without_id(self, mock_client):
        """Summary records without 'id' are silently skipped."""
        summaries = [{"name": "No ID DB"}]
        mock_client.execute_http_get_request = AsyncMock()

        result = await fetch_databases_details(mock_client, summaries)

        assert result == []
        mock_client.execute_http_get_request.assert_not_called()

    async def test_empty_summaries_returns_empty_list(self, mock_client):
        result = await fetch_databases_details(mock_client, [])
        assert result == []

    async def test_api_failure_for_one_db_skips_it(self, mock_client):
        """API failure for a single database causes it to be silently skipped."""
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

        result = await fetch_databases_details(mock_client, summaries)

        # Only id=2 should be present
        assert len(result) == 1
        assert result[0]["id"] == 2
