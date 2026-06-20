# Companion to contract/app.pkl. Hand-written boilerplate the toolkit does
# not yet emit; kept under app/generated/ to mirror the convention used by
# atlan-openapi-app/app/generated/_e2e_base.py.
from pyatlan.model.enums import AtlanConnectorType

from application_sdk.testing.e2e import BaseE2ETest  # type: ignore[attr-defined]


class MetabaseGeneratedE2EBase(BaseE2ETest):
    connector_short_name = "metabase"
    # Metabase connections live under default/metabase/ in the Atlan catalog.
    connection_type = AtlanConnectorType.METABASE.value
    # Atlas connection category for BI-type connectors.
    connection_category = "BI"
    argo_package_name = "@atlan/metabase"
    argo_template_name = "atlan-metabase"
    app_service_url = "http://metabase.metabase-app.svc.cluster.local"
