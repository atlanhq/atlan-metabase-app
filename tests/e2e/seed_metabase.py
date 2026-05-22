"""Idempotent seed script for the Metabase e2e Docker pipeline.

Workflow:
  1. Wait for the Metabase server to report ``{"status": "ok"}``.
  2. Fetch ``setup_token`` via ``GET /api/session/properties``.
  3. Create the admin user via ``POST /api/setup``. (Idempotent: if the
     instance is already set up, this returns 403 and we authenticate
     normally.)
  4. Register the ``mb-source`` postgres as a Metabase database, then
     trigger a metadata sync and wait for it to complete.
  5. Seed a known mix of collections (root, nested, personal),
     questions (native SQL against the source, MBQL, invalid), and
     dashboards (with cards drawn from the questions).

The seeded state is fully described in ``fixtures/seed_metabase_spec.yaml``
so test assertions can reason about expected counts and lineage edges.

Environment:
  MB_URL                 — Metabase base URL (default http://localhost:3000)
  MB_ADMIN_EMAIL         — admin user to create
  MB_ADMIN_PASSWORD      — admin password
  MB_SOURCE_HOST/PORT/USER/PASSWORD/DB — source postgres connection
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

MB_URL = os.environ.get("MB_URL", "http://localhost:3000").rstrip("/")
MB_ADMIN_EMAIL = os.environ["MB_ADMIN_EMAIL"]
MB_ADMIN_PASSWORD = os.environ["MB_ADMIN_PASSWORD"]
MB_SOURCE_HOST = os.environ.get("MB_SOURCE_HOST", "mb-source")
MB_SOURCE_PORT = int(os.environ.get("MB_SOURCE_PORT", "5432"))
MB_SOURCE_USER = os.environ.get("MB_SOURCE_USER", "source")
MB_SOURCE_PASSWORD = os.environ.get("MB_SOURCE_PASSWORD", "source")
MB_SOURCE_DB = os.environ.get("MB_SOURCE_DB", "testdata")

HEALTH_TIMEOUT_S = 180
SYNC_TIMEOUT_S = 120
SPEC_PATH = Path(__file__).parent / "fixtures" / "seed_metabase_spec.yaml"


def _log(msg: str) -> None:
    print(f"[seed_metabase] {msg}", flush=True)


def wait_for_health(client: httpx.Client) -> None:
    start = time.time()
    while time.time() - start < HEALTH_TIMEOUT_S:
        try:
            r = client.get(f"{MB_URL}/api/health", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                _log("metabase health: ok")
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise RuntimeError(f"Metabase did not become healthy within {HEALTH_TIMEOUT_S}s")


def setup_admin_or_login(client: httpx.Client) -> str:
    """Create the admin user on a fresh instance OR log in if already set up.

    Returns the session id (``X-Metabase-Session`` value).
    """
    props = client.get(f"{MB_URL}/api/session/properties").json()
    setup_token = props.get("setup-token")

    if setup_token:
        _log("setup_token present — creating admin user")
        body = {
            "token": setup_token,
            "user": {
                "first_name": "E2E",
                "last_name": "Admin",
                "email": MB_ADMIN_EMAIL,
                "password": MB_ADMIN_PASSWORD,
                "site_name": "E2E",
            },
            "prefs": {
                "site_name": "E2E",
                "site_locale": "en",
                "allow_tracking": False,
            },
            "database": None,
        }
        r = client.post(f"{MB_URL}/api/setup", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"setup failed: {r.status_code} {r.text}")
        return r.json()["id"]

    _log("instance already set up — logging in")
    r = client.post(
        f"{MB_URL}/api/session",
        json={"username": MB_ADMIN_EMAIL, "password": MB_ADMIN_PASSWORD},
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed: {r.status_code} {r.text}")
    return r.json()["id"]


def add_source_database(client: httpx.Client, session_id: str) -> int:
    """Register the source postgres with Metabase. Returns the database id."""
    headers = {"X-Metabase-Session": session_id}

    # Idempotent: if a db with our engine+host already exists, reuse it.
    existing = client.get(f"{MB_URL}/api/database", headers=headers).json()
    candidates = (
        existing.get("data", existing) if isinstance(existing, dict) else existing
    )
    for db in candidates or []:
        details = db.get("details") or {}
        if (
            db.get("engine") == "postgres"
            and details.get("host") == MB_SOURCE_HOST
            and str(details.get("port")) == str(MB_SOURCE_PORT)
            and details.get("dbname") == MB_SOURCE_DB
        ):
            _log(f"source postgres already registered as id={db['id']}")
            return int(db["id"])

    body = {
        "name": "e2e-source",
        "engine": "postgres",
        "details": {
            "host": MB_SOURCE_HOST,
            "port": MB_SOURCE_PORT,
            "dbname": MB_SOURCE_DB,
            "user": MB_SOURCE_USER,
            "password": MB_SOURCE_PASSWORD,
            "ssl": False,
            "tunnel-enabled": False,
        },
        "is_full_sync": True,
        "is_on_demand": False,
    }
    r = client.post(f"{MB_URL}/api/database", headers=headers, json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"create database failed: {r.status_code} {r.text}")
    db_id = int(r.json()["id"])
    _log(f"created source postgres as database id={db_id}")
    return db_id


def wait_for_sync(client: httpx.Client, session_id: str, db_id: int) -> None:
    """Trigger sync_schema and wait for at least one table to be visible."""
    headers = {"X-Metabase-Session": session_id}
    client.post(f"{MB_URL}/api/database/{db_id}/sync_schema", headers=headers)
    _log("waiting for metadata sync to expose tables")
    start = time.time()
    while time.time() - start < SYNC_TIMEOUT_S:
        meta = client.get(
            f"{MB_URL}/api/database/{db_id}/metadata", headers=headers
        ).json()
        tables = meta.get("tables") or []
        if any(t.get("schema") == "analytics" for t in tables):
            _log(f"sync complete: {len(tables)} tables visible")
            return
        time.sleep(3)
    raise RuntimeError(f"sync did not complete within {SYNC_TIMEOUT_S}s")


def seed_assets(client: httpx.Client, session_id: str, db_id: int) -> None:
    """Apply the declarative seed spec to Metabase."""
    headers = {"X-Metabase-Session": session_id}
    spec = yaml.safe_load(SPEC_PATH.read_text())

    # ---- Collections (root + nested + personal not applicable; admin user
    # already owns a personal collection by default which we'll verify
    # exists but won't touch) ----
    name_to_id: dict[str, int] = {}
    for c in spec.get("collections", []):
        # Skip if already exists by name
        existing = client.get(f"{MB_URL}/api/collection", headers=headers).json()
        match = next((e for e in existing if e.get("name") == c["name"]), None)
        if match:
            name_to_id[c["name"]] = int(match["id"])
            continue
        body = {"name": c["name"], "color": c.get("color", "#509EE3")}
        if c.get("parent"):
            body["parent_id"] = name_to_id[c["parent"]]
        r = client.post(f"{MB_URL}/api/collection", headers=headers, json=body)
        r.raise_for_status()
        name_to_id[c["name"]] = int(r.json()["id"])
        _log(f"collection created: {c['name']} (id={name_to_id[c['name']]})")

    # ---- Questions ----
    question_name_to_id: dict[str, int] = {}
    for q in spec.get("questions", []):
        existing = client.get(f"{MB_URL}/api/card", headers=headers).json()
        match = next((e for e in existing if e.get("name") == q["name"]), None)
        if match:
            question_name_to_id[q["name"]] = int(match["id"])
            continue
        body: dict[str, Any] = {
            "name": q["name"],
            "collection_id": name_to_id.get(q.get("collection", "")) or None,
            "display": "table",
            "visualization_settings": {},
        }
        if q["type"] == "native":
            body["dataset_query"] = {
                "type": "native",
                "database": db_id,
                "native": {"query": q["sql"]},
            }
        elif q["type"] == "mbql":
            # Minimal MBQL — get a source table by name via metadata cache.
            meta = client.get(
                f"{MB_URL}/api/database/{db_id}/metadata", headers=headers
            ).json()
            table = next(
                (t for t in meta.get("tables", []) if t.get("name") == q["table"]),
                None,
            )
            if table is None:
                raise RuntimeError(
                    f"MBQL question references unknown table {q['table']}"
                )
            body["dataset_query"] = {
                "type": "query",
                "database": db_id,
                "query": {"source-table": table["id"]},
            }
        else:
            raise ValueError(f"unknown question type {q['type']}")
        r = client.post(f"{MB_URL}/api/card", headers=headers, json=body)
        r.raise_for_status()
        question_name_to_id[q["name"]] = int(r.json()["id"])
        _log(f"question created: {q['name']} (id={question_name_to_id[q['name']]})")

    # ---- Dashboards + cards ----
    for d in spec.get("dashboards", []):
        existing = client.get(f"{MB_URL}/api/dashboard", headers=headers).json()
        match = next((e for e in existing if e.get("name") == d["name"]), None)
        if match:
            dash_id = int(match["id"])
        else:
            body = {
                "name": d["name"],
                "collection_id": name_to_id.get(d.get("collection", "")) or None,
            }
            r = client.post(f"{MB_URL}/api/dashboard", headers=headers, json=body)
            r.raise_for_status()
            dash_id = int(r.json()["id"])
            _log(f"dashboard created: {d['name']} (id={dash_id})")

        for card_name in d.get("cards", []):
            card_id = question_name_to_id.get(card_name)
            if card_id is None:
                _log(f"  skip card {card_name}: not in spec")
                continue
            # Idempotency check: skip if dashboard already has this card.
            cur = client.get(
                f"{MB_URL}/api/dashboard/{dash_id}", headers=headers
            ).json()
            if any(
                c.get("card_id") == card_id
                for c in cur.get("dashcards", []) or cur.get("ordered_cards", [])
            ):
                continue
            r = client.post(
                f"{MB_URL}/api/dashboard/{dash_id}/cards",
                headers=headers,
                json={"cardId": card_id, "row": 0, "col": 0, "size_x": 4, "size_y": 4},
            )
            if r.status_code not in (200, 201):
                _log(f"  add card {card_name} failed: {r.status_code} {r.text}")


def main() -> int:
    with httpx.Client(timeout=30) as client:
        wait_for_health(client)
        session_id = setup_admin_or_login(client)
        db_id = add_source_database(client, session_id)
        wait_for_sync(client, session_id, db_id)
        seed_assets(client, session_id, db_id)
        _log("seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
