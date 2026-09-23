"""Registered preflight behaviour scenarios (conformance F016).

Every test here drives the **real** ``MetabaseHandler.preflight_check`` and
validates its output with ``assert_preflight_result``. Metabase is faked only at
the HTTP transport seam — :class:`MetabaseSource` below is the app-owned source
adapter the F016 guide asks for — so the credential parsing, the API client, its
session handshake, every probe, the error classifier and the verdict
aggregation all execute for real. Mirrors ``atlan-openapi-app``
tests/unit/test_preflight_conformance.py, which uses respx for the same seam;
this app has no respx dependency and the SDK's ``BaseClient`` documents
``http_retry_transport`` as the supported override, so an ``httpx.MockTransport``
installed there is the equivalent injection point.

Mandatory probes for this handler, declared once here rather than inferred from
names (the guide explicitly forbids guessing them) — the roles come from
``app/handler.py``'s ``_MANDATORY_CHECKS`` frozenset and its tier order:

* ``authenticationCheck`` → ``collectionCountCheck`` →
  ``nativeQueryPermissionCheck``, each short-circuiting the tiers below it.
* ``dashboardCountCheck`` and ``questionCountCheck`` are advisory: either may
  fail without moving the verdict off READY.

The guide's matrix is written for a multi-resource source; two scenario names
needed a mapping decision, recorded here rather than in a commit message:

* ``mixed_resources`` — this connector's distinct resources are its four
  Metabase endpoint families (collections, databases, dashboards, cards). The
  scenario fails one of them and asserts each row is judged on its own evidence.
* ``extraction_fallback`` — there is no in-app retry to exercise. What the run
  actually rests on is probe/extraction *divergence by design*: under the same
  injected failure the probe blocks with a typed error, while extraction
  tolerates it, records a residual and downgrades the run to PARTIAL_SUCCESS
  (``app/residuals.py``, ``app/connector.py`` step 10). The scenario pins both
  halves under one injected 503.

Both entrypoints are registered. ``atlan-application-sdk-conformance`` expects
the full matrix per ``@entrypoint`` it discovers, and this app declares two
(``extract_metadata`` and ``extract_lineage`` in ``app/connector.py``). One
handler serves both, so each scenario runs twice with the entrypoint named on
``PreflightInput.entrypoint`` — the registration is the same code path under the
input the gate would actually send, not a copy with a different label.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
from application_sdk.handler.contracts import (
    BaseConnectionConfig,
    BaseMetadataConfig,
    HandlerCredential,
    PreflightInput,
    PreflightOutput,
)

# ``logger_adaptor`` re-exports loguru's logger, which is where the app's own
# ``get_logger`` output actually lands. ``caplog`` sees none of it: the SDK
# bridges stdlib logging *into* loguru, not the other way round, so asserting on
# ``caplog.text`` for this handler would be vacuous. Reached through the SDK
# module rather than importing loguru directly — loguru is the SDK's dependency,
# not a declared one of this app.
from application_sdk.observability import logger_adaptor
from conformance.preflight_testing import assert_preflight_result, assert_probe_lifetime

from app.client import MetabaseApiClient, build_client
from app.credentials import parse_metabase_credentials
from app.extracts.collections import fetch_collections_summaries
from app.handler import MetabaseHandler
from app.residuals import RESIDUAL_DIR, RESIDUAL_FAILURES_FILE

HOST = "http://metabase.invalid"
PORT = 3000

# Synthetic only — never a real credential. Used to prove the handler keeps the
# password it was handed, and the session token it obtained, out of its output
# and its logs.
SYNTHETIC_PASSWORD = "SyntheticMetabasePassword0000"
SYNTHETIC_SESSION_TOKEN = "SyntheticMetabaseSessionToken0000"

SESSION = "/api/session"
COLLECTION = "/api/collection"
DASHBOARD = "/api/dashboard"
CARD = "/api/card"
DATABASE = "/api/database"

MANDATORY = (
    "authenticationCheck",
    "collectionCountCheck",
    "nativeQueryPermissionCheck",
)
ADVISORY = ("dashboardCountCheck", "questionCountCheck")
OBSERVED = {*MANDATORY, *ADVISORY}

ENTRYPOINTS = ("extract_metadata", "extract_lineage")


def entrypoint_matrix(scenario: str) -> pytest.MarkDecorator:
    """Register one scenario against every ``@entrypoint`` this app declares.

    The marker rides on the parameter rather than the function so each
    entrypoint is registered as its own scenario, which is what the runner
    grades — a single function-level marker would register only one of them and
    leave the other reported as missing.
    """
    return pytest.mark.parametrize(
        "entrypoint",
        [
            pytest.param(
                name,
                id=name,
                marks=pytest.mark.preflight_conformance(
                    rule="F016", scenario=scenario, entrypoint=name
                ),
            )
            for name in ENTRYPOINTS
        ],
    )


# =============================================================================
# The source adapter
# =============================================================================


# ``httpx.MockTransport`` awaits a handler's result on the async path, so a
# responder may be either a plain function or a coroutine function — which is
# what lets the hang and cancellation scenarios suspend inside a request.
Responder = Callable[[httpx.Request], "httpx.Response | Awaitable[httpx.Response]"]


class MetabaseSource:
    """A synthetic Metabase served at the API client's transport seam.

    Owns the five endpoints preflight touches. Each path has a steady-state
    responder plus an optional queue of one-shot responders, so a scenario can
    say "fail once, then recover" without the test knowing how many requests a
    probe makes. Every request is recorded with the deadline the client was
    given (httpx puts it on ``request.extensions``), which is the evidence the
    budget scenarios assert on.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.read_timeouts: list[float] = []
        self._queued: dict[str, list[Responder]] = defaultdict(list)
        self._steady: dict[str, Responder] = {
            SESSION: lambda _r: httpx.Response(
                200, json={"id": SYNTHETIC_SESSION_TOKEN}
            ),
            COLLECTION: lambda _r: httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "Engineering", "personal_owner_id": None},
                    {"id": 2, "name": "Marketing", "personal_owner_id": None},
                    {"id": 3, "name": "Ada's personal", "personal_owner_id": 7},
                ],
            ),
            DASHBOARD: lambda _r: httpx.Response(
                200,
                json=[
                    {"id": 10, "name": "Revenue", "collection_id": 1},
                    {"id": 11, "name": "Signups", "collection_id": 2},
                ],
            ),
            CARD: lambda _r: httpx.Response(
                200,
                json=[
                    {"id": 20, "name": "MRR", "collection_id": 1},
                    {"id": 21, "name": "Churn", "collection_id": 2},
                ],
            ),
            DATABASE: lambda _r: httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "warehouse", "native_permissions": "write"}
                    ]
                },
            ),
        }

    # -- injection -------------------------------------------------------

    def always(self, path: str, responder: Responder) -> None:
        """Replace a path's steady-state responder."""
        self._steady[path] = responder

    def status(self, path: str, code: int) -> None:
        """Answer every request on ``path`` with a bare HTTP status."""
        self.always(path, lambda _r: httpx.Response(code))

    def once(self, path: str, responder: Responder) -> None:
        """Queue one response on ``path``, consumed before the steady state."""
        self._queued[path].append(responder)

    def status_once(self, path: str, code: int) -> None:
        self.once(path, lambda _r: httpx.Response(code))

    # -- transport -------------------------------------------------------

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """Serve one request. Async so the seam matches httpx's ``AsyncHandler``
        and a suspending responder is awaited here rather than leaking a
        coroutine back to the transport."""
        self.requests.append(request)
        timeout = request.extensions.get("timeout") or {}
        read = timeout.get("read")
        if read is not None:
            self.read_timeouts.append(float(read))
        path = request.url.path
        queue = self._queued.get(path)
        responder = queue.pop(0) if queue else self._steady.get(path)
        if responder is None:  # pragma: no cover - a probe hit an unknown path
            raise AssertionError(f"synthetic Metabase has no route for {path}")
        answer = responder(request)
        return await answer if isinstance(answer, Awaitable) else answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> MetabaseSource:
    """Install the synthetic source on every client the handler builds.

    ``BaseClient.__init__`` sets ``http_retry_transport`` and the SDK documents
    it as the supported override, so swapping it here leaves the whole client —
    session handshake, headers, error paths — running for real against a fake
    socket. The handler is constructed with ``client=None`` in every scenario,
    so it really does go through ``parse_metabase_credentials`` and
    ``build_client``.
    """
    adapter = MetabaseSource()
    original = MetabaseApiClient.__init__

    def _init(self: MetabaseApiClient, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.http_retry_transport = adapter.transport

    monkeypatch.setattr(MetabaseApiClient, "__init__", _init)
    return adapter


@pytest.fixture
def closed_clients(monkeypatch: pytest.MonkeyPatch) -> list[MetabaseApiClient]:
    """Independent teardown evidence: every client the handler closed.

    ``assert_probe_lifetime`` wants proof a probe left no background work
    running. The handler closes its client in a ``finally``; this records each
    close so a scenario asserts it happened rather than assuming it.
    """
    closed: list[MetabaseApiClient] = []
    original = MetabaseApiClient.close

    async def _close(self: MetabaseApiClient) -> None:
        closed.append(self)
        await original(self)

    monkeypatch.setattr(MetabaseApiClient, "close", _close)
    return closed


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    """Everything the app logged, at DEBUG, for the duration of one test."""
    buffer = io.StringIO()
    sink = logger_adaptor.logger.add(buffer, level="DEBUG", format="{message}")
    try:
        yield buffer
    finally:
        logger_adaptor.logger.remove(sink)


# =============================================================================
# Inputs
# =============================================================================


def credentials(
    *, password: str = SYNTHETIC_PASSWORD, prefixed: bool = False
) -> list[HandlerCredential]:
    """The ``[{key, value}]`` credential shape the HTTP layer normalises to.

    ``prefixed`` produces the ``extra.``-namespaced spelling that
    ``parse_metabase_credentials`` also has to flatten.
    """
    pairs = {"host": HOST, "port": str(PORT)}
    inner = {"username": "connector", "password": password}
    for key, value in inner.items():
        pairs[f"extra.{key}" if prefixed else key] = value
    return [HandlerCredential(key=key, value=value) for key, value in pairs.items()]


def preflight_input(
    entrypoint: str,
    *,
    creds: list[HandlerCredential] | None = None,
    budget: int = 60,
    metadata: dict[str, Any] | None = None,
    connection_config: dict[str, Any] | None = None,
) -> PreflightInput:
    return PreflightInput(
        credentials=credentials() if creds is None else creds,
        entrypoint=entrypoint,
        timeout_seconds=budget,
        metadata=BaseMetadataConfig(**(metadata or {})),
        connection_config=BaseConnectionConfig(**(connection_config or {})),
    )


async def run_preflight(entrypoint: str, **kwargs: Any) -> PreflightOutput:
    """Drive the real handler, with no pre-built client injected."""
    return await MetabaseHandler().preflight_check(
        preflight_input(entrypoint, **kwargs)
    )


def check(result: PreflightOutput, name: str):
    return next(row for row in result.checks if row.name == name)


def names(result: PreflightOutput) -> set[str]:
    return {row.name for row in result.checks}


# =============================================================================
# Healthy and failure verdicts
# =============================================================================


@entrypoint_matrix("healthy")
async def test_healthy_source_is_ready(source: MetabaseSource, entrypoint: str) -> None:
    """A reachable Metabase answering every endpoint: all five probes pass and
    the verdict is READY, with no probe left unrun."""
    result = await run_preflight(entrypoint)

    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="ready",
        mandatory_order=MANDATORY,
    )
    assert names(result) == OBSERVED
    assert all(row.passed for row in result.checks)
    # The personal collection is excluded from the count, so the probe reports
    # what the crawl would actually see rather than the raw endpoint total.
    assert check(result, "collectionCountCheck").message == "Total collections: 2"


