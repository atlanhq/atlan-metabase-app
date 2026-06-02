"""SDR integration tests for the metabase connector.

TODO(owner): expand scenarios after credentials are provisioned.
See atlan-looker-app/tests/sdr/test_looker_sdr.py for a working reference.
"""

from __future__ import annotations

from typing import Any, Dict

from application_sdk.testing.integration import Scenario, equals
from application_sdk.testing.sdr import BaseSDRIntegrationTest


_AGENT_JSON: Dict[str, Any] = {
    "agent-name": "metabase-ci-agent",
    "secret-manager": "local",
    "secret-path": "metabase-credentials",
    "auth-type": "basic",
    # TODO(owner): map agent fields -> bundle keys per handler shape
    "basic.username": "username",
    "basic.password": "password",
}


class TestMetabaseSdr(BaseSDRIntegrationTest):
    timeout: int = 60
    agent_spec_template = _AGENT_JSON
    default_credentials: Dict[str, Any] = {"authType": "basic", "type": "all"}

    scenarios = [
        Scenario(
            name="auth_valid_credentials",
            api="auth",
            assert_that={"success": equals(True)},
        ),
    ]
