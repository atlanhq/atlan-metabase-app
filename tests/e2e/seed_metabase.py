"""Lightweight Metabase seeder — config-driven, shared by integration + e2e.

Two entry points:

  * As a script (e2e compose overlay's ``seed-metabase`` one-shot service):
        python tests/e2e/seed_metabase.py
    Reads counts and connection from env vars. Default count profile is
    ``small`` (4 collections / 5 questions / 3 dashboards) so the e2e DAG
    sees enough assets to assert on without an asset volume that inflates
    container boot time.

  * As an importable async function (``tests/integration/conftest.py``):
        from tests.e2e.seed_metabase import seed_metabase
        await seed_metabase(base_url, n_collections=2, n_questions=2,
                            n_dashboards=2, source=None)
    Runs in-process against a session-scoped Metabase testcontainer.

The previous heavy ``E2E_SCALE=large`` generator (~1000 assets) is gone
— integration + e2e both run on the small spec now. Asset volume was
never a useful e2e signal; we only need ≥ a couple of each typename for
the connector to emit records and for QI to parse a few native-SQL
queries into lineage.

Idempotent: a second invocation against an already-seeded Metabase
short-circuits at each phase (admin already exists → login; collection
name exists → skip; etc.).

Env vars when used as a script:
  MB_URL                      Metabase base URL (default http://localhost:3000)
  MB_ADMIN_EMAIL              admin email to create or log in as (required)
  MB_ADMIN_PASSWORD           admin password (required)
  MB_SOURCE_HOST              optional source DB for native-SQL lineage; when
                              set, the seeder registers it as a Metabase data
                              source and routes native questions against it
  MB_SOURCE_ENGINE            source engine type: ``mysql`` (default) or
                              ``postgres``. The Metabase /api/database body
                              is shaped per engine
  MB_SOURCE_PORT/USER/PASSWORD/DB    source connection details
  MB_SEED_COLLECTIONS         override declared collection count (default 4)
  MB_SEED_QUESTIONS           override declared question count    (default 5)
  MB_SEED_DASHBOARDS          override declared dashboard count   (default 3)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import contextmanager
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Declared seed spec — hard-coded, no external YAML.
# ---------------------------------------------------------------------------
# Up to N items of each list are used per phase; setting N below the list
# length is how "integration uses 2/2/2 while e2e uses 4/5/3" works without
# duplicating the data.

_DECLARED_COLLECTIONS: list[dict[str, Any]] = [
    {"name": "E2E Marketing", "color": "#509EE3"},
    {"name": "E2E Finance", "color": "#88BF4D"},
    {"name": "E2E Excluded", "color": "#A989C5"},
    {"name": "E2E Campaigns", "parent": "E2E Marketing", "color": "#EF8C8C"},
]

# Questions reference either ``analytics.<table>`` (native SQL → lineage) or
# the sample database. When MB_SOURCE_HOST is unset, native entries fall back
# to MBQL against the sample DB so the connector still emits records.
_DECLARED_QUESTIONS: list[dict[str, Any]] = [
    {
        "name": "Top Customers by Order Value",
        "type": "native",
        "collection": "E2E Finance",
        "sql": (
            "SELECT c.customer_name, SUM(o.order_total) AS total_value "
            "FROM analytics.customers c "
            "JOIN analytics.orders o ON o.customer_id = c.customer_id "
            "GROUP BY c.customer_name ORDER BY total_value DESC"
        ),
    },
    {
        "name": "Active Campaigns",
        "type": "native",
        "collection": "E2E Marketing",
        "sql": (
            "SELECT campaign_id, name, status FROM analytics.campaigns "
            "WHERE status = 'active'"
        ),
    },
    {
        "name": "Daily Revenue",
        "type": "native",
        "collection": "E2E Finance",
        # All tables live in the ``analytics`` database — mysql databases
        # ARE schemas, and seed_source.sql puts daily_summary alongside
        # customers/orders/campaigns. Keeps the connection-level
        # ``dbname=analytics`` simple (no cross-db GRANT plumbing).
        "sql": "SELECT day, revenue FROM analytics.daily_summary ORDER BY day DESC",
    },
    {
        "name": "All Customers",
        "type": "mbql",
        "collection": "E2E Marketing",
        "table": "customers",
    },
    {
        "name": "All Products",
        "type": "mbql",
        "collection": "E2E Marketing",
        "table": "products",
    },
]

_DECLARED_DASHBOARDS: list[dict[str, Any]] = [
    {
        "name": "Marketing Overview",
        "collection": "E2E Marketing",
        "cards": ["Active Campaigns", "All Customers"],
    },
    {
        "name": "Finance Overview",
        "collection": "E2E Finance",
        "cards": ["Top Customers by Order Value", "Daily Revenue"],
    },
    {
        "name": "Catalog",
        "collection": "E2E Marketing",
        "cards": ["All Products"],
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


_HEALTH_TIMEOUT_S = 240
_SYNC_TIMEOUT_S = 120


async def _wait_for_health(client: httpx.AsyncClient, base_url: str) -> None:
    deadline = time.monotonic() + _HEALTH_TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = await client.get(f"{base_url}/api/health", timeout=5.0)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        await asyncio.sleep(2)
    raise TimeoutError(
        f"Metabase did not become healthy at {base_url} within "
        f"{_HEALTH_TIMEOUT_S}s (last error: {last_err!r})"
    )


async def _setup_or_login(
    client: httpx.AsyncClient, base_url: str, admin_email: str, admin_password: str
) -> str:
    """Return a Metabase session-id. Bootstraps admin if needed."""
    props = (await client.get(f"{base_url}/api/session/properties")).json()
    setup_token = props.get("setup-token")
    if setup_token:
        body = {
            "token": setup_token,
            "user": {
                "first_name": "E2E",
                "last_name": "Admin",
                "email": admin_email,
                "password": admin_password,
                "site_name": "Atlan Tests",
            },
            "prefs": {
                "site_name": "Atlan Tests",
                "site_locale": "en",
                "allow_tracking": False,
            },
            "database": None,
        }
        r = await client.post(f"{base_url}/api/setup", json=body)
        r.raise_for_status()
        return r.json()["id"]
    r = await client.post(
        f"{base_url}/api/session",
        json={"username": admin_email, "password": admin_password},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _register_source(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    source: dict[str, Any],
) -> int:
    """Register the source DB + trigger schema sync. Returns Metabase database id.

    Supports both ``mysql`` (default — what the e2e compose overlay uses)
    and ``postgres`` engines via ``source["engine"]``. The Metabase
    /api/database body shape is identical between the two — only the
    ``engine`` string differs.
    """
    engine = source.get("engine", "mysql")
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{base_url}/api/database", headers=headers)).json()
    candidates = (
        existing.get("data", existing) if isinstance(existing, dict) else existing
    )
    for db in candidates or []:
        details = db.get("details") or {}
        if (
            db.get("engine") == engine
            and details.get("host") == source["host"]
            and str(details.get("port")) == str(source["port"])
            and details.get("dbname") == source["db"]
        ):
            return int(db["id"])

    body = {
        "name": "e2e-source",
        "engine": engine,
        "details": {
            "host": source["host"],
            "port": int(source["port"]),
            "dbname": source["db"],
            "user": source["user"],
            "password": source["password"],
            "ssl": False,
            "tunnel-enabled": False,
        },
        "is_full_sync": True,
    }
    r = await client.post(f"{base_url}/api/database", headers=headers, json=body)
    r.raise_for_status()
    db_id = int(r.json()["id"])

    await client.post(f"{base_url}/api/database/{db_id}/sync_schema", headers=headers)
    deadline = time.monotonic() + _SYNC_TIMEOUT_S
    while time.monotonic() < deadline:
        meta = (
            await client.get(
                f"{base_url}/api/database/{db_id}/metadata", headers=headers
            )
        ).json()
        tables = meta.get("tables") or []
        # MySQL exposes the connection-level db as the "schema" name in
        # Metabase's /api/database/{id}/metadata. For our single-db layout
        # (``analytics``) we just need tables to materialize — no
        # multi-schema requirement.
        if tables:
            schemas = {t.get("schema") for t in tables if t.get("schema")}
            _log(f"  source sync: {len(tables)} tables across {sorted(schemas)}")
            return db_id
        await asyncio.sleep(2)
    raise TimeoutError("source schema sync timed out before tables appeared")


async def _pick_sample_database(
    client: httpx.AsyncClient, base_url: str, session_id: str
) -> tuple[int | None, dict[str, int]]:
    """Find Metabase's built-in sample DB. Returns (db_id, name→table_id)."""
    headers = {"X-Metabase-Session": session_id}
    dbs = (await client.get(f"{base_url}/api/database", headers=headers)).json()
    db_list = dbs if isinstance(dbs, list) else dbs.get("data", [])
    sample = next(
        (
            d
            for d in db_list
            if d.get("is_sample") or "sample" in d.get("name", "").lower()
        ),
        None,
    )
    if not sample:
        return None, {}
    meta = (
        await client.get(
            f"{base_url}/api/database/{sample['id']}/metadata", headers=headers
        )
    ).json()
    table_ids = {t["name"]: t["id"] for t in (meta.get("tables") or [])}
    return int(sample["id"]), table_ids


