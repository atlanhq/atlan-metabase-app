"""Integration tests for the Metabase connector.

Runs Metabase workflows through embedded Temporal + the in-process
MetabaseApp worker, against a real Metabase server reached via
``E2E_METABASE_*`` env vars. Each test class executes ONE workflow run
via a class-scoped fixture, then asserts on the shared outcome — so the
expensive crawl is paid once per scenario, not per assertion.

Module shape mirrors ``atlan-openapi-app/tests/integration/test_openapi.py``:
multiple ``TestX`` classes for different invocation scenarios, each with
its own class-scoped fixture.

Scenarios covered:
    - TestMetabaseExtractionWorkflow        — happy path (default filters,
                                                CredentialRef path); rich
                                                file-content assertions
    - TestMetabaseExtractionWithFilters     — include + exclude filters
                                                accepted; workflow completes
    - TestMetabaseInlineCredentials         — credentials=[...] inline path
                                                (no CredentialRef)
    - TestMetabaseLineageEntrypoint         — extract_lineage @entrypoint
                                                handles empty QI input
                                                without crashing

Requires:
    E2E_METABASE_HOST / _PORT / _USERNAME / _PASSWORD env vars
    (see tests/integration/conftest.py for the mock-secret-store seeding)

Run with:
    uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from application_sdk.contracts.types import ConnectionRef
from application_sdk.credentials.ref import CredentialRef

from app.connector import MetabaseApp
from app.contracts import (
    CollectionSelection,
    MetabaseInput,
    MetabaseLineageInput,
    MetabaseLineageOutput,
    MetabaseOutput,
)
from tests.integration.conftest import require_metabase_env

if TYPE_CHECKING:
    from tests.integration.conftest import AppExecutor

require_metabase_env()


_CONNECTION_NAME = "test-metabase-integration"
_CONNECTION_QN = f"default/metabase/{_CONNECTION_NAME}"

# CredentialRef the conftest seeds into the MockSecretStore from
# E2E_METABASE_* env vars. Used by every scenario except the inline-
# credentials test class.
_CRED_REF = CredentialRef(
    name="metabase",
    credential_type="basic",
    credential_guid="metabase",
)

_CONNECTION = ConnectionRef.model_validate(
    {
        "typeName": "Connection",
        "attributes": {
            "qualifiedName": _CONNECTION_QN,
            "name": _CONNECTION_NAME,
        },
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_transformed_jsonl(output_path: str, typename: str) -> list[dict]:
    """Read ``transformed/<TYPENAME>/result-0.json`` and return its records.

    Returns an empty list when the file doesn't exist — different
    typenames may legitimately produce zero records on a sparse tenant.
    """
    f = Path(output_path) / "transformed" / typename / "result-0.json"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# TestMetabaseExtractionWorkflow — happy path, rich post-run assertions
# ---------------------------------------------------------------------------


class TestMetabaseExtractionWorkflow:
    """Full ``extract_metadata`` workflow through embedded Temporal.

    Executes one workflow and shares the result across tests via a
    class-scoped fixture so we do not re-run the extraction (which is
    expensive against a real Metabase tenant).
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("metabase_extraction")

    @pytest.fixture(scope="class")
    async def extraction_result(
        self,
        metabase_executor: "AppExecutor",
        tmp_dir_class: Path,
    ) -> MetabaseOutput:
        """Execute a full extraction against the real Metabase tenant."""
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()
        return cast(
            "MetabaseOutput",
            await metabase_executor.execute_app(
                MetabaseApp,
                MetabaseInput(
                    workflow_id="integration-happy-path",
                    metabase_credential=_CRED_REF,
                    connection=_CONNECTION,
                    output_path=str(output_dir),
                ),
            ),
        )

    # -- output-shape assertions --------------------------------------------

    @pytest.mark.asyncio
    async def test_connection_qualified_name_echoed(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """Output echoes the input connection QN for downstream nodes."""
        assert extraction_result.connection_qualified_name == _CONNECTION_QN

    @pytest.mark.asyncio
    async def test_transformed_data_prefix_populated(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """``transformed_data_prefix`` is the publish-node input — must be set."""
        assert extraction_result.transformed_data_prefix

    @pytest.mark.asyncio
    async def test_view_lineage_output_prefix_populated(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """QI reads parsed-SQL output from this prefix; must be set."""
        assert extraction_result.view_lineage_output_prefix
        assert "view-lineage" in extraction_result.view_lineage_output_prefix or (
            "view_lineage" in extraction_result.view_lineage_output_prefix
        )

    @pytest.mark.asyncio
    async def test_state_prefixes_scoped_to_connection(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """Publish-state buckets are scoped per connection QN."""
        assert _CONNECTION_QN in extraction_result.publish_state_prefix
        assert _CONNECTION_QN in extraction_result.lineage_publish_state_prefix

    @pytest.mark.asyncio
    async def test_total_records_positive(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """A live Metabase tenant should yield at least some assets."""
        assert extraction_result.total_records >= 1

    # -- transformed/ file-content assertions -------------------------------

    @pytest.mark.asyncio
    async def test_transformed_collection_file_has_records(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """``transformed/METABASECOLLECTION/result-0.json`` is populated."""
        records = _read_transformed_jsonl(
            extraction_result.output_path, "METABASECOLLECTION"
        )
        assert len(records) >= 1, "expected at least one MetabaseCollection record"

    @pytest.mark.asyncio
    async def test_transformed_records_have_required_atlas_fields(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """Every transformed record carries the minimum Atlas envelope."""
        records = _read_transformed_jsonl(
            extraction_result.output_path, "METABASECOLLECTION"
        )
        for r in records[:5]:  # spot-check the first 5
            assert r.get("typeName") == "MetabaseCollection"
            attrs = r.get("attributes") or {}
            assert attrs.get("qualifiedName"), f"no qualifiedName on {r}"
            assert attrs.get("name"), f"no name on {r}"
            # Connection QN is always the prefix of the asset QN.
            assert attrs["qualifiedName"].startswith(_CONNECTION_QN)

    @pytest.mark.asyncio
    async def test_transformed_questions_carry_qi_input_keys(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """Questions stamp the metabaseQuery + source-DB/schema keys QI reads.

        These three attribute keys are the QueryIntelligence app's input
        contract — if any extraction regression drops them, lineage breaks
        downstream. The unit test ``test_process_enrich.py`` already covers
        the stamper; this test verifies the stamping survives the full
        workflow path.
        """
        records = _read_transformed_jsonl(
            extraction_result.output_path, "METABASEQUESTION"
        )
        if not records:
            pytest.skip("tenant has no questions")
        # At least ONE native-SQL question must carry the QI keys; pure
        # MBQL questions correctly leave metabaseQuery empty.
        with_query = [
            r for r in records if (r.get("attributes") or {}).get("metabaseQuery")
        ]
        if not with_query:
            pytest.skip("tenant has no native-SQL questions")
        attrs = with_query[0]["attributes"]
        assert attrs.get("metabaseSourceDatabaseName") is not None
        assert attrs.get("metabaseSourceSchemaName") is not None

    @pytest.mark.asyncio
    async def test_chunk_start_threaded_into_transform_filenames(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """Default ``chunk_start=0`` produces ``result-0.json`` in transformed/."""
        # Spot-check one typename; the @task uses chunk_start for the
        # output filename suffix.
        f = (
            Path(extraction_result.output_path)
            / "transformed"
            / "METABASECOLLECTION"
            / "result-0.json"
        )
        assert f.exists()


# ---------------------------------------------------------------------------
# TestMetabaseExtractionWithFilters — include / exclude filter plumbing
# ---------------------------------------------------------------------------


class TestMetabaseExtractionWithFilters:
    """Workflow accepts both filter shapes and completes without crashing.

    Filter logic itself is unit-tested in ``tests/unit/extracts/test_filter.py``;
    this class verifies the filter dicts thread through the workflow input
    contract → ``filter_data`` @task → output without error. Uses an
    obviously-non-matching collection id in ``exclude_collections`` so the
    extraction is functionally equivalent to the unfiltered run but exercises
    the filter code path.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("metabase_filtered")

    @pytest.fixture(scope="class")
    async def filtered_result(
        self,
        metabase_executor: "AppExecutor",
        tmp_dir_class: Path,
    ) -> MetabaseOutput:
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()
        return cast(
            "MetabaseOutput",
            await metabase_executor.execute_app(
                MetabaseApp,
                MetabaseInput(
                    workflow_id="integration-filtered",
                    metabase_credential=_CRED_REF,
                    connection=_CONNECTION,
                    # Non-matching id — the filter is exercised but no
                    # records are actually dropped, so we can still assert
                    # total_records >= 1.
                    exclude_collections={
                        "_does_not_exist_99999_": CollectionSelection()
                    },
                    output_path=str(output_dir),
                ),
            ),
        )

    @pytest.mark.asyncio
    async def test_filtered_workflow_completes(
        self, filtered_result: MetabaseOutput
    ) -> None:
        """Workflow with an exclude_collections payload returns a populated output."""
        assert filtered_result.connection_qualified_name == _CONNECTION_QN
        assert filtered_result.total_records >= 1
        assert filtered_result.transformed_data_prefix


# NOTE: A previous ``TestMetabaseInlineCredentials`` class ran the entire
# extract workflow a third time just to exercise the inline-credentials
# fallback. That credential-routing logic is fully covered by
# ``tests/unit/test_credentials.py::test_build_credential_ref_inline``;
# re-running the heavy workflow only to assert the same code path was a
# duplicate full-extract worth ~5 min on CI. Removed in favour of the unit
# coverage so the integration suite stays within budget.


# ---------------------------------------------------------------------------
# TestMetabaseLineageEntrypoint — extract_lineage @entrypoint smoke
# ---------------------------------------------------------------------------


class TestMetabaseLineageEntrypoint:
    """``extract_lineage`` handles empty QI input cleanly.

    The lineage entrypoint is invoked downstream by QueryIntelligence; in
    isolation we just verify it does not crash on an empty
    ``view_lineage_input_prefix`` and returns a well-formed
    ``MetabaseLineageOutput`` with zero counts. The detailed lineage
    record construction is unit-tested in ``tests/unit/test_lineage_ars.py``.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("metabase_lineage")

    @pytest.fixture(scope="class")
    async def lineage_result(
        self,
        metabase_executor: "AppExecutor",
        tmp_dir_class: Path,
    ) -> MetabaseLineageOutput:
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()
        return cast(
            "MetabaseLineageOutput",
            await metabase_executor.execute_app(
                MetabaseApp,
                MetabaseLineageInput(
                    workflow_id="integration-lineage",
                    connection=_CONNECTION,
                    connection_qualified_name=_CONNECTION_QN,
                    # Empty — no QI output to ingest. The entrypoint must
                    # complete with zero counts rather than crash.
                    view_lineage_input_prefix="",
                    output_path=str(output_dir),
                ),
            ),
        )

    @pytest.mark.asyncio
    async def test_empty_qi_input_returns_zero_counts(
        self, lineage_result: MetabaseLineageOutput
    ) -> None:
        assert lineage_result.process_count == 0
        assert lineage_result.column_process_count == 0

    @pytest.mark.asyncio
    async def test_connection_qn_echoed_into_lineage_output(
        self, lineage_result: MetabaseLineageOutput
    ) -> None:
        """Downstream lineage-publish reads ``connection_qualified_name``."""
        assert lineage_result.connection_qualified_name == _CONNECTION_QN

    @pytest.mark.asyncio
    async def test_state_prefixes_scoped_to_connection(
        self, lineage_result: MetabaseLineageOutput
    ) -> None:
        assert _CONNECTION_QN in lineage_result.lineage_publish_state_prefix
