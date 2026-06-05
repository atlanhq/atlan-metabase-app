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
    from application_sdk.testing.e2e.payload import (  # noqa: E402
        AgentSpec,
        DatabaseSpec,
        build_ae_payload,
    )

    from app.generated._e2e_base import MetabaseGeneratedE2EBase  # noqa: E402
    from app.generated._e2e_credential import MetabaseCredentialBody  # noqa: E402
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

    def database_spec(self) -> DatabaseSpec:
        # ``host=metabase`` resolves over the compose default network to
        # the sibling Metabase container the e2e-full overlay brings up.
        # username/password match seed_metabase.py's POST /api/setup
        # bootstrap user. ``connector_config_name`` matches the credential
        # configmap name emitted by the toolkit (see
        # ``app/generated/atlan-connectors-metabase.json``).
        return DatabaseSpec(
            host="http://metabase",
            port=3000,
            username="e2e@atlan.com",
            password="AtlanMetabaseE2E!1",
            connector_config_name="atlan-connectors-metabase",
        )

    def _credential_body(self) -> MetabaseCredentialBody:
        # AGENT mode: lightweight body — no host/username/password. Those
        # live in the Dapr secret store and are resolved at runtime via
        # agent-json ref keys. Sending the DIRECT-mode shape causes the
        # orchestrator to skip credential creation and leave
        # {{credentialGuid}} unsubstituted, which produced the empty
        # credential_guid in the previous metabase e2e submit.
        return MetabaseCredentialBody(
            name=f"default-{self.connector_short_name}-{self.run_id}-0",
        )

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

    def _build_ae_payload(self, slug: str) -> dict:
        # SDK 3.14's build_ae_payload emits only the {{...}} mustache params
        # and connection.* attrs. The Argo cluster template additionally reads
        # flat credential-guid.* and agent-json.* params that the SDR worker
        # resolves at runtime — inject them here so the template sees the
        # same shape it expects. Mirrors atlan-mysql-app's _build_ae_payload.
        payload = build_ae_payload(
            run_id=self.run_id,
            mode=self.mode,
            connector_short_name=self.connector_short_name,
            argo_package_name=self.argo_package_name,
            argo_template_name=self.argo_template_name,
            app_service_url=self.app_service_url,
            connection=self.connection_spec(),
            mustache_subs=self._mustache_substitutions(),
            credential_body=self._credential_body(),
            ae_workflow_slug=slug,
        )
        db = self.database_spec()
        agent = self.agent_spec()
        extra_params: list[dict] = [
            {
                "name": "credential-guid.credential-type",
                "value": db.connector_config_name
                or f"atlan-connectors-{self.connector_short_name}",
            },
            {"name": "credential-guid.port", "value": db.port},
            {"name": "credential-guid.auth-type", "value": db.auth_type},
        ]
        if agent is not None:
            extra_params.extend(
                [
                    {"name": "agent-json.host", "value": db.host},
                    {"name": "agent-json.port", "value": db.port},
                    {"name": "agent-json.auth-type", "value": db.auth_type},
                    {"name": "agent-json.agent-name", "value": agent.agent_name},
                    {"name": "agent-json.agent-type", "value": agent.agent_type},
                    {"name": "agent-json.key-type", "value": agent.key_type},
                    {
                        "name": "agent-json.aws-auth-method",
                        "value": agent.aws_auth_method,
                    },
                    {
                        "name": "agent-json.azure-auth-method",
                        "value": agent.azure_auth_method,
                    },
                    {
                        "name": "agent-json.basic.username",
                        "value": f"SDR_{self.connector_short_name.upper()}_USERNAME",
                    },
                    {
                        "name": "agent-json.basic.password",
                        "value": f"SDR_{self.connector_short_name.upper()}_PASSWORD",
                    },
                ]
            )
        payload["spec"]["templates"][0]["dag"]["tasks"][0]["arguments"][
            "parameters"
        ].extend(extra_params)
        return payload
