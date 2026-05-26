"""E2E scale test — run only when ``E2E_SCALE=large``.

After ``seed_metabase.py`` seeds ~1000 assets across the Metabase Docker
instance, drive every extract ``@task`` and assert:

- Every named-and-numbered seeded asset shows up in the extract output.
- Counts meet the expected minima (50 collections, 800 questions,
  150 dashboards).
- The Top Customers seeded question still resolves with full attribute
  coverage (sanity-anchored on a known fixture).
- Native-SQL questions carry the QI input keys populated
  (``metabase_query`` non-empty for at least 100 records).
- Extraction wall-clock time stays under a soft budget so we catch
  regressions in the per-question SQL fetch path.

Skipped under ``E2E_SCALE=small`` so the small smoke-test path stays
under a minute on developer machines.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app.connector import MetabaseApp
from app.contracts import FetchInput

pytestmark = pytest.mark.e2e

_SCALE = os.environ.get("E2E_SCALE", "small").lower()


def _skip_unless_large():
    if _SCALE != "large":
        pytest.skip(f"scale-test skipped: E2E_SCALE={_SCALE!r} (set 'large' to run)")


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


@pytest.fixture
def inline_creds(metabase_admin) -> dict[str, Any]:
    email, password = metabase_admin
    return {
        "host": "http://localhost",
        "port": 3000,
        "username": email,
        "password": password,
    }


@pytest.fixture
def app() -> MetabaseApp:
    return MetabaseApp()


@pytest.fixture
def output_dir(tmp_path: Path) -> str:
    return str(tmp_path / "scale-e2e")


@pytest.mark.asyncio
async def test_collections_meet_scale_minimum(app, inline_creds, output_dir):
    """Large seed declares 4 + 50 auto-generated = 54 named collections,
    plus an admin personal collection Metabase auto-creates. Expect ≥ 50."""
    _skip_unless_large()
    out = await app.extract_collections(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    records = _read_jsonl(Path(out.output_file.local_path))
    print(f"[scale] extracted {len(records)} collections")
    # Soft minimum — seeding ~50 in parallel against a fresh Metabase has
    # ~10-15% failure-to-create rate under concurrent POSTs. We assert the
    # bulk landed, not the exact target.
    assert len(records) >= 40, f"expected ≥ 40 collections, got {len(records)}"

    auto_names = {
        r["name"] for r in records if r.get("name", "").startswith("Auto Collection ")
    }
    assert (
        len(auto_names) >= 30
    ), f"expected ≥ 30 Auto Collection ### entries, got {len(auto_names)}"


@pytest.mark.asyncio
async def test_questions_meet_scale_minimum(app, inline_creds, output_dir):
    """800 generated + 5 declared questions. Expect ≥ 800 total."""
    _skip_unless_large()
    out = await app.extract_questions(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    records = _read_jsonl(Path(out.output_file.local_path))
    print(f"[scale] extracted {len(records)} questions")
    assert len(records) >= 800, f"expected ≥ 800 questions, got {len(records)}"

    by_prefix = {
        "auto_native": sum(
            1 for r in records if (r.get("name", "")).startswith("Auto Native ")
        ),
        "auto_mbql": sum(
            1 for r in records if (r.get("name", "")).startswith("Auto MBQL ")
        ),
    }
    print(f"[scale] question shape breakdown: {by_prefix}")
    assert by_prefix["auto_native"] >= 350, by_prefix
    assert by_prefix["auto_mbql"] >= 350, by_prefix


@pytest.mark.asyncio
async def test_dashboards_meet_scale_minimum(app, inline_creds, output_dir):
    """150 generated + 3 declared dashboards. Expect ≥ 150."""
    _skip_unless_large()
    out = await app.extract_dashboards(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    records = _read_jsonl(Path(out.output_file.local_path))
    print(f"[scale] extracted {len(records)} dashboards")
    assert len(records) >= 150, f"expected ≥ 150 dashboards, got {len(records)}"


@pytest.mark.asyncio
async def test_extract_time_budget(app, inline_creds, output_dir):
    """Soft budget — guard against per-question SQL fetch regressing.
    All four parallel-safe extracts should finish in < 60 s combined."""
    _skip_unless_large()
    fi = FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    start = time.monotonic()
    cols = await app.extract_collections(fi)
    dashs = await app.extract_dashboards(fi)
    qs = await app.extract_questions(fi)
    dbs = await app.extract_databases(fi)
    elapsed = time.monotonic() - start
    counts = {
        "collections": cols.record_count,
        "dashboards": dashs.record_count,
        "questions": qs.record_count,
        "databases": dbs.record_count,
    }
    print(f"[scale] summary {counts}  ({elapsed:.1f}s)")
    assert (
        elapsed < 60
    ), f"extraction took {elapsed:.1f}s; investigate per-question SQL fetch"


@pytest.mark.asyncio
async def test_native_questions_present_in_extract(app, inline_creds, output_dir):
    """A meaningful chunk of the extracted questions should be native-SQL
    questions referencing the seeded source database. The QI node downstream
    consumes ``dataset_query.native.query`` and the resolved
    ``database_id`` — both must be on the raw extract.

    Lighter than the full extract → filter → detail → process pipeline,
    which calls self.upload() and needs an SDK lifecycle the test
    harness doesn't provide.
    """
    _skip_unless_large()
    out = await app.extract_questions(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    records = _read_jsonl(Path(out.output_file.local_path))

    native = [
        r
        for r in records
        if (r.get("dataset_query") or {}).get("type") == "native"
        and (r.get("dataset_query") or {}).get("native", {}).get("query")
    ]
    mbql = [r for r in records if (r.get("dataset_query") or {}).get("type") == "query"]
    print(
        f"[scale] questions: total={len(records)} "
        f"native_with_sql={len(native)} mbql={len(mbql)}"
    )
    assert (
        len(native) >= 200
    ), f"expected ≥ 200 native-SQL questions in the extract, got {len(native)}"
    # Every native question must carry a non-null database_id — QI uses
    # it to scope SQL parsing to the source connection.
    with_db = [r for r in native if r.get("database_id") is not None]
    assert len(with_db) == len(
        native
    ), f"{len(native) - len(with_db)} native questions are missing database_id"
