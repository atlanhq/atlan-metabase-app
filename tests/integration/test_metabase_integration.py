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
    ]
