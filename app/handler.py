"""Metabase REST connector handler.

Wires the SDK's FastAPI endpoints to Metabase API calls, translating the
``sageTemplate``, ``restMetadataTemplate``, and
``restMetadataOutputTransformerTemplate`` sections of the
``atlan-connectors-metabase`` configmap into Python.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from application_sdk.handlers.base import BaseHandler
from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient
from app.constants import MetabaseUrls
from app.models import PreflightCheckResult, PreflightCheckResults

logger = get_logger(__name__)


class MetabaseHandler(BaseHandler):
    """FastAPI handler for Metabase metadata extraction UI interactions.

    Processes requests from the Atlan frontend through the SDK layer,
    interacts with the Metabase API, and returns formatted responses.

    Methods map to SDK endpoints as follows:

    - ``test_auth``        → ``POST /workflows/v1/auth``
    - ``fetch_metadata``   → ``POST /workflows/v1/metadata``
    - ``preflight_check``  → ``POST /workflows/v1/check``
    - ``get_configmap``    → used by SDK to load credential / workflow templates
    """

    def __init__(self, client: MetabaseApiClient | None = None):
        """Initialize the Metabase handler with an optional client instance.

        Args:
            client: Optional ``MetabaseApiClient`` instance.  If ``None``, the
                client should be initialised later via the SDK ``load()`` hook.
        """
        super().__init__(client=client)
        self.client: MetabaseApiClient | None = client

    # ========================================================================
    # SECTION 1 — SDK INTERFACE METHODS
    # ========================================================================

    async def test_auth(self, **kwargs: Any) -> bool:
        """Test that Metabase credentials are valid.

        The SDK calls this after ``load()`` has initialised the client and
        already obtained a session token via ``POST /api/session``.  We
        therefore only need to assert that the token is present.

        Args:
            **kwargs: Unused; required by the SDK interface.

        Returns:
            ``True`` if a session token is held by the client.

        Raises:
            Exception: If the client is not initialised or has no token.
        """
        if not self.client:
            raise Exception("Metabase client not initialized")
        return await self.client.test_connection()

    @staticmethod
    async def get_configmap(config_map_id: str) -> Dict[str, Any]:
        """Load a JSON config map from the ``app/templates/`` directory.

        Args:
            config_map_id: Config identifier.  ``"atlan-connectors-metabase"``
                returns the credential/UI template; anything else returns the
                workflow template.

        Returns:
            Parsed JSON dict from the matching template file.
        """
        templates_dir = Path().cwd() / "app" / "templates"
        if config_map_id == "atlan-connectors-metabase":
            with open(templates_dir / "atlan-connectors-metabase.json") as f:
                return json.load(f)
        with open(templates_dir / "workflow.json") as f:
            return json.load(f)

    async def fetch_metadata(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Fetch metadata for UI filter dropdowns.

        Translates the ``restMetadataTemplate`` / ``restMetadataOutputTransformerTemplate``
        configmap sections.

        Currently only the ``"default"`` metadata type is defined in the
        configmap.  It:

        1. Re-uses the session token already held by the client (curl id=1,
           the ``token`` step, is handled by the auth client brick).
        2. Fetches all collections via ``GET /api/collection`` (curl id=2).
        3. Filters out personal collections (those with a non-``null``
           ``personal_owner_id``).
        4. Returns the surviving collections as ``{"value", "title", "children"}``
           dicts, matching the ``restMetadataOutputTransformerTemplate``.

        Args:
            **kwargs: SDK-injected keyword arguments.  ``metadata_type``
                (str, default ``"default"``) selects the template branch.

        Returns:
            List of dicts with keys ``value`` (collection id), ``title``
            (collection name), and ``children`` (always ``[]`` for collections).

        Raises:
            Exception: If the client is not initialised or the API call fails.
        """
        if not self.client:
            raise Exception("Metabase client not initialized")

        metadata_type: str = kwargs.get("metadata_type", "default")

        if metadata_type == "default":
            return await self._fetch_collections_metadata()

        logger.warning(
            f"Unknown metadata_type '{metadata_type}', falling back to 'default'"
        )
        return await self._fetch_collections_metadata()

    async def preflight_check(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Run all sageTemplate preflight checks before workflow execution.

        Executes the four checks defined in the ``sageTemplate`` section of the
        Metabase configmap:

        1. ``collectionCountCheck`` — counts non-personal collections that
           survive the include/exclude filter.
        2. ``dashboardCountCheck`` — counts dashboards in non-personal,
           filter-matching collections.
        3. ``questionCountCheck`` — counts questions (cards) in non-personal,
           filter-matching collections.
        4. ``nativeQueryPermissionCheck`` — verifies every database has
           ``native_permissions == "write"``.

        Args:
            *args: The SDK may pass a payload dict as ``args[0]``.
            **kwargs: Additional keyword arguments from the SDK.

        Returns:
            ``PreflightCheckResults`` serialised to a dict (``None`` fields
            excluded).
        """
        try:
            results = PreflightCheckResults()

            if not self.client:
                return PreflightCheckResults(
                    collectionCountCheck=PreflightCheckResult(
                        success=False,
                        failureMessage="Metabase client not initialized",
                    )
                ).model_dump(exclude_none=True)

            # Resolve the form-data payload (SDK passes it as args[0] or kwargs)
            payload: Dict[str, Any] = {}
            if args and isinstance(args[0], dict):
                payload = args[0]
            payload.update(kwargs)

            # Extract include/exclude collection filters (JSON strings → dicts).
            # The sageTemplate uses .formData `include-collections` / `exclude-collections`.
            include_filter: Dict[str, Any] = {}
            exclude_filter: Dict[str, Any] = {}
            try:
                raw_include = payload.get("include-collections", "{}")
                include_filter = (
                    json.loads(raw_include)
                    if isinstance(raw_include, str)
                    else raw_include
                ) or {}
            except (json.JSONDecodeError, TypeError):
                include_filter = {}
            try:
                raw_exclude = payload.get("exclude-collections", "{}")
                exclude_filter = (
                    json.loads(raw_exclude)
                    if isinstance(raw_exclude, str)
                    else raw_exclude
                ) or {}
            except (json.JSONDecodeError, TypeError):
                exclude_filter = {}

            # Run checks sequentially so that later checks can reuse API data
            # fetched by earlier checks.  All checks share the session token
            # already held by self.client.

            results.collectionCountCheck = (
                await MetabaseHandler._validate_collection_count(
                    self.client, include_filter, exclude_filter
                )
            )

            # Short-circuit: if the first check failed (auth / connectivity
            # issue) there is no point running the remaining checks.
            if not results.collectionCountCheck.success:
                return results.model_dump(exclude_none=True)

            results.dashboardCountCheck = (
                await MetabaseHandler._validate_dashboard_count(
                    self.client, include_filter, exclude_filter
                )
            )

            results.questionCountCheck = await MetabaseHandler._validate_question_count(
                self.client, include_filter, exclude_filter
            )

            results.nativeQueryPermissionCheck = (
                await MetabaseHandler._validate_native_query_permission(self.client)
            )

            return results.model_dump(exclude_none=True)

        except Exception as e:
            logger.error(f"Preflight check failed: {str(e)}")
            return PreflightCheckResults(
                collectionCountCheck=PreflightCheckResult(
                    success=False,
                    failureMessage=f"Preflight check failed: {str(e)}",
                )
            ).model_dump(exclude_none=True)

    # ========================================================================
    # SECTION 2 — METADATA FETCH HELPERS
    # ========================================================================

    async def _fetch_collections_metadata(self) -> List[Dict[str, Any]]:
        """Fetch and format collections for the UI metadata dropdown.

        Implements the ``restMetadataTemplate`` ``"default"`` branch (curl id=2)
        and the ``restMetadataOutputTransformerTemplate`` output shape:

            {"value": <id>, "title": <name>, "children": []}

        Personal collections (``personal_owner_id`` is not ``null``) are
        excluded, mirroring the Jinja2 template filter.

        Returns:
            List of collection dicts with ``value``, ``title``, and
            ``children`` keys.

        Raises:
            Exception: If the client is not initialised or the API call fails.
        """
        if not self.client:
            raise Exception("Metabase client not initialized")

        url = MetabaseUrls.collection(self.client.host, self.client.port)
        response = await self.client.execute_http_get_request(url=url, timeout=30)

        if response is None or not response.is_success:
            status = response.status_code if response else "No response"
            raise Exception(f"Failed to fetch Metabase collections — HTTP {status}")

        raw_collections: List[Dict[str, Any]] = response.json()

        # Filter out personal collections and transform to output shape.
        # Matches restMetadataOutputTransformerTemplate:
        #   {% if not collection.personal_owner_id %} → include
        result: List[Dict[str, Any]] = [
            {
                "value": collection["id"],
                "title": collection["name"],
                "children": [],
            }
            for collection in raw_collections
            if not collection.get("personal_owner_id")
        ]

        logger.info(
            f"fetch_metadata: returning {len(result)} non-personal collections "
            f"(filtered from {len(raw_collections)} total)"
        )
        return result

    # ========================================================================
    # SECTION 3 — PREFLIGHT CHECK STATIC VALIDATORS
    # ========================================================================

    @staticmethod
    async def _fetch_collections(
        client: MetabaseApiClient,
    ) -> List[Dict[str, Any]]:
        """Helper: GET /api/collection and return the parsed list.

        Used by multiple sageTemplate checks that all start with a
        ``login`` + ``collections`` curl pair.

        Args:
            client: Authenticated ``MetabaseApiClient`` instance.

        Returns:
            Raw list of collection dicts from the Metabase API.

        Raises:
            Exception: If the API call fails.
        """
        url = MetabaseUrls.collection(client.host, client.port)
        response = await client.execute_http_get_request(url=url, timeout=30)
        if response is None or not response.is_success:
            status = response.status_code if response else "No response"
            raise Exception(f"Failed to fetch collections — HTTP {status}")
        return response.json()

    @staticmethod
    async def _validate_collection_count(
        client: MetabaseApiClient,
        include_filter: Dict[str, Any],
        exclude_filter: Dict[str, Any],
    ) -> PreflightCheckResult:
        """Validate collections and count those matching the include/exclude filters.

        Implements the ``collectionCountCheck`` from ``sageTemplate``:

        - Fetches all collections (``GET /api/collection``).
        - Skips personal collections (``personal_owner_id`` is not ``null``).
        - Applies the include/exclude filter keyed by collection id.
        - Reports ``"Total collections: N"`` as the success message.

        Args:
            client: Authenticated ``MetabaseApiClient`` instance.
            include_filter: Dict of collection ids to include (empty = include all).
            exclude_filter: Dict of collection ids to exclude.

        Returns:
            ``PreflightCheckResult`` with the matching collection count.
        """
        try:
            collections = await MetabaseHandler._fetch_collections(client)

            collection_count = 0
            for collection in collections:
                # Skip personal collections
                if collection.get("personal_owner_id") is not None:
                    continue
                col_id_str = str(collection.get("id", ""))
                is_included = col_id_str in include_filter
                is_excluded = col_id_str in exclude_filter
                if not is_excluded and (len(include_filter) == 0 or is_included):
                    collection_count += 1

            return PreflightCheckResult(
                success=True,
                successMessage=f"Total collections: {collection_count}",
            )

        except Exception as e:
            return PreflightCheckResult(
                success=False,
                failureMessage=f"Collection count check failed: {str(e)}",
            )

    @staticmethod
    async def _validate_dashboard_count(
        client: MetabaseApiClient,
        include_filter: Dict[str, Any],
        exclude_filter: Dict[str, Any],
    ) -> PreflightCheckResult:
        """Validate dashboards and count those in allowed collections.

        Implements the ``dashboardCountCheck`` from ``sageTemplate``:

        - Fetches all collections and adds personal collection ids to the
          exclude list.
        - Fetches all dashboards (``GET /api/dashboard``).
        - Counts dashboards whose ``collection_id`` is not excluded and is
          in the include filter (or include filter is empty).
        - Reports ``"Total dashboards: N"`` as the success message.

        Args:
            client: Authenticated ``MetabaseApiClient`` instance.
            include_filter: Dict of collection ids to include (empty = include all).
            exclude_filter: Dict of collection ids to exclude.

        Returns:
            ``PreflightCheckResult`` with the matching dashboard count.
        """
        try:
            # Step 1: Fetch collections and extend exclude_filter with personal ones
            collections = await MetabaseHandler._fetch_collections(client)
            effective_exclude: Dict[str, Any] = dict(exclude_filter)
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    effective_exclude[str(collection.get("id", ""))] = {}

            # Step 2: Fetch dashboards
            url = MetabaseUrls.dashboard(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=30)
            if response is None or not response.is_success:
                status = response.status_code if response else "No response"
                raise Exception(f"Failed to fetch dashboards — HTTP {status}")
            dashboards: List[Dict[str, Any]] = response.json()

            # Step 3: Count matching dashboards
            dashboard_count = 0
            for dashboard in dashboards:
                col_id_str = str(dashboard.get("collection_id", ""))
                is_included = col_id_str in include_filter
                is_excluded = col_id_str in effective_exclude
                if not is_excluded and (len(include_filter) == 0 or is_included):
                    dashboard_count += 1

            return PreflightCheckResult(
                success=True,
                successMessage=f"Total dashboards: {dashboard_count}",
            )

        except Exception as e:
            return PreflightCheckResult(
                success=False,
                failureMessage=f"Dashboard count check failed: {str(e)}",
            )

    @staticmethod
    async def _validate_question_count(
        client: MetabaseApiClient,
        include_filter: Dict[str, Any],
        exclude_filter: Dict[str, Any],
    ) -> PreflightCheckResult:
        """Validate questions (cards) and count those in allowed collections.

        Implements the ``questionCountCheck`` from ``sageTemplate``:

        - Fetches all collections and adds personal collection ids to the
          exclude list.
        - Fetches all questions via ``GET /api/card``.
        - Counts questions whose ``collection_id`` is not excluded and is
          in the include filter (or include filter is empty).
        - Reports ``"Total questions: N"`` as the success message.

        Args:
            client: Authenticated ``MetabaseApiClient`` instance.
            include_filter: Dict of collection ids to include (empty = include all).
            exclude_filter: Dict of collection ids to exclude.

        Returns:
            ``PreflightCheckResult`` with the matching question count.
        """
        try:
            # Step 1: Fetch collections and extend exclude_filter with personal ones
            collections = await MetabaseHandler._fetch_collections(client)
            effective_exclude: Dict[str, Any] = dict(exclude_filter)
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    effective_exclude[str(collection.get("id", ""))] = {}

            # Step 2: Fetch questions (cards)
            url = MetabaseUrls.card(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=30)
            if response is None or not response.is_success:
                status = response.status_code if response else "No response"
                raise Exception(f"Failed to fetch questions — HTTP {status}")
            questions: List[Dict[str, Any]] = response.json()

            # Step 3: Count matching questions
            question_count = 0
            for question in questions:
                col_id_str = str(question.get("collection_id", ""))
                is_included = col_id_str in include_filter
                is_excluded = col_id_str in effective_exclude
                if not is_excluded and (len(include_filter) == 0 or is_included):
                    question_count += 1

            return PreflightCheckResult(
                success=True,
                successMessage=f"Total questions: {question_count}",
            )

        except Exception as e:
            return PreflightCheckResult(
                success=False,
                failureMessage=f"Question count check failed: {str(e)}",
            )

    @staticmethod
    async def _validate_native_query_permission(
        client: MetabaseApiClient,
    ) -> PreflightCheckResult:
        """Verify that every connected database allows native query editing.

        Implements the ``nativeQueryPermissionCheck`` from ``sageTemplate``:

        - Fetches the database list via ``GET /api/database``.
        - For every database, checks that ``native_permissions == "write"``.
        - On success: returns ``"Check successful"``.
        - On failure: returns a message listing the databases that lack
          ``write`` permission.

        Args:
            client: Authenticated ``MetabaseApiClient`` instance.

        Returns:
            ``PreflightCheckResult`` indicating success or which databases
            are missing native query editing permission.
        """
        try:
            url = MetabaseUrls.database(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=30)
            if response is None or not response.is_success:
                status = response.status_code if response else "No response"
                raise Exception(f"Failed to fetch database list — HTTP {status}")

            response_body: Dict[str, Any] = response.json()
            # Metabase wraps the list under a "data" key
            databases: List[Dict[str, Any]] = response_body.get("data", response_body)
            if not isinstance(databases, list):
                databases = []

            no_native_query_perm_on: List[str] = []
            for db in databases:
                if db.get("native_permissions") != "write":
                    no_native_query_perm_on.append(
                        db.get("name", str(db.get("id", "")))
                    )

            if len(no_native_query_perm_on) == 0:
                return PreflightCheckResult(
                    success=True,
                    successMessage="Check successful",
                )
            else:
                missing = ", ".join(no_native_query_perm_on)
                return PreflightCheckResult(
                    success=False,
                    failureMessage=(
                        "Check failed. Missing native query editing permission on "
                        f"the following databases: [{missing}]"
                    ),
                )

        except Exception as e:
            return PreflightCheckResult(
                success=False,
                failureMessage=f"Native query permission check failed: {str(e)}",
            )
