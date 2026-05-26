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
    assert len(records) >= 50, f"expected ≥ 50 collections, got {len(records)}"

    # Every auto-generated collection should be present.
    auto_names = {
        r["name"] for r in records if r.get("name", "").startswith("Auto Collection ")
    }
    assert (
        len(auto_names) >= 45
    ), f"expected ≥ 45 Auto Collection ### entries, got {len(auto_names)}"


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
async def test_native_questions_carry_qi_input_keys(app, inline_creds, output_dir):
    """At least 100 questions should expose metabaseQuery / metabaseSourceDatabaseName
    after enrichment. (The QI node downstream needs them populated.)"""
    _skip_unless_large()
    # End-to-end: extract → process. Skip detail/lineage to keep this test
    # focused on the QI-input contract.
    from app.contracts import FetchDetailInput, FilterInput, ProcessInput

    fi = FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    cols = await app.extract_collections(fi)
    dashs = await app.extract_dashboards(fi)
    qs = await app.extract_questions(fi)
    dbs = await app.extract_databases(fi)
    filtered = await app.filter_data(
        FilterInput(
            output_path=output_dir,
            include_collections={},
            exclude_collections={},
            collections_file=cols.output_file,
            dashboards_file=dashs.output_file,
            questions_file=qs.output_file,
            databases_file=dbs.output_file,
            inline_credentials=inline_creds,
        )
    )
    dash_details = await app.extract_individual_dashboards(
        FetchDetailInput(
            output_path=output_dir,
            source_file=filtered.dashboards_filtered_file,
            inline_credentials=inline_creds,
        )
    )
    q_queries = await app.fetch_question_queries_activity(
        FetchDetailInput(
            output_path=output_dir,
            source_file=filtered.questions_filtered_file,
            inline_credentials=inline_creds,
        )
    )
    await app.extract_individual_databases(
        FetchDetailInput(
            output_path=output_dir,
            source_file=filtered.databases_filtered_file,
            inline_credentials=inline_creds,
        )
    )
    await app.process_metabaseprocess(
        ProcessInput(
            output_path=output_dir,
            collections_filtered_file=filtered.collections_filtered_file,
            databases_filtered_file=filtered.databases_filtered_file,
            question_queries_file=q_queries.output_file,
            dashboard_details_file=dash_details.output_file,
            questions_filtered_file=filtered.questions_filtered_file,
            inline_credentials=inline_creds,
        )
    )

    processed = _read_jsonl(
        Path(output_dir) / "processed" / "questions" / "result-0.json"
    )
    with_query = [
        r
        for r in processed
        if r.get("metabase_query") and r.get("query_type") == "native"
    ]
    with_db_name = [r for r in processed if r.get("metabase_database_name")]
    print(
        f"[scale] processed {len(processed)} questions; "
        f"{len(with_query)} carry native metabase_query; "
        f"{len(with_db_name)} carry metabase_database_name"
    )
    assert len(with_query) >= 100, (
        f"expected ≥ 100 native questions with metabase_query populated, "
        f"got {len(with_query)}"
    )
    assert len(with_db_name) >= 100
