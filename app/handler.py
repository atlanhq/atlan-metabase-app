"""Metabase REST connector handler (v3 typed).

Wires the SDK's FastAPI endpoints (``test_auth``, ``preflight_check``,
``fetch_metadata``) to Metabase API calls, translating the ``sageTemplate`` /
``restMetadataTemplate`` configmap sections into typed Python responses.

The SDK auto-serves ``/workflows/v1/configmap/<id>`` from ``app/generated/``
(see ``application_sdk/handler/service.py``); we do NOT define a
``get_configmap`` method here.
"""

from __future__ import annotations

from typing import Any

import orjson
from application_sdk.handler import Handler
from application_sdk.handler.contracts import (
    ApiMetadataObject,
    ApiMetadataOutput,
    AuthInput,
    AuthOutput,
    AuthStatus,
    HandlerCredential,
    MetadataInput,
    PreflightCheck,
    PreflightInput,
    PreflightOutput,
    PreflightStatus,
)
from application_sdk.observability.logger_adaptor import get_logger

from app.client import MetabaseApiClient, build_client
from app.constants import MetabaseUrls
from app.credentials import parse_metabase_credentials
from app.errors import MetabaseClientNotInitializedError, MetabaseSourceUnavailableError

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class MetabaseHandler(Handler):
    """FastAPI handler for Metabase metadata extraction UI interactions.

    Maps to SDK endpoints:
    - ``test_auth``        → ``POST /workflows/v1/auth``
    - ``preflight_check``  → ``POST /workflows/v1/check``
    - ``fetch_metadata``   → ``POST /workflows/v1/metadata``
    """

    def __init__(self, client: MetabaseApiClient | None = None) -> None:
        """Initialise with an optional pre-built client (used by unit tests)."""
        self.client: MetabaseApiClient | None = client

    # ------------------------------------------------------------------
    # SDK INTERFACE METHODS
    # ------------------------------------------------------------------

    async def test_auth(self, input: AuthInput) -> AuthOutput:
        """Authenticate against Metabase using the supplied credentials."""
        try:
            if self.client is not None:
                await self.client.test_connection()
                return AuthOutput(
                    status=AuthStatus.SUCCESS,
                    message="Authentication successful",
                )

            if not input.credentials:
                raise MetabaseClientNotInitializedError(
                    message="Metabase client not initialized",
                    field="credentials",
                )

            credential = parse_metabase_credentials(input.credentials)
            client = await build_client(credential)
            try:
                await client.test_connection()
                return AuthOutput(
                    status=AuthStatus.SUCCESS,
                    message="Authentication successful",
                )
            finally:
                await client.close()
        except Exception:
            logger.warning("Metabase auth failed", exc_info=True)
            return AuthOutput(
                status=AuthStatus.FAILED,
                message="Authentication failed — see application logs for detail",
            )

    async def fetch_metadata(self, input: MetadataInput) -> ApiMetadataOutput:
        """Return non-personal collections as apitree nodes for the UI dropdown."""
        client = await self._client_for(input.credentials)
        owns_client = self.client is None
        try:
            raw_collections = await self._fetch_collections(client)
            objects = [
                ApiMetadataObject(
                    value=str(collection["id"]),
                    title=str(collection.get("name", "")),
                    node_type="collection",
                )
                for collection in raw_collections
                if not collection.get("personal_owner_id")
            ]
            logger.info(
                "fetch_metadata: returning %d non-personal collections "
                "(filtered from %d total)",
                len(objects),
                len(raw_collections),
            )
            return ApiMetadataOutput(objects=objects)
        finally:
            if owns_client:
                await client.close()

    async def preflight_check(self, input: PreflightInput) -> PreflightOutput:
        """Run the four ``sageTemplate`` preflight checks."""
        try:
            client = await self._client_for(input.credentials)
        except Exception:
            logger.error("Preflight client build failed", exc_info=True)
            return PreflightOutput(
                status=PreflightStatus.NOT_READY,
                checks=[
                    PreflightCheck(
                        name="collectionCountCheck",
                        passed=False,
                        message="Preflight check failed — see application logs for detail",
                    ),
                ],
                message="Preflight check failed — see application logs for detail",
            )

        owns_client = self.client is None
        try:
            # Filters may arrive under ``metadata`` (curl / docs path) or
            # ``connection_config`` (v3 preflight runner path — see
            # ``application_sdk.testing.integration.client._call_preflight``).
            # Try both so the same handler serves the UI form, the integration
            # runner, and direct API consumers.
            include_filter, exclude_filter = self._read_filters(input.metadata)
            if not include_filter and not exclude_filter:
                include_filter, exclude_filter = self._read_filters(
                    getattr(input, "connection_config", None)
                )

            checks: list[PreflightCheck] = []

            collection_check = await self._validate_collection_count(
                client, include_filter, exclude_filter
            )
            checks.append(collection_check)

            if not collection_check.passed:
                # Short-circuit when the first check fails — it usually means
                # the API call itself failed and the rest will fail identically.
                return PreflightOutput(
                    status=PreflightStatus.NOT_READY,
                    checks=checks,
                )

            checks.append(
                await self._validate_dashboard_count(
                    client, include_filter, exclude_filter
                )
            )
            checks.append(
                await self._validate_question_count(
                    client, include_filter, exclude_filter
                )
            )
            checks.append(await self._validate_native_query_permission(client))

            all_passed = all(c.passed for c in checks)
            return PreflightOutput(
                status=PreflightStatus.READY
                if all_passed
                else PreflightStatus.NOT_READY,
                checks=checks,
            )
        except Exception:
            logger.error("Preflight check failed", exc_info=True)
            return PreflightOutput(
                status=PreflightStatus.NOT_READY,
                checks=[
                    PreflightCheck(
                        name="collectionCountCheck",
                        passed=False,
                        message="Preflight check failed — see application logs for detail",
                    ),
                ],
                message="Preflight check failed — see application logs for detail",
            )
        finally:
            if owns_client:
                await client.close()

    # ------------------------------------------------------------------
    # SHARED HELPERS
    # ------------------------------------------------------------------

    async def _client_for(
        self, credentials: list[HandlerCredential] | dict[str, Any]
    ) -> MetabaseApiClient:
        """Return an authenticated client — pre-built fixture or freshly built."""
        if self.client is not None:
            return self.client
        if not credentials:
            raise MetabaseClientNotInitializedError(
                message="Metabase client not initialized",
                field="credentials",
            )
        credential = parse_metabase_credentials(credentials)
        return await build_client(credential)

    @staticmethod
    def _read_filters(metadata: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Parse include / exclude collection filters from the preflight metadata."""

        def _coerce(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    parsed = orjson.loads(value)
                except orjson.JSONDecodeError:
                    logger.warning(
                        "Collection filter %r is not valid JSON; treating as empty",
                        value,
                        exc_info=True,
                    )
                    return {}
                return parsed if isinstance(parsed, dict) else {}
            return {}

        if metadata is None:
            return {}, {}

        # ``BaseMetadataConfig`` is a pydantic model with ``extra="allow"``; use
        # ``model_dump`` when available, fall back to ``dict``.
        raw: dict[str, Any]
        if hasattr(metadata, "model_dump"):
            raw = metadata.model_dump()
        elif isinstance(metadata, dict):
            raw = metadata
        else:
            raw = {}

        include = _coerce(
            raw.get("include-collections", raw.get("include_collections", {}))
        )
        exclude = _coerce(
            raw.get("exclude-collections", raw.get("exclude_collections", {}))
        )
        return include, exclude

    # ------------------------------------------------------------------
    # PREFLIGHT VALIDATORS
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch_collections(
        client: MetabaseApiClient,
    ) -> list[dict[str, Any]]:
        url = MetabaseUrls.collection(client.host, client.port)
        response = await client.execute_http_get_request(url=url, timeout=30)
        if response is None or not response.is_success:
            status = response.status_code if response else "No response"
            raise MetabaseSourceUnavailableError(
                message=f"Failed to fetch collections — HTTP {status}",
                source_type="metabase",
                endpoint="/api/collection",
                http_status=status if isinstance(status, int) else None,
            )
        return response.json()

    @staticmethod
    async def _validate_collection_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
    ) -> PreflightCheck:
        try:
            collections = await MetabaseHandler._fetch_collections(client)
            count = 0
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    continue
                col_id_str = str(collection.get("id", ""))
                if col_id_str in exclude_filter:
                    continue
                if include_filter and col_id_str not in include_filter:
                    continue
                count += 1
            return PreflightCheck(
                name="collectionCountCheck",
                passed=True,
                message=f"Total collections: {count}",
            )
        except Exception:
            logger.warning("collectionCountCheck failed", exc_info=True)
            return PreflightCheck(
                name="collectionCountCheck",
                passed=False,
                message="Collection count check failed — see application logs for detail",
            )

    @staticmethod
    async def _validate_dashboard_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
    ) -> PreflightCheck:
        try:
            collections = await MetabaseHandler._fetch_collections(client)
            effective_exclude: dict[str, Any] = dict(exclude_filter)
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    effective_exclude[str(collection.get("id", ""))] = {}

            url = MetabaseUrls.dashboard(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=30)
            if response is None or not response.is_success:
                status = response.status_code if response else "No response"
                raise MetabaseSourceUnavailableError(
                    message=f"Failed to fetch dashboards — HTTP {status}",
                    source_type="metabase",
                    endpoint="/api/dashboard",
                    http_status=status if isinstance(status, int) else None,
                )
            dashboards: list[dict[str, Any]] = response.json()

            count = 0
            for dashboard in dashboards:
                col_id_str = str(dashboard.get("collection_id", ""))
                if col_id_str in effective_exclude:
                    continue
                if include_filter and col_id_str not in include_filter:
                    continue
                count += 1

            return PreflightCheck(
                name="dashboardCountCheck",
                passed=True,
                message=f"Total dashboards: {count}",
            )
        except Exception:
            logger.warning("dashboardCountCheck failed", exc_info=True)
            return PreflightCheck(
                name="dashboardCountCheck",
                passed=False,
                message="Dashboard count check failed — see application logs for detail",
            )

    @staticmethod
    async def _validate_question_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
    ) -> PreflightCheck:
        try:
            collections = await MetabaseHandler._fetch_collections(client)
            effective_exclude: dict[str, Any] = dict(exclude_filter)
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    effective_exclude[str(collection.get("id", ""))] = {}

            url = MetabaseUrls.card(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=30)
            if response is None or not response.is_success:
                status = response.status_code if response else "No response"
                raise MetabaseSourceUnavailableError(
                    message=f"Failed to fetch questions — HTTP {status}",
                    source_type="metabase",
                    endpoint="/api/card",
                    http_status=status if isinstance(status, int) else None,
                )
            questions: list[dict[str, Any]] = response.json()

            count = 0
            for question in questions:
                col_id_str = str(question.get("collection_id", ""))
                if col_id_str in effective_exclude:
                    continue
                if include_filter and col_id_str not in include_filter:
                    continue
                count += 1

            return PreflightCheck(
                name="questionCountCheck",
                passed=True,
                message=f"Total questions: {count}",
            )
        except Exception:
            logger.warning("questionCountCheck failed", exc_info=True)
            return PreflightCheck(
                name="questionCountCheck",
                passed=False,
                message="Question count check failed — see application logs for detail",
            )

    @staticmethod
    async def _validate_native_query_permission(
        client: MetabaseApiClient,
    ) -> PreflightCheck:
        try:
            url = MetabaseUrls.database(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=30)
            if response is None or not response.is_success:
                status = response.status_code if response else "No response"
                raise MetabaseSourceUnavailableError(
                    message=f"Failed to fetch database list — HTTP {status}",
                    source_type="metabase",
                    endpoint="/api/database",
                    http_status=status if isinstance(status, int) else None,
                )

            response_body: dict[str, Any] = response.json()
            databases: list[dict[str, Any]] = response_body.get("data", response_body)
            if not isinstance(databases, list):
                databases = []

            missing = [
                db.get("name", str(db.get("id", "")))
                for db in databases
                if db.get("native_permissions") != "write"
            ]

            if not missing:
                return PreflightCheck(
                    name="nativeQueryPermissionCheck",
                    passed=True,
                    message="Check successful",
                )
            return PreflightCheck(
                name="nativeQueryPermissionCheck",
                passed=False,
                message=(
                    "Check failed. Missing native query editing permission on "
                    f"the following databases: [{', '.join(missing)}]"
                ),
            )
        except Exception:
            logger.warning("nativeQueryPermissionCheck failed", exc_info=True)
            return PreflightCheck(
                name="nativeQueryPermissionCheck",
                passed=False,
                message="Native query permission check failed — see application logs for detail",
            )
