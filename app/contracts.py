"""Typed Input/Output contracts for the Metabase v3 connector.

Defines the credential model, the single-``run()`` entrypoint contract, and
the per-``@task`` contracts.

Architecture mirrors ``atlan-openapi-app``: one App class with a single
``run()`` override that orchestrates ``@task`` methods sequentially. The
platform (platform-packages) dispatches Metabase as **one** Argo workflow
instance with extract→publish as nested DAG nodes, so a single entrypoint
matches the platform shape exactly.

Every field has a typed shape. ``connection`` uses :class:`ConnectionRef`
from the SDK; filter payloads are typed ``dict`` shapes from the apitree
widget routed through small contracts that the SDK payload-safety validator
accepts.
"""

from __future__ import annotations

from typing import Annotated, Any

from application_sdk.contracts.base import Input, Output
from application_sdk.contracts.types import ConnectionRef, FileReference, MaxItems
from application_sdk.credentials.ref import CredentialRef
from application_sdk.credentials.types import BasicCredential
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CredentialRef"]  # keep import live (annotations-only otherwise)

# ---------------------------------------------------------------------------
# Typed credential
# ---------------------------------------------------------------------------


class MetabaseCredential(BasicCredential, frozen=True):
    """Username + password credential plus Metabase host/port.

    ``host`` is stored with its protocol prefix (e.g.
    ``https://acme.metabaseapp.com``) — the v2 ``restCredentialTemplate``
    writes the URL as ``{{host}}:{{port}}/...`` without prepending a scheme,
    and the e2e Docker pipeline targets ``http://localhost:3000``.
    """

    model_config = ConfigDict(frozen=True)

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

    The widget posts an empty object per id (``{"<id>": {}}``). Frozen
    pydantic model with no required fields to satisfy the SDK payload-safety
    validator while accepting the wire shape.
    """

    model_config = ConfigDict(frozen=True, extra="allow")


# Bounded mapping of collection-id → selection. 1000 matches the
# ``AppInputContract`` default in ``app/generated/_input.py``.
CollectionFilter = Annotated[dict[str, CollectionSelection], MaxItems(1000)]


# ---------------------------------------------------------------------------
# App-level: single run() entrypoint
# ---------------------------------------------------------------------------


class MetabaseInput(Input, allow_unbounded_fields=True):
    """Top-level input for ``MetabaseApp.run()``.

    Credentials can arrive via three paths (mirrors sigma/looker pattern):
      1. ``metabase_credential`` (CredentialRef) — v3 PKL contract path.
      2. ``credential_guid`` (str) — legacy GUID, resolved from secret store.
      3. ``credentials`` (list[{key,value}] or dict) — inline local-dev path.

    Collapses the v2 ``ExtractionInput`` + ``TransformInput`` into one
    contract. ``processed_data_path`` is retained for the rare case where
    transform is re-run against a pre-existing ``processed/`` tree (e.g. for
    debugging); when empty it defaults to ``output_path``.

    ``allow_unbounded_fields`` is required because the AE-side payload
    contains nested dicts (connection.attributes.*, include-collections.*)
    that the payload-safety validator can't bound.
    """

    workflow_id: str = ""
    credential_guid: str = ""
    extraction_method: str = "direct"
    agent_json: str = ""

    metabase_credential: CredentialRef | None = None
    credentials: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    connection: ConnectionRef = Field(default_factory=ConnectionRef)

    include_collections: CollectionFilter = Field(default_factory=dict)
    exclude_collections: CollectionFilter = Field(default_factory=dict)

    output_path: str = ""
    output_prefix: str = ""
    processed_data_path: str = ""
    chunk_start: int = 0


class MetabaseOutput(Output):
    """Top-level output from ``MetabaseApp.run()``.

    ``transformed_data_prefix`` is read by downstream AE nodes via JSONPath;
    it points at the object-store key under which the ``transformed/`` tree
    was uploaded.
    """

    transformed_data_prefix: str = ""
    connection_qualified_name: str = ""
    output_path: str = ""
    total_records: int = 0


# ---------------------------------------------------------------------------
# Per-@task contracts
# ---------------------------------------------------------------------------


class FetchInput(Input, allow_unbounded_fields=True):
    """Input shared by all simple extract @tasks.

    Carries the credential ref (or inline credentials) so the task can
    rebuild its own client. Each ``@task`` runs as its own Temporal activity
    in a separate worker, so a shared client cache via ``app_state`` would
    only be reachable inside one activity context.
    """

    output_path: str = ""
    credential_ref: CredentialRef | None = None
    inline_credentials: dict[str, Any] = Field(default_factory=dict)


class FetchOutput(Output):
    """Output for an extract @task that wrote a single JSONL file."""

    typename: str = ""
    record_count: int = 0
    output_file: FileReference | None = None


class FilterInput(Input, allow_unbounded_fields=True):
    """Input for the filter @task.

    Receives ``FileReference``s for each of the four raw entity files and
    the include/exclude collection filters. The SDK auto-downloads any
    ``FileReference`` referenced here before the task runs.
    """

    output_path: str = ""
    include_collections: CollectionFilter = Field(default_factory=dict)
    exclude_collections: CollectionFilter = Field(default_factory=dict)
    collections_file: FileReference | None = None
    dashboards_file: FileReference | None = None
    questions_file: FileReference | None = None
    databases_file: FileReference | None = None
    credential_ref: CredentialRef | None = None
    inline_credentials: dict[str, Any] = Field(default_factory=dict)


class FilterOutput(Output):
    """Output for the filter @task — four filtered JSONL files."""

    collections_filtered_file: FileReference | None = None
    dashboards_filtered_file: FileReference | None = None
    questions_filtered_file: FileReference | None = None
    databases_filtered_file: FileReference | None = None
    total_records: int = 0


class FetchDetailInput(Input, allow_unbounded_fields=True):
    """Input for tasks that fetch per-entity detail from a filtered file."""

    output_path: str = ""
    source_file: FileReference | None = None
    credential_ref: CredentialRef | None = None
    inline_credentials: dict[str, Any] = Field(default_factory=dict)


class ProcessInput(Input, allow_unbounded_fields=True):
    """Input for the ``process_metabaseprocess`` @task.

    ``metabase_host`` is resolved inside the task itself via
    ``_build_client(input).host`` so the credential path (CredentialRef vs
    inline) stays consistent with every other task.
    """

    output_path: str = ""
    collections_filtered_file: FileReference | None = None
    databases_filtered_file: FileReference | None = None
    question_queries_file: FileReference | None = None
    dashboard_details_file: FileReference | None = None
    questions_filtered_file: FileReference | None = None
    credential_ref: CredentialRef | None = None
    inline_credentials: dict[str, Any] = Field(default_factory=dict)


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
#
# Process + ColumnProcess (SQL-parsed lineage) are produced by the
# QueryIntelligence app downstream — see ``contract/app.pkl`` ``extraNodes``.
# This connector emits only the 4 entity types it owns; QI consumes
# ``attributes.metabaseQuery`` / ``attributes.metabaseSourceDatabaseName`` /
# ``attributes.metabaseSourceSchemaName`` from the transformed
# MetabaseQuestion output to build lineage.
TYPENAME_TO_PROCESS_DIR: dict[str, str] = {
    "METABASECOLLECTION": "collections",
    "METABASEDASHBOARD": "dashboards",
    "METABASEQUESTION": "questions",
    "BIPROCESS": "questions_dashboards",
}

TRANSFORM_ASSET_TYPES: list[str] = list(TYPENAME_TO_PROCESS_DIR.keys())


_ = Any
