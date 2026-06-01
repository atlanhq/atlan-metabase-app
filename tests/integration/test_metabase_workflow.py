"""Integration tests for the Metabase connector.

Runs the full ``extract_metadata`` workflow through embedded Temporal +
the in-process MetabaseApp worker, against a real Metabase server reached
via ``E2E_METABASE_*`` env vars. Asserts the workflow output contains the
expected path / count fields without inspecting the on-disk JSONL — those
files are covered by unit tests on the per-task code paths.

Module shape mirrors ``atlan-openapi-app/tests/integration/test_openapi.py``:
a class-scoped fixture runs the workflow once and individual tests assert
on the shared result so we don't pay the extraction cost N times.

Requires:
    E2E_METABASE_HOST / _PORT / _USERNAME / _PASSWORD env vars
    (see tests/integration/conftest.py for the mock-secret-store seeding)

Run with:
    uv run pytest tests/integration/ -v
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from application_sdk.contracts.types import ConnectionRef
from application_sdk.credentials.ref import CredentialRef

from app.connector import MetabaseApp
from app.contracts import MetabaseInput, MetabaseOutput

from tests.integration.conftest import require_metabase_env

if TYPE_CHECKING:
    from tests.integration.conftest import AppExecutor

require_metabase_env()


_CONNECTION_NAME = "test-metabase-integration"
_CONNECTION_QN = f"default/metabase/{_CONNECTION_NAME}"


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

        result = cast(
            "MetabaseOutput",
            await metabase_executor.execute_app(
                MetabaseApp,
                MetabaseInput(
                    workflow_id="integration-test",
                    metabase_credential=CredentialRef(
                        name="metabase",
                        credential_type="basic",
                        credential_guid="metabase",
                    ),
                    connection=ConnectionRef.model_validate(
                        {
                            "typeName": "Connection",
                            "attributes": {
                                "qualifiedName": _CONNECTION_QN,
                                "name": _CONNECTION_NAME,
                            },
                        }
                    ),
                    # Empty filters → crawl every non-personal collection.
                    output_path=str(output_dir),
                ),
            ),
        )
        return result

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
        assert _CONNECTION_QN in extraction_result.transformed_data_prefix or (
            extraction_result.transformed_data_prefix.startswith("artifacts/")
        )

    @pytest.mark.asyncio
    async def test_view_lineage_output_prefix_populated(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """QI reads parsed-SQL output from this prefix; must be set."""
        assert extraction_result.view_lineage_output_prefix
        assert (
            "view-lineage" in extraction_result.view_lineage_output_prefix
            or "view_lineage" in extraction_result.view_lineage_output_prefix
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
