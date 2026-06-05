# Companion to contract/app.pkl. Hand-written boilerplate the toolkit does
# not yet emit for REST-style connectors; kept under app/generated/ to
# mirror atlan-mysql-app/app/generated/_e2e_credential.py.
from pydantic import Field

from application_sdk.testing.e2e.payload import CredentialBody


class MetabaseCredentialBody(CredentialBody):
    """AE credential body for the Metabase connector.

    In AGENT mode the body is lightweight — no host/username/password.
    Those live in the agent's Dapr secret store and are resolved at
    runtime via agent-json ref keys (SDR_METABASE_HOST,
    SDR_METABASE_USERNAME, SDR_METABASE_PASSWORD), exactly the way
    atlan-mysql-app wires its credentials. Sending the DIRECT-mode
    shape from AGENT-mode tests causes the orchestrator to skip
    credential creation and leave {{credentialGuid}} unsubstituted,
    which is what produced the empty credential_guid in the previous
    metabase e2e submit.
    """

    name: str = Field(alias="name")
    auth_type: str = Field(default="basic", alias="authType")
    connector_config_name: str = Field(
        default="atlan-connectors-metabase", alias="connectorConfigName"
    )
    extra: dict = Field(default_factory=dict, alias="extra")
