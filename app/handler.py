"""Metabase REST connector handler (v3 typed).

Wires the SDK's FastAPI endpoints (``test_auth``, ``preflight_check``,
``fetch_metadata``) to Metabase API calls, translating the ``sageTemplate`` /
``restMetadataTemplate`` configmap sections into typed Python responses.

The SDK auto-serves ``/workflows/v1/configmap/<id>`` from ``app/generated/``
(see ``application_sdk/handler/service.py``); we do NOT define a
``get_configmap`` method here.
"""

from __future__ import annotations

import time
from typing import Any

import orjson
from application_sdk.errors import AuthError, InvalidInputError
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
from app.errors import (
    MetabaseClientNotInitializedError,
    MetabaseCollectionAccessError,
    MetabaseNativeQueryPermissionError,
    MetabaseSourceUnavailableError,
)

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
        """Gate readiness in blocking → advisory tiers.

        Ordering is reachability → authentication → authorization → advisory.
        Blocking intent is recorded per-check (``status=NOT_READY``) and the
        short-circuit control flow is kept, but during the CNCT-81 observation
        window the overall verdict is softened to ``PARTIAL`` so the gate lets the
        run proceed while the check matrix is collected; the advisory tier stamps
        ``PARTIAL`` and continues. The aggregate is never ``NOT_READY``. Unhandled
        errors are deliberately *not* caught here — a plumbing bug should fail the
        gate open (SDK logs and proceeds), never silently block every run.
        """
        checks: list[PreflightCheck] = []

        auth_check, client = await self._authentication_check(input.credentials)
        checks.append(auth_check)
        if client is None:
            # Observation window (CNCT-81): revert to NOT_READY at hard-fail flip;
            # per-check statuses stay as they are.
            return PreflightOutput(status=PreflightStatus.PARTIAL, checks=checks)

        owns_client = self.client is None
        try:
            include_filter, exclude_filter = self._resolve_filters(input)

            collection_check = await self._validate_collection_count(
                client, include_filter, exclude_filter
            )
            checks.append(collection_check)
            if not collection_check.passed:
                # Observation window (CNCT-81): revert to NOT_READY at hard-fail
                # flip; per-check statuses stay as they are.
                return PreflightOutput(status=PreflightStatus.PARTIAL, checks=checks)

            native_check = await self._validate_native_query_permission(client)
            checks.append(native_check)
            if not native_check.passed:
                # Observation window (CNCT-81): revert to NOT_READY at hard-fail
                # flip; per-check statuses stay as they are.
                return PreflightOutput(status=PreflightStatus.PARTIAL, checks=checks)

            dashboard_check = await self._validate_dashboard_count(
                client, include_filter, exclude_filter
            )
            question_check = await self._validate_question_count(
                client, include_filter, exclude_filter
            )
            checks.append(dashboard_check)
            checks.append(question_check)

            advisory_ok = dashboard_check.passed and question_check.passed
            return PreflightOutput(
                status=PreflightStatus.READY
                if advisory_ok
                else PreflightStatus.PARTIAL,
                checks=checks,
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

    async def _authentication_check(
        self, credentials: list[HandlerCredential] | dict[str, Any]
    ) -> tuple[PreflightCheck, MetabaseApiClient | None]:
        """Reachability + authentication tier.

        Returns ``(check, client)``. On success the caller reuses the returned
        client for the remaining tiers; on failure ``client`` is ``None`` and a
        freshly-built client (if any) is closed here. Failures are attributed by
        type — malformed/absent credentials to ``InvalidInputError``, a rejected
        session to the ``AuthError`` the client raised, anything else to
        ``SourceUnavailableError``. This tier deliberately fails *closed*: unlike
        the whole-preflight wrapper, an unresolved client means extraction cannot
        run at all, so blocking with a typed error beats a mid-run crash.
        """
        start = time.perf_counter()
        client: MetabaseApiClient | None = None
        try:
            client = await self._client_for(credentials)
            await client.test_connection()
            return (
                PreflightCheck(
                    name="authenticationCheck",
                    passed=True,
                    message="Authentication successful",
                    duration_ms=self._elapsed_ms(start),
                ),
                client,
            )
        except (InvalidInputError, AuthError) as exc:
            check = self._failed_check(
                "authenticationCheck", exc, start, PreflightStatus.NOT_READY
            )
        except Exception as exc:
            logger.warning("authenticationCheck failed", exc_info=True)
            check = self._failed_check(
                "authenticationCheck",
                MetabaseSourceUnavailableError(
                    message="Could not reach the Metabase host.",
                    source_type="metabase",
                    cause=exc,
                ),
                start,
                PreflightStatus.NOT_READY,
            )
        if client is not None and self.client is None:
            await client.close()
        return check, None

    def _resolve_filters(
        self, input: PreflightInput
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read include / exclude filters, tolerating both wire shapes.

        Filters arrive under ``metadata`` (curl / docs path) or
        ``connection_config`` (v3 preflight-runner path). Try both so one handler
        serves the UI form, the integration runner, and direct API consumers.
        """
        include, exclude = self._read_filters(input.metadata)
        if not include and not exclude:
            include, exclude = self._read_filters(
                getattr(input, "connection_config", None)
            )
        return include, exclude

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        """Milliseconds elapsed since ``start`` (``time.perf_counter``)."""
        return round((time.perf_counter() - start) * 1000, 2)

    @staticmethod
    def _failed_check(
        name: str, error: Any, start: float, status: PreflightStatus
    ) -> PreflightCheck:
        """Build a failed ``PreflightCheck`` carrying a typed failure detail.

        The user-facing text comes from ``error.message`` — the SDK ignores the
        deprecated ``PreflightCheck.message`` for a failed check with a typed
        ``error`` — so the check message mirrors it; diagnostics ride the
        ``cause`` / ``evidence`` chain, never the message.

        ``status`` is mandatory (CNCT-81): a blocking check stamps
        ``NOT_READY``, an advisory one ``PARTIAL``. A failed check left unstamped
        is emitted as ``"unset"`` in the gate check matrix, so requiring it here
        makes an unstamped failure impossible to write by accident.
        """
        return PreflightCheck(
            name=name,
            passed=False,
            status=status,
            message=getattr(error, "message", "") or "",
            error=error.to_failure_details(),
            duration_ms=MetabaseHandler._elapsed_ms(start),
        )

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
        start = time.perf_counter()
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
                duration_ms=MetabaseHandler._elapsed_ms(start),
            )
        except MetabaseSourceUnavailableError as exc:
            logger.warning("collectionCountCheck failed", exc_info=True)
            error = (
                MetabaseCollectionAccessError(cause=exc)
                if exc.http_status in (401, 403)
                else exc
            )
            return MetabaseHandler._failed_check(
                "collectionCountCheck", error, start, PreflightStatus.NOT_READY
            )
        except Exception as exc:
            logger.warning("collectionCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "collectionCountCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch Metabase collections.",
                    source_type="metabase",
                    endpoint="/api/collection",
                    cause=exc,
                ),
                start,
                PreflightStatus.NOT_READY,
            )

    @staticmethod
    async def _validate_dashboard_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
    ) -> PreflightCheck:
        start = time.perf_counter()
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
                duration_ms=MetabaseHandler._elapsed_ms(start),
            )
        except MetabaseSourceUnavailableError as exc:
            logger.warning("dashboardCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "dashboardCountCheck", exc, start, PreflightStatus.PARTIAL
            )
        except Exception as exc:
            logger.warning("dashboardCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "dashboardCountCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch Metabase dashboards.",
                    source_type="metabase",
                    endpoint="/api/dashboard",
                    cause=exc,
                ),
                start,
                PreflightStatus.PARTIAL,
            )

    @staticmethod
    async def _validate_question_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
    ) -> PreflightCheck:
        start = time.perf_counter()
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
                duration_ms=MetabaseHandler._elapsed_ms(start),
            )
        except MetabaseSourceUnavailableError as exc:
            logger.warning("questionCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "questionCountCheck", exc, start, PreflightStatus.PARTIAL
            )
        except Exception as exc:
            logger.warning("questionCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "questionCountCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch Metabase questions.",
                    source_type="metabase",
                    endpoint="/api/card",
                    cause=exc,
                ),
                start,
                PreflightStatus.PARTIAL,
            )

    @staticmethod
    async def _validate_native_query_permission(
        client: MetabaseApiClient,
    ) -> PreflightCheck:
        start = time.perf_counter()
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

            # Newer Metabase wraps databases under {"data": [...]}; older
            # versions return a bare list. Handle both without a .get on a list.
            body: Any = response.json()
            if isinstance(body, dict):
                databases = body.get("data", [])
            elif isinstance(body, list):
                databases = body
            else:
                databases = []
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
                    duration_ms=MetabaseHandler._elapsed_ms(start),
                )
            return MetabaseHandler._failed_check(
                "nativeQueryPermissionCheck",
                MetabaseNativeQueryPermissionError(missing_databases=missing),
                start,
                PreflightStatus.NOT_READY,
            )
        except MetabaseSourceUnavailableError as exc:
            logger.warning("nativeQueryPermissionCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "nativeQueryPermissionCheck", exc, start, PreflightStatus.NOT_READY
            )
        except Exception as exc:
            logger.warning("nativeQueryPermissionCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "nativeQueryPermissionCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch the Metabase database list.",
                    source_type="metabase",
                    endpoint="/api/database",
                    cause=exc,
                ),
                start,
                PreflightStatus.NOT_READY,
            )