# ---------------------------------------------------------------------------
# Seed phases
# ---------------------------------------------------------------------------


async def _seed_collections(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    n: int,
) -> dict[str, int]:
    """Create up to N declared collections. Returns name→id of survivors."""
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{base_url}/api/collection", headers=headers)).json()
    name_to_id: dict[str, int] = {}
    for c in existing:
        try:
            name_to_id[c["name"]] = int(c["id"])
        except (TypeError, ValueError):
            continue

    declared = _DECLARED_COLLECTIONS[:n]
    for c in declared:
        if c["name"] in name_to_id:
            continue
        body: dict[str, Any] = {"name": c["name"], "color": c.get("color", "#509EE3")}
        if c.get("parent") and c["parent"] in name_to_id:
            body["parent_id"] = name_to_id[c["parent"]]
        r = await client.post(f"{base_url}/api/collection", headers=headers, json=body)
        if r.status_code in (200, 201):
            name_to_id[c["name"]] = int(r.json()["id"])
        else:
            _log(f"  ⚠️ collection {c['name']}: {r.status_code} {r.text[:120]}")
    _log(
        f"  collections: {len([c for c in declared if c['name'] in name_to_id])} of {len(declared)} declared"
    )
    return name_to_id


async def _seed_questions(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    n: int,
    collection_ids: dict[str, int],
    source_db_id: int | None,
    sample_db_id: int | None,
    sample_table_ids: dict[str, int],
) -> dict[str, int]:
    """Create up to N declared questions.

    Native-SQL routes to ``source_db_id`` when set (lineage); otherwise the
    question is converted to MBQL against the sample DB if available.
    Returns name→id of created cards.
    """
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{base_url}/api/card", headers=headers)).json()
    name_to_id: dict[str, int] = {}
    for q in existing:
        try:
            name_to_id[q["name"]] = int(q["id"])
        except (TypeError, ValueError):
            continue

    declared = _DECLARED_QUESTIONS[:n]
    for q in declared:
        if q["name"] in name_to_id:
            continue
        body: dict[str, Any] = {
            "name": q["name"],
            "collection_id": collection_ids.get(q.get("collection", "")) or None,
            "display": "table",
            "visualization_settings": {},
        }
        if q["type"] == "native" and source_db_id is not None:
            body["dataset_query"] = {
                "type": "native",
                "database": source_db_id,
                "native": {"query": q["sql"]},
            }
        else:
            # Fall back to MBQL against the sample DB (no lineage but a real
            # question record).
            if sample_db_id is None:
                _log(f"  skip {q['name']}: no source DB and no sample DB available")
                continue
            tbl_id = (
                next(iter(sample_table_ids.values()), None)
                if sample_table_ids
                else None
            )
            if q["type"] == "mbql":
                tbl_id = sample_table_ids.get(q["table"], tbl_id)
            if tbl_id is None:
                continue
            body["dataset_query"] = {
                "type": "query",
                "database": sample_db_id,
                "query": {"source-table": tbl_id},
            }
        r = await client.post(f"{base_url}/api/card", headers=headers, json=body)
        if r.status_code in (200, 201):
            name_to_id[q["name"]] = int(r.json()["id"])
        else:
            _log(f"  ⚠️ question {q['name']}: {r.status_code} {r.text[:120]}")
    _log(
        f"  questions: {len([q for q in declared if q['name'] in name_to_id])} of {len(declared)} declared"
    )
    return name_to_id


