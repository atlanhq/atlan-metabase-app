"""Unit tests for app.extracts.collections."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.client import MetabaseApiClient
from app.extracts.collections import fetch_collections_summaries


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

    async def test_success_returns_list(self, mock_client):
        """A 200 response returns the parsed list of collections."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Engineering"},
            {"id": 2, "name": "Marketing"},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client)

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_success_preserves_collection_fields(self, mock_client):
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

        result = await fetch_collections_summaries(mock_client)

        assert result[0]["id"] == 1
        assert result[0]["name"] == "Engineering"
        assert result[0]["personal_owner_id"] is None

    async def test_success_calls_collection_endpoint(self, mock_client):
        """The correct /api/collection URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_collections_summaries(mock_client)

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/collection" in called_url

    async def test_empty_response_returns_empty_list(self, mock_client):
        """A successful response with an empty array returns []."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client)

        assert result == []

    # -------------------------------------------------------------------------
    # Failure paths
    # -------------------------------------------------------------------------

    async def test_non_200_response_returns_empty_list(self, mock_client):
        """Non-success HTTP response returns [] (silent failure)."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client)

        assert result == []

    async def test_401_response_returns_empty_list(self, mock_client):
        """Authentication failure (401) returns []."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 401
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_collections_summaries(mock_client)

        assert result == []

    async def test_none_response_returns_empty_list(self, mock_client):
        """None response returns []."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_collections_summaries(mock_client)

        assert result == []
