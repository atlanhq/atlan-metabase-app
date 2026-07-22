"""Unit tests for app.extracts.dashboards."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app.client import MetabaseApiClient
from app.extracts.dashboards import (
    fetch_dashboard_details,
    fetch_dashboards_details,
    fetch_dashboards_summaries,
)


def _read_residual_failures(output_path):
    path = os.path.join(output_path, "residual", "failures.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        return [orjson.loads(line) for line in fh if line.strip()]


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

    async def test_success_returns_list(self, mock_client, tmp_path):
        """A 200 response returns the parsed list of dashboard summaries."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 10, "name": "Sales Dashboard", "collection_id": 1},
            {"id": 11, "name": "Ops Dashboard", "collection_id": 2},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client, str(tmp_path))

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_success_calls_dashboard_endpoint(self, mock_client, tmp_path):
        """The correct /api/dashboard URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_dashboards_summaries(mock_client, str(tmp_path))

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/dashboard" in called_url

    async def test_success_requests_exact_url_and_timeout(self, mock_client, tmp_path):
        """The URL is exactly host:port/api/dashboard and timeout is exactly 60."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_dashboards_summaries(mock_client, str(tmp_path))

        call_kwargs = mock_client.execute_http_get_request.call_args[1]
        assert (
            call_kwargs["url"] == "https://myinstance.metabaseapp.com:443/api/dashboard"
        )
        assert call_kwargs["timeout"] == 60

    async def test_success_logs_fetched_count(self, mock_client, tmp_path):
        """The success path logs the exact message with the record count."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [{"id": 10}, {"id": 11}]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.dashboards.logger") as mock_logger:
            await fetch_dashboards_summaries(mock_client, str(tmp_path))

        mock_logger.info.assert_called_once_with("Fetched %d dashboard summaries", 2)

    async def test_success_preserves_dashboard_fields(self, mock_client, tmp_path):
        """Records in the result retain their original fields."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 42, "name": "Revenue", "collection_id": 5, "archived": False},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client, str(tmp_path))

        assert result[0]["id"] == 42
        assert result[0]["collection_id"] == 5

    async def test_empty_response_returns_empty_list(self, mock_client, tmp_path):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client, str(tmp_path))

        assert result == []

    # -------------------------------------------------------------------------
    # Failure paths
    # -------------------------------------------------------------------------

    async def test_non_200_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboards_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "dashboards_fetch_failed"
        assert failures[0]["http_status"] == 503
        assert failures[0]["endpoint"] == "/api/dashboard"

    async def test_failure_logs_warning_with_status_code(self, mock_client, tmp_path):
        """A non-success response logs the exact warning with the status code."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.dashboards.logger") as mock_logger:
            await fetch_dashboards_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch dashboards: %s", 503
        )

    async def test_none_response_logs_no_response_sentinel(self, mock_client, tmp_path):
        """A None response logs the exact 'No response' sentinel string."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        with patch("app.extracts.dashboards.logger") as mock_logger:
            await fetch_dashboards_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch dashboards: %s", "No response"
        )

    async def test_none_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_dashboards_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1


