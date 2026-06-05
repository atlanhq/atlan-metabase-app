"""Write the Metabase connector e2e secrets bundle.

The e2e-full pipeline brings up Metabase inside the compose network (see
``.github/e2e/e2e-full-docker-compose.yaml``); this script writes the
credentials.json bundle the worker reads when AE dispatches the workflow.

Format follows atlan-mysql-app: flat single-key entries the SDK fetches
individually from the Dapr secret store. The test's ``_build_ae_payload``
emits ``agent-json.basic.username = "SDR_METABASE_USERNAME"`` (etc.) and
the CI worker resolves those keys against the local.file Dapr secret
store, which reads this JSON.
"""

from __future__ import annotations

import json
import os

# Inside the compose network the worker reaches the Metabase service via its
# service name (``metabase``) and internal port. Override via env when the
# compose layout changes. Must match the bootstrap user POSTed by
# tests/e2e/seed_metabase.py via /api/setup.
out = {
    "SDR_METABASE_HOST": os.environ.get("MB_E2E_HOST", "http://metabase"),
    "SDR_METABASE_PORT": os.environ.get("MB_E2E_PORT", "3000"),
    "SDR_METABASE_USERNAME": os.environ.get("MB_E2E_USERNAME", "e2e@atlan.com"),
    "SDR_METABASE_PASSWORD": os.environ.get("MB_E2E_PASSWORD", "AtlanMetabaseE2E!1"),
}

# SDR_CONFIG_DIR is set by the SDK's sdr-e2e composite action to whichever
# config dir it resolved (.github/sdr-e2e or .github/e2e). Write there so
# the post-script existence check passes regardless of which dir was picked.
sdr_config_dir = os.environ.get("SDR_CONFIG_DIR", ".github/e2e")
secrets_dir = os.path.join(sdr_config_dir, "secrets")
os.makedirs(secrets_dir, exist_ok=True)
out_path = os.path.join(secrets_dir, "credentials.json")
with open(out_path, "w") as f:
    json.dump(out, f)
print(f"Secrets bundle written: {out_path}")
