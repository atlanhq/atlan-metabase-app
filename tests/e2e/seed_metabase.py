"""Idempotent Metabase seeder.

Two scale modes selected by ``E2E_SCALE``:

  small (default)         large
  ──────────────────────  ──────────────────────────────────────
  4 collections           50  collections (mix of root, nested, personal)
  5 questions             800 questions (native SQL + MBQL + invalid)
  3 dashboards            150 dashboards (2-8 cards each)
  Total ≈ 12 assets       Total ≈ 1000 assets

Workflow:
  1. Wait for /api/health to return ``{"status": "ok"}``.
  2. POST /api/setup to create the admin user (idempotent — falls back to
     /api/session login if already set up).
  3. POST /api/database to register the ``mb-source`` postgres as a
     Metabase data source; trigger sync; wait for tables to surface.
  4. Apply the declarative + scale-generated seed:
       - collections (parallel POST /api/collection)
       - questions   (parallel POST /api/card with native SQL + MBQL)
       - dashboards  (parallel PUT  /api/dashboard/{id} with dashcards)

Per-phase timing is logged to stdout. Phase headers and asset counts
are emitted so the CI logs read like a build report rather than a
soup of API calls. Errors per asset are logged but do not abort the
seed — partial failures are OK (the suite asserts only on minimums).

Env vars:
  MB_URL                      Metabase base URL (default http://localhost:3000)
  MB_ADMIN_EMAIL              admin email to create or log in as
  MB_ADMIN_PASSWORD           admin password
  MB_SOURCE_HOST/PORT/...     source postgres connection settings
  E2E_SCALE                   small|large (default small)
  E2E_SEED_PARALLELISM        max concurrent API calls (default 8)
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from contextlib import contextmanager
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

SCALE = os.environ.get("E2E_SCALE", "small").lower()
PARALLELISM = int(os.environ.get("E2E_SEED_PARALLELISM", "8"))

HEALTH_TIMEOUT_S = 300  # 5 min — Metabase first-run can be slow on cold containers
SYNC_TIMEOUT_S = 180
SPEC_PATH = Path(__file__).parent / "fixtures" / "seed_metabase_spec.yaml"


def _log(msg: str) -> None:
    print(f"[seed_metabase] {msg}", flush=True)


@contextmanager
def _timed(phase: str):
    _log(f"━━━ {phase} ━━━")
    start = time.monotonic()
    try:
        yield
    finally:
        _log(f"    {phase} done in {time.monotonic() - start:.1f}s")


# ──────────────────────────────────────────────────────────────────────────
# Health + auth
# ──────────────────────────────────────────────────────────────────────────


async def wait_for_health(client: httpx.AsyncClient) -> None:
    start = time.monotonic()
    attempts = 0
    while time.monotonic() - start < HEALTH_TIMEOUT_S:
        attempts += 1
        try:
            r = await client.get(f"{MB_URL}/api/health", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                _log(
                    f"metabase healthy after {attempts} attempts ({time.monotonic() - start:.1f}s)"
                )
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(3)
    raise RuntimeError(
        f"metabase did not become healthy within {HEALTH_TIMEOUT_S}s (attempts={attempts})"
    )


async def setup_admin_or_login(client: httpx.AsyncClient) -> str:
    """Return the session id."""
    props = (await client.get(f"{MB_URL}/api/session/properties")).json()
    setup_token = props.get("setup-token")

    if setup_token:
        _log(f"setup_token present — creating admin user {MB_ADMIN_EMAIL}")
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
        r = await client.post(f"{MB_URL}/api/setup", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"setup failed: {r.status_code} {r.text}")
        return r.json()["id"]

    _log("instance already set up — logging in")
    r = await client.post(
        f"{MB_URL}/api/session",
        json={"username": MB_ADMIN_EMAIL, "password": MB_ADMIN_PASSWORD},
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed: {r.status_code} {r.text}")
    return r.json()["id"]


# ──────────────────────────────────────────────────────────────────────────
# Source-database registration + sync
# ──────────────────────────────────────────────────────────────────────────


async def add_source_database(client: httpx.AsyncClient, session_id: str) -> int:
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{MB_URL}/api/database", headers=headers)).json()
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
            _log(f"source postgres already registered as database id={db['id']}")
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
    r = await client.post(f"{MB_URL}/api/database", headers=headers, json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"create database failed: {r.status_code} {r.text}")
    db_id = int(r.json()["id"])
    _log(f"created source postgres as database id={db_id}")
    return db_id


async def wait_for_sync(client: httpx.AsyncClient, session_id: str, db_id: int) -> None:
    """Trigger sync_schema and wait for >= 8 tables across analytics + reports."""
    headers = {"X-Metabase-Session": session_id}
    await client.post(f"{MB_URL}/api/database/{db_id}/sync_schema", headers=headers)
    _log("waiting for metadata sync to expose tables")
    start = time.monotonic()
    while time.monotonic() - start < SYNC_TIMEOUT_S:
        meta = (
            await client.get(f"{MB_URL}/api/database/{db_id}/metadata", headers=headers)
        ).json()
        tables = meta.get("tables") or []
        schemas = {t.get("schema") for t in tables if t.get("schema")}
        if len(tables) >= 8 and {"analytics", "reports"}.issubset(schemas):
            _log(
                f"sync complete: {len(tables)} tables across {len(schemas)} schemas "
                f"({sorted(schemas)})"
            )
            return
        await asyncio.sleep(3)
    raise RuntimeError(f"sync did not surface expected tables within {SYNC_TIMEOUT_S}s")


# ──────────────────────────────────────────────────────────────────────────
# Asset seeding
# ──────────────────────────────────────────────────────────────────────────


# Native-SQL templates randomized across source tables for the large scale.
_SQL_TEMPLATES: list[str] = [
    "SELECT * FROM analytics.{tbl} LIMIT 100",
    "SELECT customer_id, customer_name, country FROM analytics.customers WHERE country = 'US'",
    "SELECT c.customer_name, SUM(o.order_total) AS total "
    "FROM analytics.customers c JOIN analytics.orders o "
    "ON o.customer_id = c.customer_id GROUP BY c.customer_name",
    "SELECT p.product_name, SUM(oi.quantity) AS qty_sold "
    "FROM analytics.products p JOIN analytics.order_items oi "
    "ON p.product_id = oi.product_id GROUP BY p.product_name",
    "SELECT i.invoice_id, i.amount, p.method "
    "FROM analytics.invoices i LEFT JOIN analytics.payments p "
    "ON p.invoice_id = i.invoice_id",
    "SELECT day, revenue FROM reports.daily_summary ORDER BY day DESC",
    "SELECT month, avg_order_value FROM reports.monthly_summary",
    "SELECT event_type, COUNT(*) FROM analytics.events GROUP BY event_type",
    "SELECT u.email, u.role FROM analytics.users u WHERE u.role IN ('admin', 'buyer')",
    "SELECT o.order_id, o.order_total, c.customer_name "
    "FROM analytics.orders o JOIN analytics.customers c USING (customer_id) "
    "WHERE o.status = 'shipped'",
]

_MBQL_TABLES = [
    "customers",
    "products",
    "orders",
    "order_items",
    "invoices",
    "payments",
    "events",
    "users",
]


async def _post_json(
    client: httpx.AsyncClient, url: str, headers: dict, body: dict
) -> dict | None:
    try:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code in (200, 201):
            return r.json()
        _log(f"    ⚠️  POST {url} → {r.status_code} {r.text[:200]}")
    except httpx.HTTPError as exc:
        _log(f"    ⚠️  POST {url} raised {type(exc).__name__}: {exc}")
    return None


async def seed_collections(
    client: httpx.AsyncClient, session_id: str, declared: list[dict]
) -> dict[str, int]:
    """Apply declared collections + (in large scale) generate 50 total."""
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{MB_URL}/api/collection", headers=headers)).json()
    # Metabase exposes a virtual "root" collection with id="root" — skip it
    # (and any non-numeric id) when building the name → numeric-id lookup.
    name_to_id: dict[str, int] = {}
    for c in existing:
        try:
            name_to_id[c["name"]] = int(c["id"])
        except (TypeError, ValueError):
            continue

    # Declared (always created)
    for c in declared:
        if c["name"] in name_to_id:
            continue
        body: dict[str, Any] = {"name": c["name"], "color": c.get("color", "#509EE3")}
        if c.get("parent"):
            body["parent_id"] = name_to_id.get(c["parent"])
        out = await _post_json(client, f"{MB_URL}/api/collection", headers, body)
        if out is not None:
            name_to_id[c["name"]] = int(out["id"])

    # Scale-generated (large only)
    if SCALE == "large":
        # 10 root + 40 nested. Total declared = 4, so add 46 more.
        target = 50
        sem = asyncio.Semaphore(PARALLELISM)

        async def _create_one(idx: int) -> None:
            async with sem:
                name = f"Auto Collection {idx:03d}"
                if name in name_to_id:
                    return
                body: dict[str, Any] = {"name": name, "color": "#509EE3"}
                # First 10 are root; rest get a random parent from the first 10.
                if idx > 10 and name_to_id:
                    parents = [
                        v
                        for k, v in name_to_id.items()
                        if k.startswith("Auto Collection 0")
                    ]
                    if parents:
                        body["parent_id"] = random.choice(parents)
                out = await _post_json(
                    client, f"{MB_URL}/api/collection", headers, body
                )
                if out is not None:
                    name_to_id[name] = int(out["id"])

        await asyncio.gather(*[_create_one(i) for i in range(1, target + 1)])

    _log(f"  collections total: {len(name_to_id)}")
    return name_to_id


async def seed_questions(
    client: httpx.AsyncClient,
    session_id: str,
    db_id: int,
    declared: list[dict],
    collection_ids: dict[str, int],
) -> dict[str, int]:
    headers = {"X-Metabase-Session": session_id}
    name_to_id: dict[str, int] = {}

    existing = (await client.get(f"{MB_URL}/api/card", headers=headers)).json()
    for e in existing:
        try:
            name_to_id[e["name"]] = int(e["id"])
        except (TypeError, ValueError):
            continue

    # MBQL needs source-table id; fetch metadata once.
    meta = (
        await client.get(f"{MB_URL}/api/database/{db_id}/metadata", headers=headers)
    ).json()
    table_name_to_id = {t["name"]: t["id"] for t in meta.get("tables", [])}

    sem = asyncio.Semaphore(PARALLELISM)

    async def _create(q: dict) -> None:
        async with sem:
            if q["name"] in name_to_id:
                return
            body: dict[str, Any] = {
                "name": q["name"],
                "collection_id": collection_ids.get(q.get("collection", "")) or None,
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
                table_id = table_name_to_id.get(q["table"])
                if table_id is None:
                    _log(
                        f"    skip mbql {q['name']}: table {q['table']} not in metadata"
                    )
                    return
                body["dataset_query"] = {
                    "type": "query",
                    "database": db_id,
                    "query": {"source-table": table_id},
                }
            else:
                return
            out = await _post_json(client, f"{MB_URL}/api/card", headers, body)
            if out is not None:
                name_to_id[q["name"]] = int(out["id"])

    # Declared questions first.
    await asyncio.gather(*[_create(q) for q in declared])

    if SCALE == "large":
        rng = random.Random(42)  # deterministic
        generated: list[dict] = []
        # 400 native SQL
        for i in range(400):
            tpl = rng.choice(_SQL_TEMPLATES)
            tbl = rng.choice(_MBQL_TABLES)
            sql = tpl.replace("{tbl}", tbl)
            collection_choice = (
                rng.choice(list(collection_ids.keys())) if collection_ids else None
            )
            generated.append(
                {
                    "name": f"Auto Native {i:04d}",
                    "type": "native",
                    "collection": collection_choice,
                    "sql": sql,
                }
            )
        # 400 MBQL
        for i in range(400):
            generated.append(
                {
                    "name": f"Auto MBQL {i:04d}",
                    "type": "mbql",
                    "collection": rng.choice(list(collection_ids.keys()))
                    if collection_ids
                    else None,
                    "table": rng.choice(_MBQL_TABLES),
                }
            )
        await asyncio.gather(*[_create(q) for q in generated])

    _log(f"  questions total: {len(name_to_id)}")
    return name_to_id


async def seed_dashboards(
    client: httpx.AsyncClient,
    session_id: str,
    declared: list[dict],
    collection_ids: dict[str, int],
    question_ids: dict[str, int],
) -> None:
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{MB_URL}/api/dashboard", headers=headers)).json()
    name_to_id: dict[str, int] = {}
    for d in existing:
        try:
            name_to_id[d["name"]] = int(d["id"])
        except (TypeError, ValueError):
            continue

    sem = asyncio.Semaphore(PARALLELISM)

    async def _apply(d: dict) -> None:
        async with sem:
            dash_id = name_to_id.get(d["name"])
            if dash_id is None:
                body = {
                    "name": d["name"],
                    "collection_id": collection_ids.get(d.get("collection", ""))
                    or None,
                }
                out = await _post_json(client, f"{MB_URL}/api/dashboard", headers, body)
                if out is None:
                    return
                dash_id = int(out["id"])
                name_to_id[d["name"]] = dash_id

            # Use the v0.49+ PUT /api/dashboard/{id} with dashcards array.
            cur = (
                await client.get(f"{MB_URL}/api/dashboard/{dash_id}", headers=headers)
            ).json()
            existing_cards = cur.get("dashcards") or cur.get("ordered_cards") or []
            existing_card_ids = {
                c.get("card_id") for c in existing_cards if c.get("card_id")
            }
            new_cards = list(existing_cards)
            for card_name in d.get("cards", []):
                cid = question_ids.get(card_name)
                if cid is None or cid in existing_card_ids:
                    continue
                new_cards.append(
                    {
                        "id": -(len(new_cards) + 1),
                        "card_id": cid,
                        "row": len(new_cards) * 4,
                        "col": 0,
                        "size_x": 4,
                        "size_y": 4,
                        "parameter_mappings": [],
                        "visualization_settings": {},
                    }
                )
            if len(new_cards) > len(existing_cards):
                r = await client.put(
                    f"{MB_URL}/api/dashboard/{dash_id}",
                    headers=headers,
                    json={"dashcards": new_cards},
                )
                if r.status_code not in (200, 201):
                    _log(f"    ⚠️  PUT dashboard {d['name']} → {r.status_code}")

    # Declared first.
    await asyncio.gather(*[_apply(d) for d in declared])

    if SCALE == "large":
        rng = random.Random(7)
        question_names = list(question_ids.keys())
        collection_names = list(collection_ids.keys())
        generated: list[dict] = []
        for i in range(150):
            n_cards = rng.randint(2, 8)
            cards = rng.sample(question_names, min(n_cards, len(question_names)))
            generated.append(
                {
                    "name": f"Auto Dashboard {i:03d}",
                    "collection": rng.choice(collection_names)
                    if collection_names
                    else None,
                    "cards": cards,
                }
            )
        await asyncio.gather(*[_apply(d) for d in generated])

    _log(f"  dashboards total: {len(name_to_id)}")


# ──────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────


async def main() -> int:
    _log(f"scale={SCALE} parallelism={PARALLELISM} url={MB_URL}")
    spec = yaml.safe_load(SPEC_PATH.read_text())

    async with httpx.AsyncClient(timeout=60) as client:
        with _timed("phase 1: wait for metabase"):
            await wait_for_health(client)

        with _timed("phase 2: admin setup/login"):
            session = await setup_admin_or_login(client)

        with _timed("phase 3: register source database + sync"):
            db_id = await add_source_database(client, session)
            await wait_for_sync(client, session, db_id)

        with _timed("phase 4: collections"):
            collection_ids = await seed_collections(
                client, session, spec.get("collections", [])
            )

        with _timed("phase 5: questions"):
            question_ids = await seed_questions(
                client, session, db_id, spec.get("questions", []), collection_ids
            )

        with _timed("phase 6: dashboards"):
            await seed_dashboards(
                client,
                session,
                spec.get("dashboards", []),
                collection_ids,
                question_ids,
            )

        # Final tally
        cols = len(
            (
                await client.get(
                    f"{MB_URL}/api/collection",
                    headers={"X-Metabase-Session": session},
                )
            ).json()
        )
        cards = len(
            (
                await client.get(
                    f"{MB_URL}/api/card",
                    headers={"X-Metabase-Session": session},
                )
            ).json()
        )
        dashboards = len(
            (
                await client.get(
                    f"{MB_URL}/api/dashboard",
                    headers={"X-Metabase-Session": session},
                )
            ).json()
        )
        total = cols + cards + dashboards
        _log("━━━ summary ━━━")
        _log(f"    collections : {cols:5d}")
        _log(f"    questions   : {cards:5d}")
        _log(f"    dashboards  : {dashboards:5d}")
        _log("    ─────────────────")
        _log(f"    total       : {total:5d}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