class TestFetchDashboardDetails:
    """Tests for fetch_dashboard_details() single-ID wrapper."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_success_returns_detail_dict(self, mock_client, tmp_path):
        """Successful GET returns the detail dict."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "id": 10,
            "name": "Sales Dashboard",
            "ordered_cards": [{"card_id": 20, "card": {"id": 20, "name": "Revenue Q"}}],
        }
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboard_details(mock_client, 10, str(tmp_path))

        assert result is not None
        assert result["id"] == 10
        assert "ordered_cards" in result

    async def test_success_calls_dashboard_detail_endpoint(self, mock_client, tmp_path):
        """The URL must contain /api/dashboard/<id>."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 5}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_dashboard_details(mock_client, 5, str(tmp_path))

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/dashboard/5" in called_url

    async def test_success_requests_exact_url_and_timeout(self, mock_client, tmp_path):
        """The URL is exactly host:port/api/dashboard/<id> and timeout is 60."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 5}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_dashboard_details(mock_client, 5, str(tmp_path))

        call_kwargs = mock_client.execute_http_get_request.call_args[1]
        assert (
            call_kwargs["url"]
            == "https://myinstance.metabaseapp.com:443/api/dashboard/5"
        )
        assert call_kwargs["timeout"] == 60

    async def test_failure_logs_warning_and_records_endpoint(
        self, mock_client, tmp_path
    ):
        """A 404 logs the exact warning args and records the endpoint."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.dashboards.logger") as mock_logger:
            result = await fetch_dashboard_details(mock_client, 99, str(tmp_path))

        assert result is None
        mock_logger.warning.assert_called_once_with(
            "Failed to fetch dashboard detail for id=%s: %s", 99, 404
        )
        failures = _read_residual_failures(str(tmp_path))
        assert failures[0]["endpoint"] == "/api/dashboard"

    async def test_none_response_logs_no_response_sentinel(self, mock_client, tmp_path):
        """A None response logs the exact 'No response' sentinel string."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        with patch("app.extracts.dashboards.logger") as mock_logger:
            await fetch_dashboard_details(mock_client, 1, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch dashboard detail for id=%s: %s", 1, "No response"
        )

    async def test_non_200_returns_none_and_records_residual(
        self, mock_client, tmp_path
    ):
        """Non-success response returns None and records a residual failure."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_dashboard_details(mock_client, 99, str(tmp_path))

        assert result is None
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "dashboard_detail_fetch_failed"
        assert failures[0]["record_id"] == 99
        assert failures[0]["http_status"] == 404

    async def test_none_response_returns_none_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_dashboard_details(mock_client, 1, str(tmp_path))

        assert result is None
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1


class TestFetchDashboardsDetails:
    """Tests for fetch_dashboards_details() batch wrapper."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    async def test_returns_detail_for_each_summary(self, mock_client, tmp_path):
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

        result = await fetch_dashboards_details(mock_client, summaries, str(tmp_path))

        assert len(result) == 2

    async def test_logs_fetched_count(self, mock_client, tmp_path):
        """The batch fetch logs the exact message with the record count."""
        summaries = [{"id": 10}, {"id": 11}]
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 10, "ordered_cards": []}
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.dashboards.logger") as mock_logger:
            await fetch_dashboards_details(mock_client, summaries, str(tmp_path))

        mock_logger.info.assert_called_once_with(
            "Fetched %d dashboard detail records", 2
        )

    async def test_skips_summaries_without_id(self, mock_client, tmp_path):
        """Summary records without 'id' are silently skipped."""
        summaries = [{"name": "No ID Dashboard"}]
        mock_client.execute_http_get_request = AsyncMock()

        result = await fetch_dashboards_details(mock_client, summaries, str(tmp_path))

        assert result == []
        mock_client.execute_http_get_request.assert_not_called()

    async def test_empty_summaries_returns_empty_list(self, mock_client, tmp_path):
        result = await fetch_dashboards_details(mock_client, [], str(tmp_path))
        assert result == []

    async def test_api_failure_for_one_dashboard_is_skipped_and_recorded(
        self, mock_client, tmp_path
    ):
        """A single dashboard's failure is skipped (not aborted) and recorded
        as a residual — the rest of the batch still completes."""
        summaries = [{"id": 10}, {"id": 11}]

        async def fake_get(url, **kwargs):
            mock = MagicMock()
            if "/api/dashboard/10" in url:
                mock.is_success = False
                mock.status_code = 500
            else:
                mock.is_success = True
                mock.json.return_value = {"id": 11, "ordered_cards": []}
            return mock

        mock_client.execute_http_get_request = fake_get

        result = await fetch_dashboards_details(mock_client, summaries, str(tmp_path))

        assert len(result) == 1
        assert result[0]["id"] == 11
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["record_id"] == 10