@entrypoint_matrix("mandatory_failure")
async def test_forbidden_collections_block_and_short_circuit(
    source: MetabaseSource, entrypoint: str
) -> None:
    """A 403 on the collection listing is the authorization gate for the whole
    run. It blocks with a PERMISSION attribution and stops the tiers below it:
    no database, dashboard or card request is made at all."""
    source.status(COLLECTION, 403)

    result = await run_preflight(entrypoint)

    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "collectionCountCheck": {
                "category": "PERMISSION",
                "code": "PERMISSION_METABASE_COLLECTION",
                "retryable": False,
                "audience": "USER",
            }
        },
    )
    assert names(result) == {"authenticationCheck", "collectionCountCheck"}
    assert DATABASE not in source.paths()
    assert DASHBOARD not in source.paths()
    assert CARD not in source.paths()


@entrypoint_matrix("advisory_failure")
async def test_failed_dashboard_probe_does_not_move_the_verdict(
    source: MetabaseSource, entrypoint: str
) -> None:
    """An advisory probe failing is reported on its own row with a typed error
    and leaves the verdict READY — extraction can still proceed, and the probe
    behind it still runs."""
    source.status(DASHBOARD, 500)

    result = await run_preflight(entrypoint)

    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "dashboardCountCheck": {
                "category": "SOURCE_UNAVAILABLE",
                "code": "SOURCE_UNAVAILABLE_METABASE",
                "retryable": True,
                "audience": "USER",
            }
        },
    )
    assert names(result) == OBSERVED
    assert check(result, "dashboardCountCheck").passed is False
    # The advisory tier is not short-circuited by its own failure.
    assert check(result, "questionCountCheck").passed is True


