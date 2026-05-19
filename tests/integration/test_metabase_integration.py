"""Integration tests for the Metabase connector.

Templated from ``atlan-mssql-app/tests/integration/test_mssql_integration.py``
and reduced to a compile-ready stub.

TODO(validation-suite): replace the empty ``scenarios`` list with real
auth / preflight / workflow scenarios for Metabase. The MSSQL gold
reference has ~950 lines of T-SQL-specific golden-file plumbing
(Azure AD + NTLM scenarios, ``extra.database`` flattening, SQL fixture
paths) that does not port directly to REST/BI. Suggested approach:

1. Run ``/write-integration-tests`` from the application-sdk repo with
   this target — it generates a clean ``BaseIntegrationTest`` subclass
   tailored to a single-auth-type connector.
2. Add per-scenario Pandera schemas under
   ``tests/integration/schema/<scenario>/`` describing the expected raw
   + transformed JSONL shape. The four placeholder dirs already in this
   repo (``empty_exclude``, ``empty_filters``, ``empty_include``,
   ``mixed_filters``) reference MSSQL entity names and need updating to
   Metabase entity names (Collection / Dashboard / Question / column).
3. Wire up REST-API mocks (or a long-lived test Metabase instance) — the
   MSSQL version relies on a live SQL Server fixture which has no
   analogue here.

Prerequisites once the scenarios are written
--------------------------------------------
1. Env vars (in ``.env`` at the repo root and/or ``tests/.env``)::

       ATLAN_APPLICATION_NAME=metabase
       E2E_METABASE_HOST=https://acme.metabaseapp.com
       E2E_METABASE_PORT=443
       E2E_METABASE_USERNAME=...
       E2E_METABASE_PASSWORD=...

2. Services + app server running::

       uv run poe start-deps            # Dapr + Temporal
       uv run python main.py            # App server on :8000

3. Run the tests::

       uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from application_sdk.testing.integration import BaseIntegrationTest, Scenario


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


class TestMetabaseIntegration(BaseIntegrationTest):
    """Metabase integration suite — auth / preflight / workflow.

    TODO(validation-suite): populate ``scenarios`` once auth + preflight
    + workflow shapes are confirmed against the v3 connector.
    """

    app_url = "http://localhost:8000"
    app_name = "metabase"

    default_connection: Dict[str, Any] = {
        "connection_qualified_name": "default/metabase/test_integration",
        "connection_name": "test_metabase",
    }

    default_credentials: Dict[str, Any] = _basic_creds()

    # Empty until per-scenario tests are written. BaseIntegrationTest tolerates
    # an empty list — pytest collects no tests and the suite is a no-op.
    scenarios: List[Scenario] = []
