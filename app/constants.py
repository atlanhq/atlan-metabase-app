"""Metabase API endpoint URL builders."""


class MetabaseUrls:
    """Centralized URL builders for all Metabase API endpoints.

    The Metabase ``host`` credential is stored **with** the protocol prefix
    (e.g. ``https://myinstance.metabaseapp.com``), so URL builders combine
    host and port directly without adding a scheme.
    """

    @staticmethod
    def session(host: str, port: int) -> str:
        """Build the URL for the Metabase session (login) endpoint.

        Args:
            host: Full host URL including protocol
                  (e.g. ``https://myinstance.metabaseapp.com``).
            port: TCP port (default 443 for cloud-hosted instances).

        Returns:
            Full URL string for ``POST /api/session``.
        """
        return f"{host}:{port}/api/session"

    @staticmethod
    def collection(host: str, port: int) -> str:
        """Build the URL for the Metabase collection list endpoint.

        Used by ``restMetadataTemplate`` (id=2) and all ``sageTemplate`` checks
        that need to identify personal collections.

        Args:
            host: Full host URL including protocol.
            port: TCP port.

        Returns:
            Full URL string for ``GET /api/collection``.
        """
        return f"{host}:{port}/api/collection"

    @staticmethod
    def dashboard(host: str, port: int) -> str:
        """Build the URL for the Metabase dashboard list endpoint.

        Used by the ``dashboardCountCheck`` in ``sageTemplate``.

        Args:
            host: Full host URL including protocol.
            port: TCP port.

        Returns:
            Full URL string for ``GET /api/dashboard``.
        """
        return f"{host}:{port}/api/dashboard"

    @staticmethod
    def card(host: str, port: int) -> str:
        """Build the URL for the Metabase card (question) list endpoint.

        Used by the ``questionCountCheck`` in ``sageTemplate``.

        Args:
            host: Full host URL including protocol.
            port: TCP port.

        Returns:
            Full URL string for ``GET /api/card``.
        """
        return f"{host}:{port}/api/card"

    @staticmethod
    def database(host: str, port: int) -> str:
        """Build the URL for the Metabase database list endpoint.

        Used by the ``nativeQueryPermissionCheck`` in ``sageTemplate``.

        Args:
            host: Full host URL including protocol.
            port: TCP port.

        Returns:
            Full URL string for ``GET /api/database``.
        """
        return f"{host}:{port}/api/database"

    @staticmethod
    def dashboard_detail(host: str, port: int, dashboard_id: int) -> str:
        """Build the URL for a single Metabase dashboard detail endpoint.

        Returns full dashboard data including ``ordered_cards`` (the list of
        card/question slots on the dashboard), which is absent from the list
        endpoint response.

        Args:
            host: Full host URL including protocol.
            port: TCP port.
            dashboard_id: Integer ID of the dashboard to fetch.

        Returns:
            Full URL string for ``GET /api/dashboard/{dashboard_id}``.
        """
        return f"{host}:{port}/api/dashboard/{dashboard_id}"

    @staticmethod
    def database_metadata(host: str, port: int, database_id: int) -> str:
        """Build the URL for a Metabase database metadata endpoint.

        Returns schema and table information (including fields) for the given
        database.  Used by the ``extract-detailed`` stage to populate
        ``database-metadata/``.

        Args:
            host: Full host URL including protocol.
            port: TCP port.
            database_id: Integer ID of the database whose metadata to fetch.

        Returns:
            Full URL string for ``GET /api/database/{database_id}/metadata``.
        """
        return f"{host}:{port}/api/database/{database_id}/metadata"

    @staticmethod
    def dataset_native(host: str, port: int) -> str:
        """Build the URL for the Metabase dataset native query endpoint.

        Accepts a POST body containing ``dataset_query`` (plus an optional
        ``question_id`` hint) and returns the resolved native SQL string in
        ``{"query": "...", "params": ...}``.  Used by the ``extract-queries``
        stage to materialise the SQL for each question.

        Args:
            host: Full host URL including protocol.
            port: TCP port.

        Returns:
            Full URL string for ``POST /api/dataset/native``.
        """
        return f"{host}:{port}/api/dataset/native"
