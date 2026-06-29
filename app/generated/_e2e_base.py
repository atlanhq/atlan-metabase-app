# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from application_sdk.testing.e2e import BaseE2ETest


class MetabaseGeneratedE2EBase(BaseE2ETest):
    connector_short_name = "metabase"
    argo_package_name = "@atlan/metabase"
    argo_template_name = "atlan-metabase"
    app_service_url = "http://metabase.metabase-app.svc.cluster.local"
    connection_category = "bi"
