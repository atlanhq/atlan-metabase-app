"""Pytest fixtures for the Metabase e2e Docker pipeline tests.

Reads the same env vars the GH Actions workflow exports
(``E2E_METABASE_HOST``, ``MB_ADMIN_EMAIL``, etc.) and the same declarative
seed spec the seed script applied, so test assertions can reason about
expected counts and names without re-parsing Metabase state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

_SPEC_PATH = Path(__file__).parent / "fixtures" / "seed_metabase_spec.yaml"


@pytest.fixture(scope="session")
def metabase_url() -> str:
    return os.environ.get(
        "E2E_METABASE_HOST", os.environ.get("MB_URL", "http://localhost:3000")
    ).rstrip("/")


@pytest.fixture(scope="session")
def metabase_admin() -> tuple[str, str]:
    email = os.environ.get(
        "E2E_METABASE_USERNAME", os.environ.get("MB_ADMIN_EMAIL", "")
    )
    password = os.environ.get(
        "E2E_METABASE_PASSWORD", os.environ.get("MB_ADMIN_PASSWORD", "")
    )
    if not (email and password):
        pytest.skip("E2E credentials not set — skipping e2e suite")
    return email, password


@pytest.fixture(scope="session")
def metabase_session(metabase_url: str, metabase_admin: tuple[str, str]) -> str:
    email, password = metabase_admin
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{metabase_url}/api/session",
            json={"username": email, "password": password},
        )
        r.raise_for_status()
        return r.json()["id"]


@pytest.fixture(scope="session")
def seed_spec() -> dict[str, Any]:
    return yaml.safe_load(_SPEC_PATH.read_text())


@pytest.fixture(scope="session")
def mb_get(metabase_url: str, metabase_session: str):
    """Authenticated GET helper that returns parsed JSON."""

    def _get(path: str) -> Any:
        with httpx.Client(timeout=30) as client:
            r = client.get(
                f"{metabase_url}{path}",
                headers={"X-Metabase-Session": metabase_session},
            )
            r.raise_for_status()
            return r.json()

    return _get