# =============================================================================
# Recovery versus exhaustion
# =============================================================================


@entrypoint_matrix("recoverable_transient")
async def test_transient_blocks_then_clears_against_a_recovered_source(
    source: MetabaseSource, entrypoint: str
) -> None:
    """A 503 on a mandatory probe is a retryable fact, so the row carries
    ``retryable=True`` — but the verdict is still NOT_READY, because a probe
    that could not read the source has no evidence the run would succeed.
    Continuation is established by the next attempt actually succeeding against
    the recovered source, never by the error's retryable flag alone."""
    source.status_once(COLLECTION, 503)

    blocked = await run_preflight(entrypoint)

    assert_preflight_result(
        blocked,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "collectionCountCheck": {
                "code": "SOURCE_UNAVAILABLE_METABASE",
                "retryable": True,
            }
        },
    )

    recovered = await run_preflight(entrypoint)

    assert_preflight_result(
        recovered,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="ready",
        mandatory_order=MANDATORY,
    )
    assert all(row.passed for row in recovered.checks)


@entrypoint_matrix("persistent_failure")
async def test_persistent_denial_is_the_same_verdict_on_every_attempt(
    source: MetabaseSource, entrypoint: str
) -> None:
    """A revoked grant is a stable, customer-fixable fact. Exhaustion for this
    connector looks like the same typed NOT_READY twice — never a fail-open
    that lets a run start against a source it cannot read."""
    source.status(COLLECTION, 401)

    for _ in range(2):
        result = await run_preflight(entrypoint)
        assert_preflight_result(
            result,
            required_checks=set(MANDATORY),
            observed_checks=OBSERVED,
            expected_status="not_ready",
            mandatory_order=MANDATORY,
            expected_errors={
                "collectionCountCheck": {
                    "code": "PERMISSION_METABASE_COLLECTION",
                    "retryable": False,
                }
            },
        )


