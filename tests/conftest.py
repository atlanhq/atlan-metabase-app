"""Root test configuration — env vars the SDK snapshots on first import.

``application_sdk.constants`` reads a handful of environment variables into
module-level constants the first time the package is imported, and
``application_sdk.testing.integration.fixtures`` refuses to load if it finds
those constants disagreeing with the live environment
(``IntegrationEnvOrderingError``). Setting them inside
``tests/integration/conftest.py`` is early enough only when that directory is
collected on its own, which is how CI runs it — one job per suite.

A whole-tree run is a different order: pytest loads this root conftest first,
then walks ``tests/e2e``, ``tests/integration`` and ``tests/unit`` in turn, so
by the time the integration conftest sets the variables the SDK has already
been imported by an earlier module and the ordering guard fires. That made
``pytest tests`` fail collection outright.

That is not only a local-convenience problem. The conformance suite's
``detect --with-tests`` runs the app's whole ``tests/`` tree in a bounded
subprocess to grade the registered preflight scenarios (see
``tests/unit/test_preflight_conformance.py``), and a collection error there is
graded as "no complete conformance result", which fails F016 and leaves F019
uncleared no matter how many scenarios pass.

Setting them here fixes the order for every entry point at once. The
per-directory ``setdefault`` calls stay where they are: they are a no-op once
this file has run, and they keep each suite's requirement stated where its
reader is looking.
"""

from __future__ import annotations

import os

# Identity the SDK stamps on logs, metrics and object-store prefixes. "metabase"
# is what the integration and e2e suites already run under; unit tests
# previously inherited the SDK default, which nothing asserts on.
os.environ.setdefault("ATLAN_APPLICATION_NAME", "metabase")
os.environ.setdefault("ATLAN_DEPLOYMENT_NAME", "ci")

# No test run may attempt an object-store metric flush. The SDK's observability
# store sink resolves a Dapr ``objectstore`` component that no test environment
# provides; its retry warning is emitted from a background thread after pytest
# has closed its streams and surfaces as a "Logging error in Loguru Handler"
# traceback that reads like a test failure.
os.environ.setdefault("ATLAN_ENABLE_OBSERVABILITY_STORE_SINK", "false")
