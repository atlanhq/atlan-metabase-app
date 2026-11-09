"""Regression tests for residual writes from asynchronous extractors."""

import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.client import MetabaseApiClient
from app.extracts import collections, dashboards, databases, questions
from app.residuals import record_residual_failure


async def _run_in_thread_inline(func, *args, **kwargs):
    return func(*args, **kwargs)


class TestResidualFailureOffload(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.output_path = temp_dir.name
        self.client = MagicMock(spec=MetabaseApiClient)
        self.client.host = "https://myinstance.metabaseapp.com"
        self.client.port = 443

    @staticmethod
    def _failure_response(status_code):
        response = MagicMock()
        response.is_success = False
        response.status_code = status_code
        return response

    async def _assert_offloaded(
        self, module, operation, expected_result, category, **residual_metadata
    ):
        with patch.object(
            module,
            "run_in_thread",
            new=AsyncMock(side_effect=_run_in_thread_inline),
        ) as offloader:
            result = await operation

        self.assertEqual(result, expected_result)
        offloader.assert_awaited_once_with(
            record_residual_failure,
            self.output_path,
            category,
            **residual_metadata,
        )

    async def test_each_residual_write_uses_the_thread_offloader(self):
        self.client.execute_http_get_request = AsyncMock(
            return_value=self._failure_response(500)
        )
        await self._assert_offloaded(
            collections,
            collections.fetch_collections_summaries(self.client, self.output_path),
            [],
            "collections_fetch_failed",
            endpoint="/api/collection",
            http_status=500,
        )

        self.client.execute_http_get_request = AsyncMock(
            return_value=self._failure_response(500)
        )
        await self._assert_offloaded(
            questions,
            questions.fetch_questions_summaries(self.client, self.output_path),
            [],
            "questions_fetch_failed",
            endpoint="/api/card",
            http_status=500,
        )

        self.client.execute_http_post_request = AsyncMock(
            return_value=self._failure_response(400)
        )
        await self._assert_offloaded(
            questions,
            questions.fetch_question_queries_single(
                self.client, 41, {"type": "query"}, self.output_path
            ),
            None,
            "question_query_fetch_failed",
            endpoint="/api/dataset/native",
            record_id=41,
            http_status=400,
        )

        self.client.execute_http_post_request = AsyncMock(
            side_effect=RuntimeError("request failed")
        )
        await self._assert_offloaded(
            questions,
            questions.fetch_question_queries_single(
                self.client, 40, {"type": "query"}, self.output_path
            ),
            None,
            "question_query_fetch_errored",
            endpoint="/api/dataset/native",
            record_id=40,
        )

        self.client.execute_http_get_request = AsyncMock(
            return_value=self._failure_response(503)
        )
        await self._assert_offloaded(
            dashboards,
            dashboards.fetch_dashboards_summaries(self.client, self.output_path),
            [],
            "dashboards_fetch_failed",
            endpoint="/api/dashboard",
            http_status=503,
        )

        self.client.execute_http_get_request = AsyncMock(
            return_value=self._failure_response(404)
        )
        await self._assert_offloaded(
            dashboards,
            dashboards.fetch_dashboard_details(self.client, 99, self.output_path),
            None,
            "dashboard_detail_fetch_failed",
            endpoint="/api/dashboard",
            record_id=99,
            http_status=404,
        )

        self.client.execute_http_get_request = AsyncMock(
            return_value=self._failure_response(500)
        )
        await self._assert_offloaded(
            databases,
            databases.fetch_databases_summaries(self.client, self.output_path),
            [],
            "databases_fetch_failed",
            endpoint="/api/database",
            http_status=500,
        )

        self.client.execute_http_get_request = AsyncMock(
            return_value=self._failure_response(404)
        )
        await self._assert_offloaded(
            databases,
            databases.fetch_database_metadata(self.client, 99, self.output_path),
            None,
            "database_metadata_fetch_failed",
            endpoint="/api/database",
            record_id=99,
            http_status=404,
        )
