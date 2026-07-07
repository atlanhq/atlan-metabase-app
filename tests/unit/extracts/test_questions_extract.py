"""Unit tests for app.extracts.questions."""

import os
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from app.client import MetabaseApiClient
from app.extracts.questions import fetch_question_queries, fetch_questions_summaries


def _read_residual_failures(output_path):
    path = os.path.join(output_path, "residual", "failures.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        return [orjson.loads(line) for line in fh if line.strip()]


class TestFetchQuestionsSummaries:
    """Tests for fetch_questions_summaries()."""

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
        """A 200 response returns the parsed list of question summaries."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 20, "name": "Revenue Q", "collection_id": 1, "database_id": 3},
            {"id": 21, "name": "Churn Q", "collection_id": 1, "database_id": 3},
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_questions_summaries(mock_client, str(tmp_path))

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_success_calls_card_endpoint(self, mock_client, tmp_path):
        """The correct /api/card URL is requested."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_questions_summaries(mock_client, str(tmp_path))

        called_url = mock_client.execute_http_get_request.call_args[1]["url"]
        assert "/api/card" in called_url

    async def test_success_preserves_question_fields(self, mock_client, tmp_path):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {
                "id": 20,
                "name": "Revenue Q",
                "collection_id": 1,
                "database_id": 3,
                "dataset_query": {"type": "native"},
            },
        ]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_questions_summaries(mock_client, str(tmp_path))

        assert result[0]["id"] == 20
        assert result[0]["database_id"] == 3

    async def test_empty_response_returns_empty_list(self, mock_client, tmp_path):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        result = await fetch_questions_summaries(mock_client, str(tmp_path))

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

        result = await fetch_questions_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "questions_fetch_failed"
        assert failures[0]["http_status"] == 500

    async def test_none_response_returns_empty_list_and_records_residual(
        self, mock_client, tmp_path
    ):
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        result = await fetch_questions_summaries(mock_client, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1


class TestFetchQuestionQueries:
    """Tests for fetch_question_queries() batch query fetcher."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    # -------------------------------------------------------------------------
    # Success: question with dataset_query → POST and return record
    # -------------------------------------------------------------------------

    async def test_question_with_dataset_query_returns_record(
        self, mock_client, tmp_path
    ):
        """A question with dataset_query is POSTed and returns a result record."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "query": "SELECT 1 FROM orders",
            "params": None,
        }
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        questions = [
            {
                "id": 20,
                "name": "Revenue Q",
                "dataset_query": {"type": "query", "database": 3},
            }
        ]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert len(result) == 1
        assert result[0]["question_id"] == 20
        assert result[0]["query"] == "SELECT 1 FROM orders"

    async def test_result_record_shape(self, mock_client, tmp_path):
        """Result records must have question_id, query, and params."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "query": "SELECT count(*) FROM users",
            "params": {"limit": 100},
        }
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        questions = [
            {
                "id": 5,
                "dataset_query": {"type": "native", "native": {"query": "SELECT 1"}},
            }
        ]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert len(result) == 1
        record = result[0]
        assert "question_id" in record
        assert "query" in record
        assert "params" in record

    # -------------------------------------------------------------------------
    # Skipped: question without dataset_query → silently skipped (returns None)
    # -------------------------------------------------------------------------

    async def test_question_without_dataset_query_is_skipped(
        self, mock_client, tmp_path
    ):
        """Questions with no dataset_query are silently skipped."""
        mock_client.execute_http_post_request = AsyncMock()

        questions = [{"id": 30, "name": "No Query Q"}]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert result == []
        mock_client.execute_http_post_request.assert_not_called()

    async def test_question_with_none_dataset_query_is_skipped(
        self, mock_client, tmp_path
    ):
        """None dataset_query is treated as absent → skipped."""
        mock_client.execute_http_post_request = AsyncMock()

        questions = [{"id": 31, "dataset_query": None}]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert result == []

    # -------------------------------------------------------------------------
    # API error → silently returns None (skipped), recorded as a residual
    # -------------------------------------------------------------------------

    async def test_api_error_returns_none_for_that_question(
        self, mock_client, tmp_path
    ):
        """API errors for individual questions are skipped and recorded."""
        mock_client.execute_http_post_request = AsyncMock(
            side_effect=Exception("Connection timeout")
        )

        questions = [
            {
                "id": 40,
                "dataset_query": {"type": "query", "database": 1},
            }
        ]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "question_query_fetch_errored"
        assert failures[0]["record_id"] == 40

    async def test_non_success_response_skipped(self, mock_client, tmp_path):
        """Non-success API response causes the question to be skipped and recorded."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        questions = [
            {
                "id": 41,
                "dataset_query": {"type": "query"},
            }
        ]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert result == []
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["category"] == "question_query_fetch_failed"
        assert failures[0]["record_id"] == 41
        assert failures[0]["http_status"] == 400

    async def test_empty_query_in_response_skipped(self, mock_client, tmp_path):
        """Response with empty/missing query string causes the record to be skipped."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"query": "", "params": None}
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        questions = [
            {
                "id": 42,
                "dataset_query": {"type": "query"},
            }
        ]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert result == []

    # -------------------------------------------------------------------------
    # Mixed: some succeed, some fail
    # -------------------------------------------------------------------------

    async def test_mixed_success_and_failure(self, mock_client, tmp_path):
        """Only successfully resolved questions appear in the result."""
        good_response = MagicMock()
        good_response.is_success = True
        good_response.json.return_value = {"query": "SELECT 1", "params": None}

        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return good_response
            raise Exception("API error")

        mock_client.execute_http_post_request = AsyncMock(side_effect=side_effect)

        questions = [
            {"id": 50, "dataset_query": {"type": "query"}},
            {"id": 51, "dataset_query": {"type": "query"}},
        ]
        result = await fetch_question_queries(mock_client, questions, str(tmp_path))

        assert len(result) == 1
        assert result[0]["question_id"] == 50
        failures = _read_residual_failures(str(tmp_path))
        assert len(failures) == 1
        assert failures[0]["record_id"] == 51
