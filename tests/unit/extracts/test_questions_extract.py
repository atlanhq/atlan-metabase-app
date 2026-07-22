"""Unit tests for app.extracts.questions."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app.client import MetabaseApiClient
from app.extracts.questions import (
    fetch_question_queries,
    fetch_question_queries_single,
    fetch_questions_summaries,
)


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

    async def test_request_pins_exact_url_and_timeout(self, mock_client, tmp_path):
        """The GET call uses the exact host:port/api/card URL and a 60s timeout."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = []
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_questions_summaries(mock_client, str(tmp_path))

        assert mock_client.execute_http_get_request.call_args.args == ()
        assert mock_client.execute_http_get_request.call_args.kwargs == {
            "url": "https://myinstance.metabaseapp.com:443/api/card",
            "timeout": 60,
        }

    async def test_success_logs_summary_count(self, mock_client, tmp_path):
        """The success path logs the exact record count message."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = [{"id": 20}, {"id": 21}]
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.questions.logger") as mock_logger:
            await fetch_questions_summaries(mock_client, str(tmp_path))

        mock_logger.info.assert_called_once_with("Fetched %d question summaries", 2)

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

    async def test_failure_logs_warning_with_status_code(self, mock_client, tmp_path):
        """A non-success response logs the exact warning with the HTTP status."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        with patch("app.extracts.questions.logger") as mock_logger:
            await fetch_questions_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch questions: %s", 500
        )

    async def test_none_response_logs_no_response_status(self, mock_client, tmp_path):
        """A missing response logs the literal 'No response' placeholder."""
        mock_client.execute_http_get_request = AsyncMock(return_value=None)

        with patch("app.extracts.questions.logger") as mock_logger:
            await fetch_questions_summaries(mock_client, str(tmp_path))

        mock_logger.warning.assert_called_once_with(
            "Failed to fetch questions: %s", "No response"
        )

    async def test_failure_residual_records_card_endpoint(self, mock_client, tmp_path):
        """The residual failure record pins the /api/card endpoint field."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_client.execute_http_get_request = AsyncMock(return_value=mock_response)

        await fetch_questions_summaries(mock_client, str(tmp_path))

        failures = _read_residual_failures(str(tmp_path))
        assert failures[0]["endpoint"] == "/api/card"


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

    # -------------------------------------------------------------------------
    # Logging contract
    # -------------------------------------------------------------------------

    async def test_batch_logs_record_count(self, mock_client, tmp_path):
        """The batch fetcher logs the exact fetched-record count message."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"query": "SELECT 1", "params": None}
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        questions = [{"id": 5, "dataset_query": {"type": "query"}}]
        with patch("app.extracts.questions.logger") as mock_logger:
            await fetch_question_queries(mock_client, questions, str(tmp_path))

        mock_logger.info.assert_called_once_with("Fetched %d question query records", 1)


class TestFetchQuestionQueriesSingle:
    """Tests for fetch_question_queries_single() — single-question fetcher."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock(spec=MetabaseApiClient)
        client.host = "https://myinstance.metabaseapp.com"
        client.port = 443
        return client

    # -------------------------------------------------------------------------
    # Request contract: exact URL, POST body, and timeout
    # -------------------------------------------------------------------------

    async def test_posts_exact_url_body_and_timeout(self, mock_client, tmp_path):
        """The POST call pins the dataset/native URL, merged body, and timeout."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"query": "SELECT 1", "params": None}
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        await fetch_question_queries_single(
            mock_client, 20, {"type": "query", "database": 3}, str(tmp_path)
        )

        assert mock_client.execute_http_post_request.call_args.args == ()
        assert mock_client.execute_http_post_request.call_args.kwargs == {
            "url": "https://myinstance.metabaseapp.com:443/api/dataset/native",
            "json_data": {"question_id": 20, "type": "query", "database": 3},
            "timeout": 60,
        }

    # -------------------------------------------------------------------------
    # Result contract: exact record shape and values
    # -------------------------------------------------------------------------

    async def test_returns_exact_record_with_params(self, mock_client, tmp_path):
        """The returned record propagates the query and the params value."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "query": "SELECT count(*) FROM users",
            "params": {"limit": 100},
        }
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        result = await fetch_question_queries_single(
            mock_client, 5, {"type": "native"}, str(tmp_path)
        )

        assert result == {
            "question_id": 5,
            "query": "SELECT count(*) FROM users",
            "params": {"limit": 100},
        }

    # -------------------------------------------------------------------------
    # Failure paths: residual endpoint fields and warning log
    # -------------------------------------------------------------------------

    async def test_non_success_records_dataset_native_endpoint(
        self, mock_client, tmp_path
    ):
        """A non-success response records the exact endpoint in the residual."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 403
        mock_client.execute_http_post_request = AsyncMock(return_value=mock_response)

        result = await fetch_question_queries_single(
            mock_client, 41, {"type": "query"}, str(tmp_path)
        )

        assert result is None
        failures = _read_residual_failures(str(tmp_path))
        assert failures[0]["category"] == "question_query_fetch_failed"
        assert failures[0]["endpoint"] == "/api/dataset/native"
        assert failures[0]["record_id"] == 41
        assert failures[0]["http_status"] == 403

    async def test_error_logs_warning_and_records_endpoint(self, mock_client, tmp_path):
        """An exception logs the exact skip warning and records the endpoint."""
        mock_client.execute_http_post_request = AsyncMock(side_effect=Exception("boom"))

        with patch("app.extracts.questions.logger") as mock_logger:
            result = await fetch_question_queries_single(
                mock_client, 40, {"type": "query"}, str(tmp_path)
            )

        assert result is None
        mock_logger.warning.assert_called_once_with(
            "fetch_question_query: skipping question_id=%s after error",
            40,
            exc_info=True,
        )
        failures = _read_residual_failures(str(tmp_path))
        assert failures[0]["category"] == "question_query_fetch_errored"
        assert failures[0]["endpoint"] == "/api/dataset/native"
        assert failures[0]["record_id"] == 40
