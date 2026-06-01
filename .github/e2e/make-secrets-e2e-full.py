"""Write the Metabase connector e2e secrets bundle.

The e2e-full pipeline brings up Metabase inside the compose network (see
``.github/e2e/e2e-full-docker-compose.yaml``); this script writes the
credentials.json bundle the worker reads when AE dispatches the workflow.

The bundle key (``atlan-connectors-metabase``) matches the credential
configmap name emitted by the toolkit (see
``app/generated/atlan-connectors-metabase.json``). The connector's
``parse_metabase_credentials`` accepts the flat shape produced here
(``host`` / ``port`` / ``username`` / ``password``).
"""

import json
import os
from pathlib import Path

# Inside the compose network the worker reaches the Metabase service via its
# service name (``metabase``) and internal port. Override via env when the
# compose layout changes.
host = os.environ.get("MB_E2E_HOST", "http://metabase")
port = os.environ.get("MB_E2E_PORT", "3000")
username = os.environ.get("MB_E2E_USERNAME", "e2e@atlan.com")
password = os.environ.get("MB_E2E_PASSWORD", "AtlanMetabaseE2E!1")

bundle = {
    "atlan-connectors-metabase": json.dumps(
        {
            "host": host,
            "port": int(port),
            "username": username,
            "password": password,
        }
    ),
}

# SDR_CONFIG_DIR is set by the SDK's sdr-e2e composite action to whichever
# config dir it resolved (.github/sdr-e2e or .github/e2e). Write there so
# the post-script existence check passes regardless of which dir was picked.
sdr_config_dir = os.environ.get("SDR_CONFIG_DIR", ".github/e2e")
out = Path(sdr_config_dir) / "secrets" / "credentials.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(bundle))
print(f"Secrets bundle written: {out}")
