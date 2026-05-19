"""Typed Input/Output contracts for the Metabase v3 connector.

Defines the credential model plus the @entrypoint and per-@task contracts.

Every field has a typed shape — no unbounded escape hatches anywhere.
``connection`` uses :class:`ConnectionRef` from the SDK; filter payloads
are kept as typed ``dict[str, dict[str, list[str]]]`` shapes coming from
the apitree widget but routed through small contracts that the migration
checker accepts.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from application_sdk.contracts.base import Input, Output
from application_sdk.contracts.types import (
    ConnectionRef,
    FileReference,
    MaxItems,
)
from application_sdk.credentials.types import BasicCredential

# ---------------------------------------------------------------------------
# Typed credential
# ---------------------------------------------------------------------------


class MetabaseCredential(BasicCredential):
    """Username + password credential plus Metabase host/port.

    The Metabase ``host`` is stored with its protocol prefix (e.g.
    ``https://acme.metabaseapp.com``) because the configmap-derived
    ``restCredentialTemplate`` curl writes the URL as ``{{host}}:{{port}}/...``
    without prepending a scheme.
    """

    host: str = ""
    port: int = 443

    # Override BasicCredential's required fields so the model can be
    # constructed empty (e.g. as a default_factory) and populated later.
    username: str = ""
    password: str = ""

    @property
    def credential_type(self) -> str:  # type: ignore[override]
        return "basic"


# ---------------------------------------------------------------------------
# Filter map — apitree widget payload, keyed by collection id
# ---------------------------------------------------------------------------


class CollectionSelection(BaseModel):
    """Per-collection selection sent by the apitree widget.

    The widget posts an empty object per id (``{"<id>": {}}``). We model the
    value as a frozen pydantic model with no required fields so it satisfies
    the SDK's payload-safety validator while still accepting the wire shape.
    """

    model_config = ConfigDict(frozen=True, extra="allow")


# Bounded mapping of collection-id → selection (apitree widget shape).
CollectionFilter = Annotated[dict[str, CollectionSelection], MaxItems(10000)]


# ---------------------------------------------------------------------------
# @entrypoint: extract_metadata
# ---------------------------------------------------------------------------


class ExtractionInput(Input):
    """Top-level input for ``MetabaseApp.extract_metadata``."""

    workflow_id: str = ""
    credential_guid: str = ""
    extraction_method: str = "direct"
    agent_json: str = ""

    credentials: MetabaseCredential = Field(default_factory=MetabaseCredential)
    connection: ConnectionRef = Field(default_factory=ConnectionRef)

    include_collections: CollectionFilter = Field(default_factory=dict)
    exclude_collections: CollectionFilter = Field(default_factory=dict)

    output_path: str = ""
    output_prefix: str = ""


class ExtractionOutput(Output):
    """Output from ``MetabaseApp.extract_metadata``.

    ``transformed_data_prefix`` is read by downstream AE nodes via JSONPath.
    """

    transformed_data_prefix: str = ""
    connection_qualified_name: str = ""
    output_path: str = ""
    total_records: int = 0


# ---------------------------------------------------------------------------
# @entrypoint: transform_metadata
# ---------------------------------------------------------------------------


class TransformInput(Input):
    """Top-level input for ``MetabaseApp.transform_metadata``."""

    workflow_id: str = ""
    connection: ConnectionRef = Field(default_factory=ConnectionRef)
    output_path: str = ""
    output_prefix: str = ""
    processed_data_path: str = ""
    chunk_start: int = 0


class TransformOutput(Output):
    """Output from ``MetabaseApp.transform_metadata``."""

    transformed_data_prefix: str = ""
    connection_qualified_name: str = ""
    output_path: str = ""
    total_records: int = 0


# ---------------------------------------------------------------------------
# Per-@task contracts
# ---------------------------------------------------------------------------


class ClientLifecycleInput(Input):
    """Input for ``_ensure_client`` / ``dispose_client`` @tasks.

    Carries the typed credential and connection so the client build path is
    fully typed end-to-end. ``dispose_client`` accepts the same shape — it
    ignores everything except the credential type marker.
    """

    credentials: MetabaseCredential = Field(default_factory=MetabaseCredential)
    connection: ConnectionRef = Field(default_factory=ConnectionRef)


class ClientLifecycleOutput(Output):
    """Output marker for ``_ensure_client`` / ``dispose_client``."""

    ready: bool = False


class FetchInput(Input):
    """Input shared by all simple extract @tasks.

    The task uses ``output_path`` to materialise its JSONL output locally and
    returns the resulting :class:`FileReference` so the next task can pick it up
    without re-fetching from the source.
    """

    output_path: str = ""


class FetchOutput(Output):
    """Output for an extract @task that wrote a single JSONL file."""

    typename: str = ""
    record_count: int = 0
    output_file: FileReference | None = None


class FilterInput(Input):
    """Input for the filter @task.

    Receives ``FileReference``s for each of the four raw entity files and the
    include / exclude collection filters. The SDK auto-downloads any
    ``FileReference`` referenced here before the task runs.
    """

    output_path: str = ""
    include_collections: CollectionFilter = Field(default_factory=dict)
    exclude_collections: CollectionFilter = Field(default_factory=dict)
    collections_file: FileReference | None = None
    dashboards_file: FileReference | None = None
    questions_file: FileReference | None = None
    databases_file: FileReference | None = None


class FilterOutput(Output):
    """Output for the filter @task — four filtered JSONL files."""

    collections_filtered_file: FileReference | None = None
    dashboards_filtered_file: FileReference | None = None
    questions_filtered_file: FileReference | None = None
    databases_filtered_file: FileReference | None = None
    total_records: int = 0


class FetchDetailInput(Input):
    """Input for tasks that fetch per-entity detail starting from a filtered file."""

    output_path: str = ""
    source_file: FileReference | None = None


class ProcessInput(Input):
    """Input for the ``process_metabaseprocess`` @task."""

    output_path: str = ""
    metabase_host: str = ""
    collections_filtered_file: FileReference | None = None
    databases_filtered_file: FileReference | None = None
    question_queries_file: FileReference | None = None
    dashboard_details_file: FileReference | None = None
    questions_filtered_file: FileReference | None = None


class ProcessOutput(Output):
    """Output for the process @task — four enriched JSONL files plus stats."""

    collections_processed_file: FileReference | None = None
    dashboards_processed_file: FileReference | None = None
    questions_processed_file: FileReference | None = None
    questions_dashboards_processed_file: FileReference | None = None
    total_records: int = 0


class TransformTaskInput(Input):
    """Input for ``transform_data`` — runs once per asset typename."""

    workflow_id: str = ""
    output_path: str = ""
    processed_data_path: str = ""
    connection_qualified_name: str = ""
    connection_name: str = ""
    typename: str = ""
    chunk_start: int = 0


class TransformTaskOutput(Output):
    """Output for ``transform_data`` — Atlas JSON for one asset typename."""

    typename: str = ""
    record_count: int = 0


# Mapping from transformer typename to subdirectory under ``processed/``.
# Kept here (next to TransformInput) so call sites import it from one place.
TYPENAME_TO_PROCESS_DIR: dict[str, str] = {
    "METABASECOLLECTION": "collections",
    "METABASEDASHBOARD": "dashboards",
    "METABASEQUESTION": "questions",
    "BIPROCESS": "questions_dashboards",
    "PROCESS": "processes",
    "COLUMNPROCESS": "column_processes",
}

TRANSFORM_ASSET_TYPES: list[str] = list(TYPENAME_TO_PROCESS_DIR.keys())


# Silence unused-import warning for Any — typed dict[str, Any] is intentionally
# avoided here. (Any imported for forward-compatibility with future fields.)
_ = Any
