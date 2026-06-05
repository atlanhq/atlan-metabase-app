# Companion to contract/app.pkl. Hand-written boilerplate the toolkit does
# not yet emit; kept under app/generated/ to mirror the convention used by
# atlan-openapi-app/app/generated/_e2e_substitutions.py.
from pydantic import Field

from application_sdk.testing.e2e.substitutions import MustacheSubstitutions


class MetabaseMustacheSubstitutions(MustacheSubstitutions):
    """Mustache substitutions for the Metabase ``extract_metadata`` workflow.

    Aliased to the placeholder strings the generated manifest.json carries
    (see ``app/generated/manifest.json``). The base ``MustacheSubstitutions``
    contributes the connection / credential / workflow-id substitutions; the
    fields here cover the connector-specific extraction options.

    ``extraction_method`` and ``agent_json`` are the two mustache params Heracles'
    native-flow-engine reads to decide whether to route the workflow via the
    SDR/agent path (queue ``atlan-metabase-<agent-name>``) or the static prod
    path (queue ``atlan-metabase-production``). When AE submits with
    ``extraction-method == "agent"`` and a populated ``agent-json`` JSON blob,
    the engine derives the queue from ``sprig.fromJson(agent-json)['agent-name']``;
    otherwise it defaults to ``atlan-metabase-production``. The base
    ``MustacheSubstitutions`` ships the same two fields aliased to
    ``{{extraction-method}}`` / ``{{agent-json}}`` — we re-declare them here
    purely to keep all metabase substitutions co-located + give Heracles' field
    sniffer the explicit ``agent`` enum value via override at the test layer
    (``_mustache_substitutions()`` flips it to ``self.mode.value`` for AGENT
    mode runs). Mirrors how ``SQLAppE2ETest._mustache_substitutions()`` wires
    these for atlan-mysql-app.
    """

    extraction_method: str = Field(default="direct", alias="{{extraction-method}}")
    agent_json: dict | None = Field(default=None, alias="{{agent-json}}")
    include_collections: dict = Field(default_factory=dict, alias="{{include-collections}}")
    exclude_collections: dict = Field(default_factory=dict, alias="{{exclude-collections}}")
    preflight_check: dict = Field(default_factory=dict, alias="{{preflight-check}}")
