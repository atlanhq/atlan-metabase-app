"""E2E test: full extraction pipeline against a seeded Metabase instance.

Drives each ``@task`` method on a real ``MetabaseApp`` instance with inline
credentials (no secret store needed), writes outputs to a tmp directory,
and asserts the seeded entities are present with expected attributes.

Validates the openapi-pattern shape: each task's output feeds into the
next, mirroring how ``run()`` orchestrates them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.connector import MetabaseApp
from app.contracts import (
    FetchInput,
    FilterInput,
)

pytestmark = pytest.mark.e2e


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


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
    return str(tmp_path / "metabase-e2e")


@pytest.mark.asyncio
async def test_extract_collections_returns_seeded_collections(
    app, inline_creds, output_dir, seed_spec
):
    """``extract_collections`` must return every seeded named collection."""
    out = await app.extract_collections(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    assert out.output_file is not None
    records = _read_jsonl(Path(out.output_file.local_path))
    names = {r.get("name") for r in records}
    for c in seed_spec["collections"]:
        assert c["name"] in names, f"missing collection {c['name']} in {names}"


@pytest.mark.asyncio
async def test_extract_questions_returns_seeded_questions(
    app, inline_creds, output_dir, seed_spec
):
    """``extract_questions`` must return every seeded card."""
    out = await app.extract_questions(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    records = _read_jsonl(Path(out.output_file.local_path))
    names = {r.get("name") for r in records}
    for q in seed_spec["questions"]:
        assert q["name"] in names, f"missing question {q['name']} in {names}"


@pytest.mark.asyncio
async def test_extract_dashboards_returns_seeded_dashboards(
    app, inline_creds, output_dir, seed_spec
):
    out = await app.extract_dashboards(
        FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    )
    records = _read_jsonl(Path(out.output_file.local_path))
    names = {r.get("name") for r in records}
    for d in seed_spec["dashboards"]:
        assert d["name"] in names, f"missing dashboard {d['name']} in {names}"


@pytest.mark.asyncio
async def test_filter_data_excludes_collections(app, inline_creds, output_dir):
    """Excluding 'E2E Excluded' must drop the excluded question."""
    fetch_input = FetchInput(output_path=output_dir, inline_credentials=inline_creds)
    cols = await app.extract_collections(fetch_input)
    dashs = await app.extract_dashboards(fetch_input)
    qs = await app.extract_questions(fetch_input)
    dbs = await app.extract_databases(fetch_input)

    # Find the id of "E2E Excluded" collection in the raw output.
    cols_records = _read_jsonl(Path(cols.output_file.local_path))
    excluded = next((c for c in cols_records if c["name"] == "E2E Excluded"), None)
    assert excluded, "seed did not produce 'E2E Excluded' collection"

    filtered = await app.filter_data(
        FilterInput(
            output_path=output_dir,
            include_collections={},
            exclude_collections={str(excluded["id"]): {}},
            collections_file=cols.output_file,
            dashboards_file=dashs.output_file,
            questions_file=qs.output_file,
            databases_file=dbs.output_file,
            inline_credentials=inline_creds,
        )
    )

    fq = _read_jsonl(Path(filtered.questions_filtered_file.local_path))
    q_names = {r.get("name") for r in fq}
    assert "Excluded Question" not in q_names, q_names
    # Other seeded questions should remain.
    assert "Top Customers by Order Value" in q_names
