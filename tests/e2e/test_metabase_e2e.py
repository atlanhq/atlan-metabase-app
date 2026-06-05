"""Full-DAG e2e test for the Metabase connector.

Submits a real AE workflow to a test tenant, runs
extract → qi → publish → extract-lineage → lineage-publish, and asserts
the resulting MetabaseCollection / MetabaseDashboard / MetabaseQuestion /
BIProcess assets land in Atlas, plus that lineage Process / ColumnProcess
records are present.

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips
the test when those env vars are absent, so it never runs accidentally
in local or CI unit-test invocations. In CI, enabled by the ``e2e`` PR label.

The source-side Metabase is the docker-compose Metabase brought up by the
e2e-full pipeline's compose overlay (`.github/e2e/e2e-full-docker-compose.yaml`);
the credential bundle wired into the connection points at it.
"""

from __future__ import annotations

import os

import pytest

if not os.environ.get("ATLAN_BASE_URL") or not os.environ.get("ATLAN_API_KEY"):
    pytest.skip(
        "e2e harness needs ATLAN_BASE_URL + ATLAN_API_KEY",
        allow_module_level=True,
    )

# Guard SDK version — BaseE2ETest landed after SDK 3.13.4. When the installed
# SDK is older the test is cleanly skipped rather than erroring. The
# type-ignore comments silence pyright on stale local pins; the runtime
# import-guard handles correctness.
# pyright: reportAttributeAccessIssue=false, reportMissingImports=false
try:
    from application_sdk.testing.e2e import RunMode  # noqa: E402
    from application_sdk.testing.e2e.payload import AgentSpec  # noqa: E402

    from app.generated._e2e_base import MetabaseGeneratedE2EBase  # noqa: E402
    from app.generated._e2e_substitutions import (  # noqa: E402
        MetabaseMustacheSubstitutions,
    )
except ImportError as _exc:
    pytest.skip(
        f"SDK does not yet export agnostic e2e harness: {_exc}",
        allow_module_level=True,
    )


@pytest.mark.e2e
class TestMetabaseE2E(MetabaseGeneratedE2EBase):
    """Full-DAG e2e against a real tenant + docker-compose Metabase.

    Name-derived attrs (``connector_short_name``, ``connection_type``,
    ``argo_package_name``, ``argo_template_name``, ``app_service_url``) come
    from :class:`MetabaseGeneratedE2EBase`. The base harness builds the
    connection QN as ``default/metabase/{epoch}`` automatically.
    """

    mode = RunMode.AGENT

    # Minimum counts produced by ``tests/e2e/seed_metabase.py`` (light spec:
    # 4 collections + 5 questions + 3 dashboards, with BIProcess derived
    # from dashboard→card pairings). Floors are set just below the seed
    # so personal/sample-DB collections don't have to be accounted for
    # in each assertion.
    expected_min_asset_counts = {
        "MetabaseCollection": 2,
        "MetabaseDashboard": 2,
        "MetabaseQuestion": 4,
        "BIProcess": 2,
    }
    # Lineage is produced by the qi → extract-lineage → lineage-publish
    # branch of the DAG; QI parses native-SQL questions against the seeded
    # ``analytics`` / ``reports`` schemas in mb-source.
    expect_lineage = True

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900

    def agent_spec(self) -> AgentSpec:
        return AgentSpec(agent_name=f"metabase-e2e-full-ci-{self.run_id}")

    def _mustache_substitutions(self) -> MetabaseMustacheSubstitutions:
        # Round-trip through the alias-keyed dict instead of constructing
        # by field name — SDK 3.14's MustacheSubstitutions declares
        # `connection` / `credential` with mustache-literal aliases
        # (`{{connection}}`, `{{credential}}`) that pyright's pydantic
        # synthesis treats as the only accepted kwargs, even though
        # `populate_by_name=True`. Connector-specific fields fall back
        # to their defaults — include every non-personal collection,
        # no excludes, extraction_method "direct".
        base = super()._mustache_substitutions()
        return MetabaseMustacheSubstitutions.model_validate(
            base.model_dump(by_alias=True)
        )
