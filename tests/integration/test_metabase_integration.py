"""Integration tests for the Metabase connector.

Exercises the three v3 handler endpoints (``auth`` / ``preflight`` /
``metadata``) against a live Metabase tenant. Each scenario is dispatched
by :class:`BaseIntegrationTest` to the running app server (default
``http://localhost:8000``) and the response is asserted with the SDK's
``equals`` / ``is_dict`` / ``is_list`` / ``is_string`` predicates.

Workflow scenarios (``api="workflow"``) are intentionally omitted for now
— they require a ``credential_guid`` pre-seeded in the Dapr secret store
(see PR #15's "Phase 6 known follow-up" note); the SDK's ``/start``
handler strips inline credentials before workflow dispatch.

Prerequisites
-------------
1. ``tests/.env`` (gitignored) populated with::

       ATLAN_APPLICATION_NAME=metabase
       E2E_METABASE_HOST=https://acme.metabaseapp.com
       E2E_METABASE_PORT=443
       E2E_METABASE_USERNAME=...
       E2E_METABASE_PASSWORD=...

2. Services + app server running::

       uv run --env-file tests/.env -- atlan app run -p .

3. Run::

       uv run --env-file tests/.env -- pytest tests/integration/ -v
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from application_sdk.testing.integration import BaseIntegrationTest, Scenario
from application_sdk.testing.integration.assertions import (
    equals,
    is_dict,
    is_list,
    is_not_empty,
    is_string,
)


def _basic_creds(**overrides: Any) -> Dict[str, Any]:
    """Single auth type for Metabase: basic (session-token via POST /api/session).

    Matches the ``MetabaseCredential`` contract in ``app/contracts.py``:
    ``host`` is stored WITH the protocol prefix because the v3 connector
    concatenates ``{{host}}:{{port}}`` without prepending a scheme.
    """
    port_env = os.environ.get("E2E_METABASE_PORT", "443")
    creds: Dict[str, Any] = {
        "authType": "basic",
        "host": os.environ.get("E2E_METABASE_HOST", ""),
        "port": int(port_env) if str(port_env).isdigit() else port_env,
        "username": os.environ.get("E2E_METABASE_USERNAME", ""),
        "password": os.environ.get("E2E_METABASE_PASSWORD", ""),
    }
    creds.update(overrides)
    return creds


_VALID = _basic_creds()


class TestMetabaseIntegration(BaseIntegrationTest):
    """Metabase integration suite — auth / preflight / metadata."""

    app_url = "http://localhost:8000"
    app_name = "metabase"

    default_connection: Dict[str, Any] = {
        "connection_qualified_name": "default/metabase/test_integration",
        "connection_name": "test_metabase",
    }

    default_credentials: Dict[str, Any] = _basic_creds()

    scenarios: List[Scenario] = [
        # -- auth --------------------------------------------------------------
        Scenario(
            name="auth_valid_credentials",
            api="auth",
            assert_that={
                "success": equals(True),
                "data.status": equals("success"),
                "data.message": equals("Authentication successful"),
            },
            description="Valid Metabase credentials yield a session token and AuthStatus.SUCCESS.",
        ),
        Scenario(
            name="auth_response_shape",
            api="auth",
            assert_that={
                "success": equals(True),
                "data": is_dict(),
                "data.status": is_string(),
                "data.message": is_string(),
            },
            description="Auth response has the v3 envelope shape (success / data{status,message}).",
        ),
        Scenario(
            name="auth_wrong_password",
            api="auth",
            credentials=_basic_creds(password="definitely-not-the-real-password"),
            assert_that={
                "success": equals(False),
                "data.status": equals("failed"),
            },
            description="Correct user, wrong password fails the /api/session round-trip.",
        ),
        Scenario(
            name="auth_wrong_username",
            api="auth",
            credentials=_basic_creds(username="not-a-real-user@example.invalid"),
            assert_that={
                "success": equals(False),
                "data.status": equals("failed"),
            },
            description="Unknown user fails auth.",
        ),
        # -- metadata ----------------------------------------------------------
        Scenario(
            name="metadata_returns_collection_tree",
            api="metadata",
            assert_that={
                "success": equals(True),
                "data": is_list(),
            },
            description="fetch_metadata returns ApiMetadataOutput.objects as a flat list (v3 shape).",
        ),
        Scenario(
            name="metadata_objects_have_apitree_shape",
            api="metadata",
            assert_that={
                "success": equals(True),
                # The runner's _get_nested_value splits on '.' only — list
                # indexing uses ``data.0.value`` (numeric segment), not the
                # bracketed ``data[0].value`` form.
                "data.0.value": is_string(),
                "data.0.title": is_string(),
                "data.0.node_type": equals("collection"),
            },
            description="Each apitree node has value/title/node_type (v3 ApiMetadataObject).",
        ),
        # -- preflight ---------------------------------------------------------
        Scenario(
            name="preflight_check_all_pass",
            api="preflight",
            assert_that={
                "success": equals(True),
                "data": is_dict(),
                "data.collectionCountCheck.success": equals(True),
                "data.dashboardCountCheck.success": equals(True),
                "data.questionCountCheck.success": equals(True),
                "data.nativeQueryPermissionCheck.success": equals(True),
            },
            description=(
                "All four sageTemplate checks pass against the test tenant. "
                "Service layer auto-converts PreflightOutput.checks to v2 "
                "camelCase keys (collectionCountCheck etc.) so existing "
                "frontends keep working."
            ),
        ),
        Scenario(
            name="preflight_returns_check_messages",
            api="preflight",
            assert_that={
                "success": equals(True),
                "data.collectionCountCheck.message": is_not_empty(),
                "data.dashboardCountCheck.message": is_not_empty(),
                "data.questionCountCheck.message": is_not_empty(),
            },
            description="Each check has a non-empty human-readable message.",
        ),
        # -- preflight filter behaviour ----------------------------------------
        # All four sage checks accept include-collections / exclude-collections
        # as JSON-encoded dicts keyed by collection id (apitree widget shape).
        # Empty filter = include everything.
        Scenario(
            name="preflight_with_include_filter_root_only",
            api="preflight",
            metadata={
                "include-collections": '{"root": {}}',
                "exclude-collections": "{}",
            },
            assert_that={
                "success": equals(True),
                # Only the root collection survives; dashboards/questions
                # whose collection_id is not "root" get filtered out.
                "data.collectionCountCheck.success": equals(True),
                "data.collectionCountCheck.message": equals("Total collections: 1"),
            },
            description=(
                "include-collections filter narrows the count to the matching "
                "id only. Verifies the handler honours apitree widget payloads."
            ),
        ),
        Scenario(
            name="preflight_with_exclude_all_filter",
            api="preflight",
            metadata={
                "include-collections": "{}",
                # Every non-personal collection id visible on the test
                # tenant. Refresh this list by curling /workflows/v1/metadata
                # if the tenant gains or loses collections.
                "exclude-collections": (
                    '{"root": {}, "3": {}, "4": {}, "5": {}, '
                    '"34": {}, "67": {}, "133": {}}'
                ),
            },
            assert_that={
                "success": equals(True),
                # Every visible collection on the test tenant is in the
                # exclude filter, so the collection count drops to zero.
                # Dashboards / questions with ``collection_id = null`` (not
                # in any tracked collection) survive the filter; we only
                # assert those checks ran successfully — not exact zeros.
                "data.collectionCountCheck.success": equals(True),
                "data.collectionCountCheck.message": equals("Total collections: 0"),
                "data.dashboardCountCheck.success": equals(True),
                "data.questionCountCheck.success": equals(True),
                "data.nativeQueryPermissionCheck.success": equals(True),
            },
            description=(
                "exclude-collections covering every known collection drives "
                "the collection count to zero. dashboardCountCheck and "
                "questionCountCheck still pass — Metabase allows dashboards "
                "and questions to live outside any tracked collection "
                "(``collection_id = null``) and those survive the filter. "
                "nativeQueryPermissionCheck is unaffected (it inspects "
                "databases, not collections)."
            ),
        ),
        Scenario(
            name="preflight_include_and_exclude_overlap",
            api="preflight",
            metadata={
                # Include root but also exclude it — exclude wins.
                "include-collections": '{"root": {}}',
                "exclude-collections": '{"root": {}}',
            },
            assert_that={
                "success": equals(True),
                "data.collectionCountCheck.success": equals(True),
                "data.collectionCountCheck.message": equals("Total collections: 0"),
            },
            description=(
                "When the same id appears in both filters, exclude wins. "
                "Documents the v3 precedence (matches v2 marketplace logic)."
            ),
        ),
        # -- preflight error handling ------------------------------------------
        Scenario(
            name="preflight_short_circuits_on_bad_host",
            api="preflight",
            credentials=_basic_creds(host="https://nonexistent.example.invalid"),
            assert_that={
                # The HTTP envelope's outer ``success`` reflects whether the
                # preflight request was processed, NOT whether the checks
                # passed. Outer is True; the inner check signals failure.
                "success": equals(True),
                "data.collectionCountCheck.success": equals(False),
                "data.collectionCountCheck.message": is_not_empty(),
            },
            description=(
                "Unreachable host fails the collection check; the handler "
                "short-circuits so dashboardCountCheck / questionCountCheck / "
                "nativeQueryPermissionCheck are absent from the response. "
                "The outer envelope ``success`` stays True (the request was "
                "served); only the inner check signals failure."
            ),
        ),
        # -- metadata edge cases -----------------------------------------------
        Scenario(
            name="metadata_filters_out_personal_collections",
            api="metadata",
            assert_that={
                "success": equals(True),
                # Every node returned must have a node_type of 'collection' —
                # personal collections (those whose owner is not None) are
                # filtered out by the handler before the dropdown is built.
                "data.0.node_type": equals("collection"),
                "data.1.node_type": equals("collection"),
            },
            description=(
                "Handler skips personal collections (personal_owner_id != "
                "null) so the dropdown only shows shared collections."
            ),
        ),
        # -- auth edge cases ---------------------------------------------------
        Scenario(
            name="auth_with_empty_credentials",
            api="auth",
            credentials={},
            assert_that={
                "success": equals(False),
                "data.status": equals("failed"),
            },
            description=(
                "Empty credentials short-circuit auth — handler catches the "
                "missing-host / missing-username path before any HTTP round-trip."
            ),
        ),
        Scenario(
            name="auth_with_unreachable_host",
            api="auth",
            credentials=_basic_creds(host="https://nonexistent.example.invalid"),
            assert_that={
                "success": equals(False),
                "data.status": equals("failed"),
                # Error message surfaces the upstream failure for debugging.
                "data.message": is_not_empty(),
            },
            description=(
                "Wrong host fails auth at the /api/session network layer. "
                "Verifies the handler surfaces a debuggable error message "
                "rather than a generic 'failed'."
            ),
        ),
        # -- workflow scenarios ------------------------------------------------
        # Credentials are resolved via ``metabase_credential.name`` →
        # MockSecretStore (seeded by main.py from E2E_METABASE_* env vars
        # under the key ``metabase-default``). NOT inline — the SDK's
        # /workflows/v1/start handler strips ``credentials`` from the body
        # before workflow dispatch, so inline creds can't reach the tasks.
        # ``scenario.args`` is a full override that bypasses the runner's
        # default credential-merging logic; we set exactly the fields the
        # workflow @entrypoint reads.
        Scenario(
            name="workflow_extract_metadata_start",
            api="workflow",
            endpoint="/start?entrypoint=extract-metadata",
            args={
                "metabase_credential": {
                    "name": "metabase-default",
                    "credential_type": "basic",
                },
                "metadata": {
                    "include-collections": {},
                    "exclude-collections": {},
                },
                "connection": {
                    "connection": "default/metabase/test_integration",
                },
            },
            assert_that={
                "success": equals(True),
                "data.workflow_id": is_not_empty(),
                "data.run_id": is_not_empty(),
            },
            description=(
                "extract_metadata @entrypoint accepts a CredentialRef "
                "(metabase_credential.name) and dispatches to Temporal. The "
                "9 extract @tasks each resolve credentials via "
                "self.context.resolve_credential_raw(cred_ref) against the "
                "MockSecretStore-backed secret store. Validates the v3 "
                "credential pipeline end-to-end."
            ),
            workflow_timeout=60,
            polling_interval=5,
        ),
        Scenario(
            name="workflow_transform_metadata_start",
            api="workflow",
            endpoint="/start?entrypoint=transform-metadata",
            args={
                "metabase_credential": {
                    "name": "metabase-default",
                    "credential_type": "basic",
                },
                "metadata": {},
                "connection": {
                    "connection": "default/metabase/test_integration",
                },
            },
            assert_that={
                "success": equals(True),
                "data.workflow_id": is_not_empty(),
                "data.run_id": is_not_empty(),
            },
            description=(
                "transform_metadata @entrypoint accepts the same CredentialRef "
                "shape and dispatches the transform pipeline. Together with "
                "the extract scenario, validates that both v2 workflows are "
                "reachable as v3 @entrypoint methods on the same App class."
            ),
            workflow_timeout=60,
            polling_interval=5,
        ),
    ]
