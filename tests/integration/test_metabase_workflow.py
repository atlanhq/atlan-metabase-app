"""Integration tests for the Metabase connector — embedded Temporal + testcontainer.

Tests the full ``extract_metadata`` workflow through an in-process Temporal
worker against a session-scoped Metabase docker testcontainer seeded with
a minimal collection + question (see ``tests/integration/conftest.py``).
No externally-installed Dapr or Temporal required; no real Atlas tenant
required. The connector's HTTP layer hits the local container; results
land as files in a LocalStore that this module asserts against.

Pattern mirrors ``atlan-mysql-app/tests/integration/test_mysql_workflow.py``:
ONE class with a class-scoped fixture that executes ONE workflow run, then
multiple test methods share the same result and assert on different
facets. With pytest-xdist's ``--dist=loadfile`` distribution, the file is
pinned to one worker so the expensive fixture runs once total.

Filter-logic plumbing and the ``extract_lineage`` @entrypoint are unit-
tested in ``tests/unit/test_connector.py`` (TestFilterDataTask /
TestExtractLineage); re-running the heavy workflow only to assert
already-unit-covered code paths inflates CI time. Keep this file focused
on the workflow-through-real-Temporal contract.

Run tests with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from application_sdk.contracts.types import ConnectionRef

from app.connector import MetabaseApp
from app.contracts import MetabaseInput, MetabaseOutput

if TYPE_CHECKING:
    from tests.integration.conftest import AppExecutor


_CONNECTION_NAME = "test-metabase-integration"
_CONNECTION_QN = f"default/metabase/{_CONNECTION_NAME}"

_CONNECTION = ConnectionRef.model_validate(
    {
        "typeName": "Connection",
        "attributes": {
            "qualifiedName": _CONNECTION_QN,
            "name": _CONNECTION_NAME,
        },
    }
)


def _inline_credentials(creds: dict[str, Any]) -> list[dict[str, str]]:
    """Pack ``{host, port, username, password}`` into the v3 ``[{key, value}]`` shape.

    Uses FLAT keys (``username`` / ``password``) rather than the
    ``extra.username`` / ``extra.password`` HTTP-layer convention. Reason:
    ``build_credential_ref`` in app/credentials.py packs the list into a
    dict with keys preserved literally, and the downstream
    ``parse_metabase_credentials`` reads ``flat.get("username")`` directly —
    it strips the ``extra.`` prefix only when given a list (not a dict).
    Sending ``extra.``-prefixed keys lands them as literal dict keys and
    leaves username/password empty, which Metabase rejects with HTTP 400.

    Using the inline path keeps the test scope on the extraction workflow
    rather than CredentialRef → secret-store resolution (covered in
    ``tests/unit/test_credentials.py``).
    """
    return [
        {"key": "host", "value": str(creds["host"])},
        {"key": "port", "value": str(creds["port"])},
        {"key": "username", "value": str(creds["username"])},
        {"key": "password", "value": str(creds["password"])},
    ]


def _read_transformed_jsonl(output_path: str, typename: str) -> list[dict]:
    """Read ``transformed/<TYPENAME>/result-0.json`` and return its records.

    Returns an empty list when the file doesn't exist — different
    typenames may legitimately produce zero records against a sparsely-
    seeded Metabase.
    """
    f = Path(output_path) / "transformed" / typename / "result-0.json"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


class TestMetabaseExtraction:
    """Full ``extract_metadata`` workflow via embedded Temporal + testcontainer.

    One workflow runs (class-scoped fixture) and every test below asserts
    on the shared outcome — so the expensive crawl happens exactly once
    per pytest session.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("metabase_extraction")

    @pytest.fixture(scope="class")
    async def extraction_result(
        self,
        metabase_executor: "AppExecutor",
        metabase_credentials: dict[str, Any],
        tmp_dir_class: Path,
    ) -> MetabaseOutput:
        """Execute one extraction against the seeded testcontainer Metabase."""
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()
        return cast(
            "MetabaseOutput",
            await metabase_executor.execute_app(
                MetabaseApp,
                MetabaseInput(
                    workflow_id="integration-happy-path",
                    credentials=_inline_credentials(metabase_credentials),
                    connection=_CONNECTION,
                    output_path=str(output_dir),
                ),
                # MetabaseApp is multi-entry-point — without ``entry_point``
                # the backend would submit to workflow name "metabase",
                # which has no registered handler (the registered names are
                # "metabase:extract-metadata" and "metabase:extract-lineage").
                # Without this, the Temporal client awaits forever for a
                # listener that never claims the workflow.
                entry_point="extract-metadata",
                execution_id_prefix=f"metabase-int-{uuid.uuid4().hex[:8]}",
            ),
        )

    # -- workflow output object contract ------------------------------------

    @pytest.mark.asyncio
    async def test_workflow_completes(self, extraction_result: MetabaseOutput) -> None:
        """Workflow returns a populated ``MetabaseOutput``."""
        assert extraction_result is not None
        assert isinstance(extraction_result, MetabaseOutput)

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
        """The minimal seed produces at least one asset record."""
        assert extraction_result.total_records >= 1

    # -- transformed/ file-content assertions -------------------------------

    @pytest.mark.asyncio
    async def test_transformed_collection_file_has_records(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """``transformed/METABASECOLLECTION/result-0.json`` is populated.

        The conftest seed creates one collection; with the personal
        admin collection auto-created by ``/api/setup`` filtered out by
        the connector, we still expect at least the seeded one to land.
        """
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
    async def test_chunk_start_threaded_into_transform_filenames(
        self, extraction_result: MetabaseOutput
    ) -> None:
        """Default ``chunk_start=0`` produces ``result-0.json`` in transformed/."""
        f = (
            Path(extraction_result.output_path)
            / "transformed"
            / "METABASECOLLECTION"
            / "result-0.json"
        )
        assert f.exists()
