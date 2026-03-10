"""Unit tests for app.constants.MetabaseUrls URL builders."""

import pytest

from app.constants import MetabaseUrls


class TestMetabaseUrls:
    """Tests for every URL builder method in MetabaseUrls."""

    HOST = "https://myinstance.metabaseapp.com"
    PORT = 443

    # -------------------------------------------------------------------------
    # session
    # -------------------------------------------------------------------------

    def test_session_returns_correct_url(self):
        url = MetabaseUrls.session(self.HOST, self.PORT)
        assert url == "https://myinstance.metabaseapp.com:443/api/session"

    def test_session_with_non_default_port(self):
        url = MetabaseUrls.session("http://localhost", 3000)
        assert url == "http://localhost:3000/api/session"

    def test_session_contains_api_session_path(self):
        url = MetabaseUrls.session(self.HOST, self.PORT)
        assert "/api/session" in url

    # -------------------------------------------------------------------------
    # collection
    # -------------------------------------------------------------------------

    def test_collection_returns_correct_url(self):
        url = MetabaseUrls.collection(self.HOST, self.PORT)
        assert url == "https://myinstance.metabaseapp.com:443/api/collection"

    def test_collection_with_custom_port(self):
        url = MetabaseUrls.collection("https://mb.example.com", 8080)
        assert url == "https://mb.example.com:8080/api/collection"

    def test_collection_contains_api_collection_path(self):
        url = MetabaseUrls.collection(self.HOST, self.PORT)
        assert "/api/collection" in url

    # -------------------------------------------------------------------------
    # dashboard
    # -------------------------------------------------------------------------

    def test_dashboard_returns_correct_url(self):
        url = MetabaseUrls.dashboard(self.HOST, self.PORT)
        assert url == "https://myinstance.metabaseapp.com:443/api/dashboard"

    def test_dashboard_with_custom_port(self):
        url = MetabaseUrls.dashboard("https://mb.example.com", 8080)
        assert url == "https://mb.example.com:8080/api/dashboard"

    def test_dashboard_contains_api_dashboard_path(self):
        url = MetabaseUrls.dashboard(self.HOST, self.PORT)
        assert "/api/dashboard" in url

    # -------------------------------------------------------------------------
    # card
    # -------------------------------------------------------------------------

    def test_card_returns_correct_url(self):
        url = MetabaseUrls.card(self.HOST, self.PORT)
        assert url == "https://myinstance.metabaseapp.com:443/api/card"

    def test_card_with_custom_port(self):
        url = MetabaseUrls.card("https://mb.example.com", 8080)
        assert url == "https://mb.example.com:8080/api/card"

    def test_card_contains_api_card_path(self):
        url = MetabaseUrls.card(self.HOST, self.PORT)
        assert "/api/card" in url

    # -------------------------------------------------------------------------
    # database
    # -------------------------------------------------------------------------

    def test_database_returns_correct_url(self):
        url = MetabaseUrls.database(self.HOST, self.PORT)
        assert url == "https://myinstance.metabaseapp.com:443/api/database"

    def test_database_with_custom_port(self):
        url = MetabaseUrls.database("https://mb.example.com", 8080)
        assert url == "https://mb.example.com:8080/api/database"

    def test_database_contains_api_database_path(self):
        url = MetabaseUrls.database(self.HOST, self.PORT)
        assert "/api/database" in url

    # -------------------------------------------------------------------------
    # dashboard_detail
    # -------------------------------------------------------------------------

    def test_dashboard_detail_returns_correct_url(self):
        url = MetabaseUrls.dashboard_detail(self.HOST, self.PORT, 42)
        assert url == "https://myinstance.metabaseapp.com:443/api/dashboard/42"

    def test_dashboard_detail_embeds_id(self):
        url = MetabaseUrls.dashboard_detail(self.HOST, self.PORT, 999)
        assert "/api/dashboard/999" in url

    def test_dashboard_detail_with_id_one(self):
        url = MetabaseUrls.dashboard_detail("https://mb.example.com", 8080, 1)
        assert url == "https://mb.example.com:8080/api/dashboard/1"

    # -------------------------------------------------------------------------
    # database_metadata
    # -------------------------------------------------------------------------

    def test_database_metadata_returns_correct_url(self):
        url = MetabaseUrls.database_metadata(self.HOST, self.PORT, 7)
        assert url == "https://myinstance.metabaseapp.com:443/api/database/7/metadata"

    def test_database_metadata_embeds_id(self):
        url = MetabaseUrls.database_metadata(self.HOST, self.PORT, 100)
        assert "/api/database/100/metadata" in url

    def test_database_metadata_with_custom_port(self):
        url = MetabaseUrls.database_metadata("https://mb.example.com", 8080, 3)
        assert url == "https://mb.example.com:8080/api/database/3/metadata"

    # -------------------------------------------------------------------------
    # dataset_native
    # -------------------------------------------------------------------------

    def test_dataset_native_returns_correct_url(self):
        url = MetabaseUrls.dataset_native(self.HOST, self.PORT)
        assert url == "https://myinstance.metabaseapp.com:443/api/dataset/native"

    def test_dataset_native_with_custom_port(self):
        url = MetabaseUrls.dataset_native("https://mb.example.com", 8080)
        assert url == "https://mb.example.com:8080/api/dataset/native"

    def test_dataset_native_contains_api_dataset_native_path(self):
        url = MetabaseUrls.dataset_native(self.HOST, self.PORT)
        assert "/api/dataset/native" in url

    # -------------------------------------------------------------------------
    # Consistency: all builders include the host
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "method,extra_args",
        [
            (MetabaseUrls.session, []),
            (MetabaseUrls.collection, []),
            (MetabaseUrls.dashboard, []),
            (MetabaseUrls.card, []),
            (MetabaseUrls.database, []),
            (MetabaseUrls.dataset_native, []),
        ],
    )
    def test_url_starts_with_host(self, method, extra_args):
        url = method(self.HOST, self.PORT, *extra_args)
        assert url.startswith(self.HOST)

    @pytest.mark.parametrize(
        "method,extra_args",
        [
            (MetabaseUrls.dashboard_detail, [5]),
            (MetabaseUrls.database_metadata, [5]),
        ],
    )
    def test_url_with_id_starts_with_host(self, method, extra_args):
        url = method(self.HOST, self.PORT, *extra_args)
        assert url.startswith(self.HOST)
