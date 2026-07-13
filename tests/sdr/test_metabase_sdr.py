"""SDR (Self-Deployed Runtime) integration tests for the Metabase connector.

Auth: basic (username / password) against a Metabase server. The agent-mode
credential bundle is resolved through the secret-manager -> secret-path chain;
``parse_metabase_credentials`` (app/credentials.py) reads flat
``username`` / ``password`` / ``host`` / ``port`` keys (the ``extra.`` prefix is
stripped only for the list shape), so the agent field -> bundle key mapping
below is flat.

Workflow scenarios build their input from the committed ``manifest.json`` via
``manifest_path`` — the SAME ``dag.extract.inputs.args`` shape the platform
(Heracles/AE) submits in production — so this suite catches a manifest that
fails to wire a contract field into the workflow input, instead of passing on a
hand-supplied value. Auth / preflight scenarios run against inline
``default_credentials`` (the auth API does not go through the secret-manager
indirection).

This file lives under ``tests/sdr/`` and is run ONLY by the SDK ``sdr-e2e``
composite action (which brings up the SDR container). The normal unit +
integration job excludes it via ``test-paths`` in ``.github/workflows/tests.yaml``
— without a running SDR server ``BaseIntegrationTest.setup_class`` fails hard
rather than skips, so it must not be collected by the serverless run.

TODO(owner): wire ``.github/sdr-e2e/`` (make-secrets.py + component/compose
config) and add an ``api="workflow"`` scenario + ``default_connection`` /
``extracted_output_base_path`` so the readiness floor validates a real
extraction (set ``enforce_workflow_floor = True`` once it exists). Reference:
atlan-tableau-app/tests/sdr/test_tableau_sdr.py.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from application_sdk.testing.integration import Scenario, equals
from application_sdk.testing.sdr import BaseSDRIntegrationTest

# Agent-mode credential bundle mapping: agent-spec field -> handler bundle key.
# Metabase uses basic auth; host/port are carried through so the client can
# reach the server the agent provisions.
_AGENT_JSON: Dict[str, Any] = {
    "agent-name": "metabase-ci-agent",
    "secret-manager": "local",
    "secret-path": "metabase-credentials",
    "auth-type": "basic",
    "basic.username": "username",
    "basic.password": "password",
    "basic.host": "host",
    "basic.port": "port",
}


def _inline_credentials() -> Dict[str, Any]:
    """Inline credentials for auth / preflight scenarios, read from the same env
    vars an ``.github/sdr-e2e/make-secrets.py`` would populate. The auth API does
    not go through the secret-manager indirection, so it needs the payload
    directly. Empty defaults keep collection green until credentials are
    provisioned."""
    return {
        "authType": "basic",
        "username": os.environ.get("METABASE_AUTH_USERNAME", ""),
        "password": os.environ.get("METABASE_AUTH_PASSWORD", ""),
        "host": os.environ.get("METABASE_AUTH_HOST", ""),
        "port": int(os.environ.get("METABASE_AUTH_PORT", "443")),
    }


# conformance: ignore[T013] SDR suites must live under tests/sdr/ — the SDK sdr-e2e composite action targets that path by default; the T013 tier list omitting 'sdr' is a known rule gap (see the rule's own docstring).
class TestMetabaseSdr(BaseSDRIntegrationTest):
    timeout: int = 60

    # Derive workflow-scenario input from the committed manifest (T003-clean:
    # sets manifest_path, not just agent_spec_template).
    manifest_path = "app/generated/manifest.json"

    agent_spec_template = _AGENT_JSON
    default_credentials: Dict[str, Any] = _inline_credentials()

    scenarios = [
        Scenario(
            name="auth_valid_credentials",
            api="auth",
            credentials=_inline_credentials(),
            assert_that={"success": equals(True)},
        ),
    ]
