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
from application_sdk.errors.base import sanitize_cause_repr
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

# Checks whose failure blocks extraction. Everything else is advisory: it is
# reported in its own PreflightCheck row and leaves the verdict READY. Declared
# here, as a literal, so the preflight analysis can resolve the roles behind the
# verdict without executing the handler (F019).
_MANDATORY_CHECKS = frozenset(
    {
        "authenticationCheck",
        "collectionCountCheck",
        "nativeQueryPermissionCheck",
    }
)

# Longest deadline any single preflight probe may ask for, in seconds. Caps the
# per-probe slice when the gate hands down a generous budget; the historical
# value, kept so a default-budget run behaves exactly as before.
_PROBE_TIMEOUT_CAP = 30

# Fraction of the *remaining* gate budget one probe may consume. Strictly below
# 1.0 so a probe's deadline always sits inside the budget the gate will cancel
# at — a probe allowed the whole remainder would be killed mid-flight and the
# gate would get no check evidence at all (see PreflightInput.timeout_seconds).
_PROBE_BUDGET_FRACTION = 0.9


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
        A failed *mandatory* check (see ``_MANDATORY_CHECKS``) blocks the run and
        short-circuits the tiers below it, so a broken source is not probed
        further. An *advisory* failure is reported in its own check row and does
        **not** change the verdict — extraction proceeds, so the verdict stays
        ``READY``. Unhandled errors are deliberately *not* caught here — a
        plumbing bug should fail the gate open (SDK logs and proceeds), never
        silently block every run.

        One terminal ``PreflightOutput``, with the mandatory/advisory roles in a
        module-level frozenset: that is the shape the preflight analysis can read
        statically (F019), and it mirrors ``atlan-openapi-app`` app/handler.py.
        ``PreflightStatus.PARTIAL`` is deprecated and removed in SDK v3.40.0
        (B001); READY and NOT_READY are the only two verdicts.

        Every probe is bounded by what remains of ``input.timeout_seconds``. On
        the injected gate path that field is the *enforced* remaining budget and
        the gate cancels the handler when it elapses — a handler whose probes can
        outlive it is killed with no check evidence, which is the one outcome
        worse than NOT_READY. ``_probe_timeout`` keeps each deadline strictly
        inside the remainder.
        """
        deadline = time.monotonic() + max(input.timeout_seconds, 0)
        checks: list[PreflightCheck] = []

        auth_check, client = await self._authentication_check(
            input.credentials, timeout=self._probe_timeout(deadline)
        )
        checks.append(auth_check)

        if client is not None:
            owns_client = self.client is None
            try:
                include_filter, exclude_filter = self._resolve_filters(input)

                collection_check = await self._validate_collection_count(
                    client,
                    include_filter,
                    exclude_filter,
                    timeout=self._probe_timeout(deadline),
                )
                checks.append(collection_check)

                if collection_check.passed:
                    native_check = await self._validate_native_query_permission(
                        client, timeout=self._probe_timeout(deadline)
                    )
                    checks.append(native_check)

                    if native_check.passed:
                        checks.append(
                            await self._validate_dashboard_count(
                                client,
                                include_filter,
                                exclude_filter,
                                timeout=self._probe_timeout(deadline),
                            )
                        )
                        checks.append(
                            await self._validate_question_count(
                                client,
                                include_filter,
                                exclude_filter,
                                timeout=self._probe_timeout(deadline),
                            )
                        )
            finally:
                if owns_client:
                    await client.close()

        mandatory_failed = any(
            not check.passed for check in checks if check.name in _MANDATORY_CHECKS
        )
        return PreflightOutput(
            status=PreflightStatus.NOT_READY
            if mandatory_failed
            else PreflightStatus.READY,
            checks=checks,
        )

    # ------------------------------------------------------------------
    # SHARED HELPERS
    # ------------------------------------------------------------------

    async def _client_for(
        self,
        credentials: list[HandlerCredential] | dict[str, Any],
        *,
        timeout: int = 30,
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
        return await build_client(credential, timeout=timeout)

    async def _authentication_check(
        self,
        credentials: list[HandlerCredential] | dict[str, Any],
        *,
        timeout: int = 30,
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
            client = await self._client_for(credentials, timeout=timeout)
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
            # No ``exc_info`` here, unlike the other probes: this ``try`` reads
            # the credentials, so a traceback would carry the password in its
            # frame locals past every redaction under loguru's ``diagnose``
            # (F014). The sanitized, capped cause is enough for an engineer;
            # the customer-facing outcome is the typed error on the check row.
            # DEBUG, not WARNING, for the same reason as every other probe: the
            # gate levels the verdict row itself and a handler-authored WARNING
            # is both a duplicate and invisible under the customer's default
            # ERROR filter (F005 / FND-901).
            logger.debug("authenticationCheck failed: %s", sanitize_cause_repr(exc))
            check = self._failed_check("authenticationCheck", exc, start)
        except Exception as exc:
            logger.debug("authenticationCheck failed: %s", sanitize_cause_repr(exc))
            check = self._failed_check(
                "authenticationCheck",
                MetabaseSourceUnavailableError(
                    message="Could not reach the Metabase host.",
                    source_type="metabase",
                    cause=exc,
                ),
                start,
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
    def _probe_timeout(deadline: float) -> int:
        """Seconds the next probe may take, strictly inside the gate budget.

        ``deadline`` is a ``time.monotonic`` instant. Each probe gets a fraction
        of what is left rather than all of it, so the sum of the probes cannot
        reach the budget the gate cancels at, and a later probe in the same run
        gets a smaller deadline than an earlier one. Floored at 1s: a request has
        to be attempted even on an exhausted budget, because a probe that never
        ran yields no evidence either. Returned as ``int`` to match the SDK
        client's ``timeout`` parameter.
        """
        remaining = deadline - time.monotonic()
        return max(1, min(_PROBE_TIMEOUT_CAP, int(remaining * _PROBE_BUDGET_FRACTION)))

    @staticmethod
    def _failed_check(name: str, error: Any, start: float) -> PreflightCheck:
        """Build a failed ``PreflightCheck`` carrying a typed failure detail.

        The user-facing text comes from ``error.message`` — the SDK ignores the
        deprecated ``PreflightCheck.message`` for a failed check with a typed
        ``error`` — so the check message mirrors it; diagnostics ride the
        ``cause`` / ``evidence`` chain, never the message.
        """
        return PreflightCheck(
            name=name,
            passed=False,
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
        *,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        url = MetabaseUrls.collection(client.host, client.port)
        response = await client.execute_http_get_request(url=url, timeout=timeout)
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
        *,
        timeout: int = 30,
    ) -> PreflightCheck:
        start = time.perf_counter()
        try:
            collections = await MetabaseHandler._fetch_collections(
                client, timeout=timeout
            )
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
            # DEBUG, not WARNING: the probe returns the failure typed, on the
            # check row below, and the preflight gate owns the customer-facing
            # outcome and levels it from the verdict. A handler-authored WARNING
            # is both a duplicate of that row and invisible under the customer's
            # default ERROR filter (F005 / FND-901). DEBUG keeps the traceback
            # for engineers without adding a second customer-visible record —
            # the same applies to every probe below.
            logger.debug("collectionCountCheck failed", exc_info=True)
            error = (
                MetabaseCollectionAccessError(cause=exc)
                if exc.http_status in (401, 403)
                else exc
            )
            return MetabaseHandler._failed_check("collectionCountCheck", error, start)
        except Exception as exc:
            logger.debug("collectionCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "collectionCountCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch Metabase collections.",
                    source_type="metabase",
                    endpoint="/api/collection",
                    cause=exc,
                ),
                start,
            )

    @staticmethod
    async def _validate_dashboard_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
        *,
        timeout: int = 30,
    ) -> PreflightCheck:
        start = time.perf_counter()
        try:
            collections = await MetabaseHandler._fetch_collections(
                client, timeout=timeout
            )
            effective_exclude: dict[str, Any] = dict(exclude_filter)
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    effective_exclude[str(collection.get("id", ""))] = {}

            url = MetabaseUrls.dashboard(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=timeout)
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
            logger.debug("dashboardCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check("dashboardCountCheck", exc, start)
        except Exception as exc:
            logger.debug("dashboardCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "dashboardCountCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch Metabase dashboards.",
                    source_type="metabase",
                    endpoint="/api/dashboard",
                    cause=exc,
                ),
                start,
            )

    @staticmethod
    async def _validate_question_count(
        client: MetabaseApiClient,
        include_filter: dict[str, Any],
        exclude_filter: dict[str, Any],
        *,
        timeout: int = 30,
    ) -> PreflightCheck:
        start = time.perf_counter()
        try:
            collections = await MetabaseHandler._fetch_collections(
                client, timeout=timeout
            )
            effective_exclude: dict[str, Any] = dict(exclude_filter)
            for collection in collections:
                if collection.get("personal_owner_id") is not None:
                    effective_exclude[str(collection.get("id", ""))] = {}

            url = MetabaseUrls.card(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=timeout)
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
            logger.debug("questionCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check("questionCountCheck", exc, start)
        except Exception as exc:
            logger.debug("questionCountCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "questionCountCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch Metabase questions.",
                    source_type="metabase",
                    endpoint="/api/card",
                    cause=exc,
                ),
                start,
            )

    @staticmethod
    async def _validate_native_query_permission(
        client: MetabaseApiClient,
        *,
        timeout: int = 30,
    ) -> PreflightCheck:
        start = time.perf_counter()
        try:
            url = MetabaseUrls.database(client.host, client.port)
            response = await client.execute_http_get_request(url=url, timeout=timeout)
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
            )
        except MetabaseSourceUnavailableError as exc:
            logger.debug("nativeQueryPermissionCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "nativeQueryPermissionCheck", exc, start
            )
        except Exception as exc:
            logger.debug("nativeQueryPermissionCheck failed", exc_info=True)
            return MetabaseHandler._failed_check(
                "nativeQueryPermissionCheck",
                MetabaseSourceUnavailableError(
                    message="Failed to fetch the Metabase database list.",
                    source_type="metabase",
                    endpoint="/api/database",
                    cause=exc,
                ),
                start,
            )
