"""Unit tests for app.extracts.collections."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app.client import MetabaseApiClient
from app.extracts.collections import fetch_collections_summaries


def _read_residual_failures(output_path):
    path = os.path.join(output_path, "residual", "failures.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        return [orjson.loads(line) for line in fh if line.strip()]


class TestFetchCollectionsSummaries:
    """Tests for fetch_collections_summaries()."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    # -------------------------------------------------------------------------
    # Success path
    # -------------------------------------------------------------------------

    async def test_success_returns_list(self, mock_client, tmp_path):
        """A 200 response returns the parsed list of collections."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering"},
            {"id": 2, "name": "Marketing"},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client, str(tmp_path))

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_success_preserves_collection_fields(self, mock_client, tmp_path):
        """Each record in the result retains its original fields."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {
                "id": 1,
                "name": "Engineering",
                "personal_owner_id": None,
                "archived": False,
            },
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client, str(tmp_path))

        assert result[0]["id"] == 1
        assert result[0]["name"] == "Engineering"
        assert result[0]["personal_owner_id"] is None

    async def test_success_calls_collection_endpoint(self, mock_client, tmp_path):
        """The correct /api/collection URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_collections_summaries(mock_client, str(tmp_path))

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/collection" in called_url

    async def test_success_requests_exact_url_and_timeout(self, mock_client, tmp_path):
        """The URL is exactly host:port/api/collection and timeout is exactly 60."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_collections_summaries(mock_client, str(tmp_path))

        call_kwargs = mock_client.execute_http_get_request.call_args[1]
        assert (
            call_kwargs["url"]
            == "https://myinstance.metabaseapp.com:443/api/collection"
        )
        assert call_kwargs["timeout"] == 60

    async def test_success_logs_fetched_count(self, mock_client, tmp_path):
        """The success path logs the exact message with the record count."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.collections.logger") as mock_logger:
            await fetch_collections_summaries(mock_client, str(tmp_path))

        mock_logger.info.assert_called_once_with("Fetched %d collections", 2)

    async def test_empty_response_returns_empty_list(self, mock_client, tmp_path):
        """A successful response with an empty array returns []."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client, str(tmp_path))

        assert result == []

    # -------------------------------------------------------------------------
    # Failure paths
    # -------------------------------------------------------------------------

    async def test_non_200_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        """Non-success HTTP response returns [] and records a residual failure."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "collections_fetch_failed"
        assert failures[0]["http_status"] == 500
        assert failures[0]["endpoint"] == "/api/collection"

    async def test_failure_logs_warning_with_status_code(self, mock_client, tmp_path):
        """A non-success response logs the exact warning with the status code."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.collections.logger") as mock_logger:
            await fetch_collections_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch collections: %s", 500
        )

    async def test_none_response_logs_no_response_sentinel(self, mock_client, tmp_path):
        """A None response logs the exact 'No response' sentinel string."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        with patch("app.extracts.collections.logger") as mock_logger:
            await fetch_collections_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch collections: %s", "No response"
        )

    async def test_401_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        """Authentication failure (401) returns [] and records a residual failure."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 401
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["http_status"] == 401

    async def test_none_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        """None response returns [] and records a residual failure."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_collections_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["http_status"] is None