# =============================================================================
# Resource and input shapes
# =============================================================================


@entrypoint_matrix("mixed_resources")
async def test_each_endpoint_family_is_judged_on_its_own_evidence(
    source: MetabaseSource, entrypoint: str
) -> None:
    """This connector's distinct resources are its four Metabase endpoint
    families. With dashboards broken and collections, databases and cards
    healthy, each row carries its own outcome — one resource's failure neither
    suppresses another's evidence nor leaks into its verdict."""
    source.status(DASHBOARD, 502)

    result = await run_preflight(entrypoint)

    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="ready",
        mandatory_order=MANDATORY,
    )
    assert {row.name: row.passed for row in result.checks} == {
        "authenticationCheck": True,
        "collectionCountCheck": True,
        "nativeQueryPermissionCheck": True,
        "dashboardCountCheck": False,
        "questionCountCheck": True,
    }
    # The failed resource is the only one carrying an error, and the passed ones
    # carry counts rather than inheriting its message.
    assert check(result, "dashboardCountCheck").error is not None
    assert check(result, "questionCountCheck").message == "Total questions: 2"


@entrypoint_matrix("extraction_fallback")
async def test_probe_blocks_where_extraction_degrades_under_one_failure(
    source: MetabaseSource, entrypoint: str, tmp_path
) -> None:
    """Preflight and extraction deliberately diverge on the same failure, and
    both halves have to hold or the gate is judging something the run does not
    do.

    Under one injected 503 on the collection listing: the probe blocks with a
    typed SOURCE_UNAVAILABLE row (no evidence, no run), while extraction
    tolerates it — catches the typed ``MetabaseSourceUnavailableError``,
    returns the empty sentinel and records a residual, which is what makes
    ``app/connector.py`` step 10 declare the run PARTIAL_SUCCESS instead of
    publishing a gap as a complete crawl.
    """
    source.status(COLLECTION, 503)

    blocked = await run_preflight(entrypoint)

    assert_preflight_result(
        blocked,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "collectionCountCheck": {"code": "SOURCE_UNAVAILABLE_METABASE"}
        },
    )

    client = await build_client(parse_metabase_credentials(credentials()))
    try:
        records = await fetch_collections_summaries(client, str(tmp_path))
    finally:
        await client.close()

    assert records == []
    residual = tmp_path / RESIDUAL_DIR / RESIDUAL_FAILURES_FILE
    assert residual.exists(), "a tolerated extraction failure must leave a residual"
    assert "collections_fetch_failed" in residual.read_text()


