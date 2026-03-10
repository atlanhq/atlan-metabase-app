"""Unit tests for app.extracts.dashboards."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.client import MetabaseApiClient
from app.extracts.dashboards import (
    fetch_dashboard_details,
    fetch_dashboards_details,
    fetch_dashboards_summaries,
)


class TestFetchDashboardsSummaries:
    """Tests for fetch_dashboards_summaries()."""

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
        """A 200 response returns the parsed list of dashboard summaries."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 10, "name": "Sales Dashboard", "collection_id": 1},
            {"id": 11, "name": "Ops Dashboard", "collection_id": 2},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client)

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_success_calls_dashboard_endpoint(self, mock_client):
        """The correct /api/dashboard URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_dashboards_summaries(mock_client)

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/dashboard" in called_url

    async def test_success_preserves_dashboard_fields(self, mock_client):
        """Records in the result retain their original fields."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 42, "name": "Revenue", "collection_id": 5, "archived": False},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client)

        assert result[0]["id"] == 42
        assert result[0]["collection_id"] == 5

    async def test_empty_response_returns_empty_list(self, mock_client):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client)

        assert result == []

    # -------------------------------------------------------------------------
    # Failure paths
    # -------------------------------------------------------------------------

    async def test_non_200_returns_empty_list(self, mock_client):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client)

        assert result == []

    async def test_none_response_returns_empty_list(self, mock_client):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_dashboards_summaries(mock_client)

        assert result == []


class TestFetchDashboardDetails:
    """Tests for fetch_dashboard_details() single-ID wrapper."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_success_returns_detail_dict(self, mock_client):
        """Successful GET returns the detail dict."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "id": 10,
            "name": "Sales Dashboard",
            "ordered_cards": [{"card_id": 20, "card": {"id": 20, "name": "Revenue Q"}}],
        }
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboard_details(mock_client, 10)

        assert result is not None
        assert result["id"] == 10
        assert "ordered_cards" in result

    async def test_success_calls_dashboard_detail_endpoint(self, mock_client):
        """The URL must contain /api/dashboard/<id>."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 5}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_dashboard_details(mock_client, 5)

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/dashboard/5" in called_url

    async def test_non_200_returns_none(self, mock_client):
        """Non-success response returns None (silent skip)."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboard_details(mock_client, 99)

        assert result is None

    async def test_none_response_returns_none(self, mock_client):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_dashboard_details(mock_client, 1)

        assert result is None


class TestFetchDashboardsDetails:
    """Tests for fetch_dashboards_details() batch wrapper."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_returns_detail_for_each_summary(self, mock_client):
        """One detail record is fetched and returned per summary."""
        summaries = [{"id": 10}, {"id": 11}]

        detail_10 = {"id": 10, "name": "Sales", "ordered_cards": []}
        detail_11 = {"id": 11, "name": "Ops", "ordered_cards": []}

        async def fake_get(url, **kwargs):
            mock = MagicMock()
            mock.is_success = True
            if "/api/dashboard/10" in url:
                mock.json.return_value = detail_10
            else:
                mock.json.return_value = detail_11
            return mock

        mock_client.execute_http_get_request = fake_get

        result = await fetch_dashboards_details(mock_client, summaries)

        assert len(result) == 2

    async def test_skips_summaries_without_id(self, mock_client):
        """Summary records without 'id' are silently skipped."""
        summaries = [{"name": "No ID Dashboard"}]
        mock_client.execute_http_get_request = AsyncMock()

        result = await fetch_dashboards_details(mock_client, summaries)

        assert result == []
        mock_client.execute_http_get_request.assert_not_called()

    async def test_empty_summaries_returns_empty_list(self, mock_client):
        result = await fetch_dashboards_details(mock_client, [])
        assert result == []
