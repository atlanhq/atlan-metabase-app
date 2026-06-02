"""Write the SDR test secrets bundle for the metabase connector.

TODO(owner): inspect app/handlers.py / app/clients.py to determine the
credential dict shape the handler expects, then update the bundle keys
below. References:
  - basic auth: atlan-looker-app/.github/e2e/make-secrets.py
  - IAM auth:   atlan-glue-app/.github/sdr-e2e/make-secrets.py
"""

from __future__ import annotations

import json
import os

# TODO(owner): replace with the actual credential fields your handler reads
bundle = {
    "username": os.environ.get("E2E_METABASE_USERNAME", ""),
    "password": os.environ.get("E2E_METABASE_PASSWORD", ""),
}
out = {"metabase-credentials": json.dumps(bundle)}

os.makedirs(".github/sdr-e2e/secrets", exist_ok=True)
with open(".github/sdr-e2e/secrets/credentials.json", "w") as f:
    json.dump(out, f)
print("Wrote .github/sdr-e2e/secrets/credentials.json")