async def _seed_dashboards(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    n: int,
    collection_ids: dict[str, int],
    question_ids: dict[str, int],
) -> None:
    """Create up to N declared dashboards with their referenced cards."""
    headers = {"X-Metabase-Session": session_id}
    existing = (await client.get(f"{base_url}/api/dashboard", headers=headers)).json()
    name_to_id: dict[str, int] = {}
    for d in existing:
        try:
            name_to_id[d["name"]] = int(d["id"])
        except (TypeError, ValueError):
            continue

    declared = _DECLARED_DASHBOARDS[:n]
    for d in declared:
        if d["name"] not in name_to_id:
            r = await client.post(
                f"{base_url}/api/dashboard",
                headers=headers,
                json={
                    "name": d["name"],
                    "collection_id": collection_ids.get(d.get("collection", ""))
                    or None,
                },
            )
            if r.status_code not in (200, 201):
                _log(f"  ⚠️ dashboard {d['name']}: {r.status_code} {r.text[:120]}")
                continue
            name_to_id[d["name"]] = int(r.json()["id"])

        dash_id = name_to_id[d["name"]]
        cur = (
            await client.get(f"{base_url}/api/dashboard/{dash_id}", headers=headers)
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
                f"{base_url}/api/dashboard/{dash_id}",
                headers=headers,
                json={"dashcards": new_cards},
            )
            if r.status_code not in (200, 201):
                _log(f"  ⚠️ PUT dashboard {d['name']}: {r.status_code}")
    _log(
        f"  dashboards: {len([d for d in declared if d['name'] in name_to_id])} of {len(declared)} declared"
    )


