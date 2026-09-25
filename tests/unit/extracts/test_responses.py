"""Unit tests for app.extracts.responses."""

from unittest.mock import MagicMock

import pytest

from app.errors import MetabaseSourceUnavailableError
from app.extracts.responses import json_or_raise


class TestJsonOrRaise:
    """Tests for json_or_raise()."""

    def test_success_returns_decoded_body(self):
        """A successful response returns its decoded JSON body."""
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"data": [{"id": 1}]}

        assert json_or_raise(response, endpoint="/api/database") == {
            "data": [{"id": 1}]
        }

    def test_non_success_raises_with_status_and_endpoint(self):
        """A non-success response raises the typed error carrying its status."""
        response = MagicMock()
        response.is_success = False
        response.status_code = 503

        with pytest.raises(MetabaseSourceUnavailableError) as excinfo:
            json_or_raise(response, endpoint="/api/card")

        assert excinfo.value.http_status == 503
        assert excinfo.value.endpoint == "/api/card"
        assert excinfo.value.source_type == "metabase"
        response.json.assert_not_called()

    def test_no_response_raises_without_status(self):
        """A missing response raises with ``http_status`` left as ``None``."""
        with pytest.raises(MetabaseSourceUnavailableError) as excinfo:
            json_or_raise(None, endpoint="/api/collection")

        assert excinfo.value.http_status is None
        assert excinfo.value.endpoint == "/api/collection"

    def test_message_is_stable_across_statuses(self):
        """The user-facing message does not vary with the failing status."""
        messages = set()
        for status in (401, 404, 500):
            response = MagicMock()
            response.is_success = False
            response.status_code = status
            with pytest.raises(MetabaseSourceUnavailableError) as excinfo:
                json_or_raise(response, endpoint="/api/dashboard")
            messages.add(excinfo.value.message)

        assert len(messages) == 1
