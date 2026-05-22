"""E2E test: sqlglot-driven lineage (Process + ColumnProcess) against real Metabase.

Drives the full pipeline up to ``parse_lineage`` and asserts:
- Process records reference the source tables (analytics.customers,
  analytics.orders, analytics.products) from native-SQL questions.
- ColumnProcess records reference the explicit columns selected.
- BIProcess records connect questions to dashboards (the v2-equivalent
  shape, emitted in ``process_metabase``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.connector import MetabaseApp
from app.contracts import (
    FetchDetailInput,
    FetchInput,
    FilterInput,
    ParseLineageInput,
    ProcessInput,
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


@pytest.mark.asyncio
async def test_lineage_resolves_source_tables(app, inline_creds, tmp_path):
    """parse_lineage must emit Process records that reference seeded tables."""
    output_path = str(tmp_path / "metabase-e2e")
    fetch_input = FetchInput(output_path=output_path, inline_credentials=inline_creds)

    cols = await app.extract_collections(fetch_input)
    dashs = await app.extract_dashboards(fetch_input)
    qs = await app.extract_questions(fetch_input)
    dbs = await app.extract_databases(fetch_input)

    filtered = await app.filter_data(
        FilterInput(
            output_path=output_path,
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
            output_path=output_path,
            source_file=filtered.dashboards_filtered_file,
            inline_credentials=inline_creds,
        )
    )
    db_meta = await app.extract_individual_databases(
        FetchDetailInput(
            output_path=output_path,
            source_file=filtered.databases_filtered_file,
            inline_credentials=inline_creds,
        )
    )
    q_queries = await app.fetch_question_queries_activity(
        FetchDetailInput(
            output_path=output_path,
            source_file=filtered.questions_filtered_file,
            inline_credentials=inline_creds,
        )
    )

    processed = await app.process_metabaseprocess(
        ProcessInput(
            output_path=output_path,
            collections_filtered_file=filtered.collections_filtered_file,
            databases_filtered_file=filtered.databases_filtered_file,
            question_queries_file=q_queries.output_file,
            dashboard_details_file=dash_details.output_file,
            questions_filtered_file=filtered.questions_filtered_file,
            inline_credentials=inline_creds,
        )
    )

    # BIProcess (Question → Dashboard) lineage, emitted by process_metabase.
    qd = _read_jsonl(Path(processed.questions_dashboards_processed_file.local_path))
    assert qd, "process_metabase produced no BIProcess records"
    qd_q_names = {r.get("question_name") or r.get("name") for r in qd}
    assert "Top Customers by Order Value" in qd_q_names

    # parse_lineage — sqlglot Process + ColumnProcess.
    lineage_out = await app.parse_lineage(
        ParseLineageInput(
            output_path=output_path,
            connection_qualified_name="default/metabase/e2e",
            questions_processed_file=processed.questions_processed_file,
            database_metadata_file=db_meta.output_file,
        )
    )

    processes = _read_jsonl(Path(lineage_out.processes_file.local_path))
    assert processes, "parse_lineage produced zero Process records"

    # The "Top Customers" question joins customers + orders — at least one
    # Process record must reference both.
    top_customers = next(
        (p for p in processes if p.get("name") == "Top Customers by Order Value"),
        None,
    )
    assert top_customers, processes
    qns = set(top_customers.get("input_table_qualified_names") or [])
    assert any("customers" in q for q in qns), qns
    assert any("orders" in q for q in qns), qns

    column_processes = _read_jsonl(Path(lineage_out.column_processes_file.local_path))
    # ColumnProcess emission is best-effort; verify at least one record was
    # produced if any explicit columns were referenced.
    cp_names = {c.get("name") for c in column_processes}
    assert (
        "Top Customers by Order Value" in cp_names
        or "Recent Orders" in cp_names
        or "Product Catalog" in cp_names
    ), column_processes
