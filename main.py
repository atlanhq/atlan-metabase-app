"""Local-dev entry point for the Metabase connector.

In production the container is launched by the v3 base image's CLI with
``ATLAN_APP_MODULE=app.connector:MetabaseApp``. For local dev we boot the
SDK's combined runtime (in-process worker + handler + Temporal + Dapr
shims) via :func:`run_dev_combined`.

Credential seeding for workflow scenarios
-----------------------------------------
The SDK's HTTP service strips inline credentials from
``/workflows/v1/start`` request bodies by design — credentials never land
in Temporal history. The runtime resolves credentials inside each
``@task`` via ``self.context.resolve_credential_raw(cred_ref)`` against a
``SecretStore`` keyed by ``CredentialRef.name``.

For local dev we inject a :class:`MockSecretStore` pre-seeded from the
``E2E_METABASE_*`` env vars. Workflow scenarios reference the seed key
via ``metabase_credential={"name": "metabase-default", ...}`` on the
``/start`` request body; ``MetabaseApp.build_credential_ref`` picks up
the typed ref and routes through the named-path resolver
(``secret_store.get(name)``) which the ``MockSecretStore`` answers.

If the env vars aren't set the seed bundle is empty — the workflow still
starts but each ``@task`` will fail at ``_build_client`` with "no
credential_ref or inline_credentials". That's the expected local-dev
signal: populate ``tests/.env``.

In production this code path is not used. The CLI entry point goes
through ``application_sdk.main:main`` → ``run_combined_mode`` which
wires the real Dapr-backed secret store.
"""

from __future__ import annotations

import asyncio
import json
import os

from application_sdk.main import run_dev_combined
from application_sdk.observability.logger_adaptor import get_logger
from application_sdk.testing.mocks import MockSecretStore

from app.connector import MetabaseApp
from app.handler import MetabaseHandler  # noqa: F401 — registers handler with SDK

logger = get_logger(__name__)

# Canonical secret-store key for local-dev workflow tests.
# Reference it via ``metabase_credential={"name": "metabase-default"}`` on
# the ``/workflows/v1/start`` request body.
_LOCAL_DEV_CREDENTIAL_NAME = "metabase-default"


def _seed_credential_bundle() -> dict[str, str]:
    """Read Metabase creds from env and return the bundle for the secret store."""
    host = os.environ.get("E2E_METABASE_HOST", "")
    port = os.environ.get("E2E_METABASE_PORT", "443")
    username = os.environ.get("E2E_METABASE_USERNAME", "")
    password = os.environ.get("E2E_METABASE_PASSWORD", "")
    return {
        "host": host,
        "port": str(port),
        "username": username,
        "password": password,
    }


async def _main() -> None:
    bundle = _seed_credential_bundle()
    secrets = {_LOCAL_DEV_CREDENTIAL_NAME: json.dumps(bundle)}
    credential_stores = {"default": MockSecretStore(secrets)}

    if not (bundle["host"] and bundle["username"] and bundle["password"]):
        logger.warning(
            "main: no Metabase credentials found in env (E2E_METABASE_* or "
            "E2E_METABASE_BASIC_*) — workflow scenarios will fail at "
            "_build_client. Populate tests/.env or pass credentials inline."
        )

    await run_dev_combined(MetabaseApp, credential_stores=credential_stores)


if __name__ == "__main__":
    asyncio.run(_main())
