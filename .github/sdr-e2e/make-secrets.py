"""Write the SDR test secrets bundle for the Metabase connector.

Reads ``E2E_METABASE_BASIC_USERNAME`` and ``E2E_METABASE_BASIC_PASSWORD``
from env (set as job-level env vars in the calling workflow) and writes
them under the secret-store key ``metabase-credentials`` to
``.github/sdr-e2e/secrets/credentials.json`` — the canonical SDR test
secrets path the SDK secretstore component reads.

The bundle is JSON-encoded as a string because the Dapr ``local.file``
secret store with ``nestedSeparator=":"`` expects nested-bundle-as-string
on lookup. ``CredentialRef.resolve(input)`` inside the connector will
read the ``metabase-credentials`` key, JSON-decode the bundle, and feed
``username`` / ``password`` into ``_build_client(input)``.
"""

from __future__ import annotations

import json
import os

bundle = {
    "username": os.environ["E2E_METABASE_BASIC_USERNAME"],
    "password": os.environ["E2E_METABASE_BASIC_PASSWORD"],
}
out = {"metabase-credentials": json.dumps(bundle)}

os.makedirs(".github/sdr-e2e/secrets", exist_ok=True)
with open(".github/sdr-e2e/secrets/credentials.json", "w") as f:
    json.dump(out, f)
print("Wrote .github/sdr-e2e/secrets/credentials.json")
