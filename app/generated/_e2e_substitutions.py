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
    """

    extraction_method: str = Field(default="direct", alias="{{extraction-method}}")
    include_collections: dict = Field(default_factory=dict, alias="{{include-collections}}")
    exclude_collections: dict = Field(default_factory=dict, alias="{{exclude-collections}}")
    preflight_check: dict = Field(default_factory=dict, alias="{{preflight-check}}")