@entrypoint_matrix("credential_entrypoint_shapes")
async def test_every_supported_credential_shape_produces_a_typed_verdict(
    source: MetabaseSource, entrypoint: str
) -> None:
    """Each credential spelling the entrypoint accepts resolves to a truthful
    verdict, including the two absence cases, which must block rather than pass
    vacuously. Filters are read from both wire shapes the handler supports."""
    shapes: list[tuple[dict[str, Any], str]] = [
        ({"creds": credentials()}, "ready"),
        ({"creds": credentials(prefixed=True)}, "ready"),
        (
            {"metadata": {"include-collections": {"1": {}}}},
            "ready",
        ),
        (
            {"connection_config": {"exclude-collections": {"2": {}}}},
            "ready",
        ),
        ({"creds": []}, "not_ready"),
    ]
    for kwargs, expected in shapes:
        result = await run_preflight(entrypoint, **kwargs)
        assert_preflight_result(
            result,
            required_checks=set(MANDATORY),
            observed_checks=OBSERVED,
            expected_status=expected,
            mandatory_order=MANDATORY,
        )

    # A rejected session is the other absence case: credentials present, but not
    # usable. It must be attributed to AUTH, not to an unreachable source.
    source.status(SESSION, 401)
    rejected = await run_preflight(entrypoint)
    assert_preflight_result(
        rejected,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "authenticationCheck": {
                "category": "AUTH",
                "code": "AUTH_METABASE_SESSION",
                "audience": "USER",
            }
        },
    )


# =============================================================================
# Probe lifetime: absence, hangs, cancellation, budgets
# =============================================================================


@entrypoint_matrix("no_probe")
async def test_absent_credentials_block_without_touching_the_source(
    source: MetabaseSource, entrypoint: str
) -> None:
    """With no credentials there is nothing to probe. The verdict is decided
    from the input alone — a typed INVALID_INPUT NOT_READY, not a vacuous READY
    — and not one request reaches Metabase."""
    result = await run_preflight(entrypoint, creds=[])

    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "authenticationCheck": {
                "category": "INVALID_INPUT",
                "code": "INVALID_INPUT_METABASE_CLIENT_NOT_INITIALIZED",
                "retryable": False,
            }
        },
    )
    assert source.requests == []
    assert names(result) == {"authenticationCheck"}


@entrypoint_matrix("hung_probe")
async def test_unanswered_probe_stays_inside_the_budget_and_cleans_up(
    source: MetabaseSource,
    closed_clients: list[MetabaseApiClient],
    entrypoint: str,
) -> None:
    """An endpoint that will not answer must not outlive the gate's budget, and
    must leave no client behind.

    The transport is faked, so the sleep-then-ReadTimeout below reproduces what
    httpx reports for a hung endpoint rather than exercising httpx's own timer.
    What this does prove against the real handler: the deadline it hands the
    client is strictly inside the enforced budget, the call returns within that
    budget with a typed verdict rather than being killed by the gate, and the
    client is closed on the failure path.
    """
    budget = 5

    async def _hang(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        raise httpx.ReadTimeout("endpoint did not answer")

    source.always(COLLECTION, _hang)

    started = time.monotonic()
    result = await run_preflight(entrypoint, budget=budget)
    elapsed = time.monotonic() - started

    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        expected_errors={
            "collectionCountCheck": {"code": "SOURCE_UNAVAILABLE_METABASE"}
        },
    )
    assert source.read_timeouts and all(
        deadline < budget for deadline in source.read_timeouts
    ), "every probe deadline must sit strictly inside the enforced budget"
    assert_probe_lifetime(
        elapsed=elapsed,
        budget=float(budget),
        background_stopped=bool(closed_clients),
    )