# ---------------------------------------------------------------------------
# Public entry point — importable from integration conftest.
# ---------------------------------------------------------------------------


async def seed_metabase(
    base_url: str,
    *,
    admin_email: str,
    admin_password: str,
    n_collections: int = 4,
    n_questions: int = 5,
    n_dashboards: int = 3,
    source: dict[str, Any] | None = None,
    wait_for_health: bool = True,
) -> None:
    """Apply the declared seed against a running Metabase.

    Args:
        base_url: Metabase root URL, e.g. ``http://localhost:3000``.
        admin_email/password: admin user to create (or log in as).
        n_collections / n_questions / n_dashboards: how many declared
            items of each type to seed. Items are taken in declaration
            order; e.g. ``n_questions=2`` keeps the first two of
            ``_DECLARED_QUESTIONS``.
        source: when provided, register this postgres as a Metabase
            database and route native-SQL questions against it (enables
            QI lineage). Required keys: host, port, user, password, db.
            Optional key ``engine`` selects ``mysql`` (default) or
            ``postgres``.
            Pass ``None`` to skip — questions fall back to MBQL against
            the built-in sample DB.
        wait_for_health: skip the health poll if the caller has already
            waited (avoids double-waiting in the integration fixture).
    """
    async with httpx.AsyncClient(timeout=60) as client:
        if wait_for_health:
            with _timed("wait for Metabase health"):
                await _wait_for_health(client, base_url)

        with _timed("admin setup / login"):
            session_id = await _setup_or_login(
                client, base_url, admin_email, admin_password
            )

        source_db_id: int | None = None
        if source:
            with _timed("register source postgres + sync"):
                source_db_id = await _register_source(
                    client, base_url, session_id, source
                )

        with _timed("inspect sample database"):
            sample_db_id, sample_table_ids = await _pick_sample_database(
                client, base_url, session_id
            )

        with _timed("seed collections"):
            collection_ids = await _seed_collections(
                client, base_url, session_id, n_collections
            )

        with _timed("seed questions"):
            question_ids = await _seed_questions(
                client,
                base_url,
                session_id,
                n_questions,
                collection_ids,
                source_db_id,
                sample_db_id,
                sample_table_ids,
            )

        with _timed("seed dashboards"):
            await _seed_dashboards(
                client,
                base_url,
                session_id,
                n_dashboards,
                collection_ids,
                question_ids,
            )


# ---------------------------------------------------------------------------
# Script entry point — driven by env vars from the compose overlay.
# ---------------------------------------------------------------------------


async def _main() -> int:
    base_url = os.environ.get("MB_URL", "http://localhost:3000").rstrip("/")
    admin_email = os.environ["MB_ADMIN_EMAIL"]
    admin_password = os.environ["MB_ADMIN_PASSWORD"]

    source: dict[str, Any] | None = None
    if os.environ.get("MB_SOURCE_HOST"):
        engine = os.environ.get("MB_SOURCE_ENGINE", "mysql").lower()
        default_port = "3306" if engine == "mysql" else "5432"
        source = {
            "engine": engine,
            "host": os.environ["MB_SOURCE_HOST"],
            "port": int(os.environ.get("MB_SOURCE_PORT", default_port)),
            "user": os.environ.get("MB_SOURCE_USER", "source"),
            "password": os.environ.get("MB_SOURCE_PASSWORD", "source"),
            "db": os.environ.get("MB_SOURCE_DB", "analytics"),
        }

    n_collections = int(os.environ.get("MB_SEED_COLLECTIONS", "4"))
    n_questions = int(os.environ.get("MB_SEED_QUESTIONS", "5"))
    n_dashboards = int(os.environ.get("MB_SEED_DASHBOARDS", "3"))

    _log(
        f"start url={base_url} collections={n_collections} "
        f"questions={n_questions} dashboards={n_dashboards} "
        f"source={'mb-source' if source else 'sample-db'}"
    )

    await seed_metabase(
        base_url,
        admin_email=admin_email,
        admin_password=admin_password,
        n_collections=n_collections,
        n_questions=n_questions,
        n_dashboards=n_dashboards,
        source=source,
    )
    _log("seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