@entrypoint_matrix("cancellation_cleanup")
async def test_external_cancellation_propagates_and_closes_the_client(
    source: MetabaseSource,
    closed_clients: list[MetabaseApiClient],
    entrypoint: str,
) -> None:
    """External cancellation must be preserved rather than swallowed into a
    verdict — a cancelled gate that returned NOT_READY would report a source
    problem that was never observed — and it must not leak the probe's client.
    """
    budget = 5
    probing = asyncio.Event()

    async def _block(_request: httpx.Request) -> httpx.Response:
        probing.set()
        await asyncio.sleep(30)
        raise AssertionError("probe was not cancelled")  # pragma: no cover

    source.always(COLLECTION, _block)

    task = asyncio.create_task(run_preflight(entrypoint, budget=budget))
    await asyncio.wait_for(probing.wait(), timeout=budget)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - started

    assert_probe_lifetime(
        elapsed=elapsed,
        budget=float(budget),
        background_stopped=bool(closed_clients),
    )

    # And the handler is not left poisoned: a fresh attempt against the
    # recovered source returns a truthful READY.
    source.always(COLLECTION, MetabaseSource()._steady[COLLECTION])
    result = await run_preflight(entrypoint, budget=budget)
    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="ready",
        mandatory_order=MANDATORY,
    )


@entrypoint_matrix("budget_retry")
async def test_probe_deadline_scales_with_the_remaining_gate_budget(
    source: MetabaseSource,
    closed_clients: list[MetabaseApiClient],
    entrypoint: str,
) -> None:
    """``timeout_seconds`` is the *enforced remaining* budget, so a later gate
    attempt gets a smaller one and every probe must shrink with it. A deadline
    that can exceed its budget makes the gate's cancel decorative: the handler
    is killed mid-probe and the gate gets no check evidence at all."""
    budgets = (60, 10, 3)
    first_deadlines: list[float] = []

    for index, budget in enumerate(budgets, start=1):
        before = len(source.read_timeouts)
        started = time.monotonic()
        result = await run_preflight(entrypoint, budget=budget)
        elapsed = time.monotonic() - started
        this_run = source.read_timeouts[before:]

        assert_preflight_result(
            result,
            required_checks=set(MANDATORY),
            observed_checks=OBSERVED,
            expected_status="ready",
            mandatory_order=MANDATORY,
        )
        # Measured against THIS attempt's own remaining budget. Timing the three
        # together against their sum would pass on any timing at all — only the
        # tightest budget constrains anything, and the sum hides it.
        assert_probe_lifetime(
            elapsed=elapsed,
            budget=float(budget),
            background_stopped=len(closed_clients) == index,
        )
        assert this_run and all(
            deadline < budget for deadline in this_run
        ), "every probe deadline must stay inside its own attempt's budget"
        # Within one attempt the budget only shrinks as probes consume it.
        assert this_run == sorted(this_run, reverse=True)
        first_deadlines.append(this_run[0])

    assert first_deadlines == sorted(
        first_deadlines, reverse=True
    ), "a shrinking remaining budget must shrink the probe deadline"
    assert len(set(first_deadlines)) == len(
        budgets
    ), "each budget must produce its own deadline, not a constant"


# =============================================================================
# Safe typed output
# =============================================================================


@entrypoint_matrix("typed_safe_output")
async def test_password_and_session_token_never_reach_the_output_or_the_logs(
    source: MetabaseSource,
    captured_logs: io.StringIO,
    entrypoint: str,
) -> None:
    """The failure path is the dangerous one: it populates messages, evidence
    and ``cause_repr`` fields that travel to Temporal history, the Automation
    Engine and the connector-pulse check matrix — none of which the SDK redacts.
    Two secrets pass through this handler on that path: the password it was
    handed, and the session token it obtained and set as a request header."""
    source.status(COLLECTION, 403)

    result = await run_preflight(entrypoint)

    secrets = (SYNTHETIC_PASSWORD, SYNTHETIC_SESSION_TOKEN)
    assert_preflight_result(
        result,
        required_checks=set(MANDATORY),
        observed_checks=OBSERVED,
        expected_status="not_ready",
        mandatory_order=MANDATORY,
        synthetic_secrets=secrets,
        captured_logs=captured_logs.getvalue(),
        expected_errors={
            "collectionCountCheck": {
                "category": "PERMISSION",
                "code": "PERMISSION_METABASE_COLLECTION",
                "audience": "USER",
            }
        },
    )
    # Pin the two carriers the assertion covers as a set, so a future field that
    # starts echoing input is caught here rather than in production.
    wire = result.model_dump_json()
    logs = captured_logs.getvalue()
    for secret in secrets:
        assert secret not in wire
        assert secret not in logs
    # The handshake really did run — otherwise the token assertion is vacuous.
    assert SESSION in source.paths()
    assert logs, "the probe must log its failure for engineers"
