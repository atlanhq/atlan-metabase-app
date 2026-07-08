"""Typed Input/Output contracts for the Metabase v3 connector.

Defines the credential model, the single-``run()`` entrypoint contract, and
the per-``@task`` contracts.

Architecture mirrors ``atlan-openapi-app``: one App class with a single
``run()`` override that orchestrates ``@task`` methods sequentially. The
platform dispatches Metabase as **one** workflow instance with extract→publish
as nested DAG nodes, so a single entrypoint matches the platform shape exactly.

Every field has a typed shape. ``connection`` uses :class:`ConnectionRef`
from the SDK; filter payloads are typed ``dict`` shapes from the apitree
widget routed through small contracts that the SDK payload-safety validator
accepts.
"""

from __future__ import annotations

from typing import Annotated, Any

import orjson
from application_sdk.contracts.base import Input, Output
from application_sdk.contracts.types import ConnectionRef, FileReference, MaxItems
from application_sdk.credentials.ref import CredentialRef
from application_sdk.credentials.spec import AgentCredentialSpec
from application_sdk.observability.logger_adaptor import get_logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.credentials import MetabaseCredential

_logger = get_logger(__name__)


def _coerce_collection_filter(value: Any) -> Any:
    """Recover from upstream object→string serialization for collection filters.

    The frontend → AE → SDK pipeline can hand us the literal string
    ``"[object Object]"`` for ``include_collections`` / ``exclude_collections``
    when the form widget's value is a non-plain JS object that gets passed
    through ``String(value)`` in the manifest substitution layer instead of
    ``JSON.stringify(value)``. The two filters have *identical* contract
    shape (same APITree widget, same ``default: {}``, same wiring), so this
    is value-side, not contract-side.

    Coerce the known-bad sentinel and JSON-encoded variants back to a dict
    so the workflow doesn't fail validation on an upstream defect. Anything
    else passes through and pydantic decides.
    """
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == "[object Object]":
            _logger.warning(
                "Coerced stringified collection filter to empty dict "
                "(upstream serializer emitted %r). This is a workaround "
                "for a frontend/AE substitution bug — see contracts.py.",
                value,
            )
            return {}
        try:
            parsed = orjson.loads(stripped)
        except orjson.JSONDecodeError:
            _logger.warning(
                "Collection filter %r is not valid JSON; passing through as-is",
                value,
                exc_info=True,
            )
            return value
        if isinstance(parsed, dict):
            return parsed
    return value


__all__ = ["CredentialRef", "MetabaseCredential"]


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
# Bounded credential-bag shapes — inline/local-dev credential channels carry
# a handful of scalar key-value pairs (host, port, username, password, …),
# never hundreds+.  500 keys / 50 list entries is comfortably above any real
# credential shape while still satisfying the SDK payload-safety validator
# (a bare ``dict[str, Any]`` is unconditionally forbidden — ``Any`` itself is
# rejected regardless of MaxItems — so the value type is narrowed to the
# scalar types a credential field actually holds).
# ---------------------------------------------------------------------------
CredentialValue = str | int | bool | None
BoundedCredentialDict = Annotated[dict[str, CredentialValue], MaxItems(500)]
BoundedCredentialList = Annotated[list[BoundedCredentialDict], MaxItems(50)]


# ---------------------------------------------------------------------------
# App-level: single run() entrypoint
# ---------------------------------------------------------------------------


# conformance: ignore[P001] 'credentials' is an entrypoint field (B005-guarded) — narrowing its dict value type away from Any to satisfy payload-safety bounding would be a breaking contract-type change; see BoundedCredentialDict's docstring note.
class MetabaseInput(Input, allow_unbounded_fields=True):
    """Input for the ``extract_metadata`` @entrypoint.

    Credentials can arrive via three paths (mirrors sigma/looker pattern):
      1. ``metabase_credential`` (CredentialRef) — v3 PKL contract path.
      2. ``credential_guid`` (str) — legacy GUID, resolved from secret store.
      3. ``credentials`` (list[{key,value}] or dict) — inline local-dev path.

    ``processed_data_path`` is retained for the rare case where transform
    is re-run against a pre-existing ``processed/`` tree (e.g. for
    debugging); when empty it defaults to ``output_path``.

    ``allow_unbounded_fields`` is required for ``credentials: list[dict[str,
    Any]] | dict[str, Any]`` — this is an ``@entrypoint`` contract, so B005
    (NonAdditiveContractChange) blocks narrowing an existing field's value
    type away from ``Any`` even just to satisfy payload-safety bounding
    (``Any`` is unconditionally forbidden regardless of ``MaxItems``). Unlike
    the ``@task``-only ``inline_credentials`` fields elsewhere in this module
    (safe to bound — task contracts aren't B005-guarded), this field cannot
    be narrowed in place; only a new, additively-added field could carry a
    bounded type.
    """

    workflow_id: str = ""
    credential_guid: str = ""
    extraction_method: str = "direct"
    # Typed envelope for agent-shape credential payloads (host, port,
    # agent-name, basic.username, basic.password, ...). Previously
    # declared as ``str = ""`` which silently swallowed agent-mode
    # credentials on the worker side — ``build_credential_ref()`` had no
    # branch for it, so AGENT-mode workflows fell through to inline=={}
    # and ``_build_client`` raised ``no credential_ref or inline_credentials``.
    # Typing it as :class:`AgentCredentialSpec` makes ``MetabaseInput``
    # satisfy the SDK's ``CredentialResolvable`` protocol so
    # :meth:`CredentialRef.resolve` routes both direct + agent without
    # custom code in this repo.
    agent_json: AgentCredentialSpec | None = None

    metabase_credential: CredentialRef | None = None
    credentials: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    connection: ConnectionRef = Field(default_factory=ConnectionRef)

    include_collections: CollectionFilter = Field(default_factory=dict)
    exclude_collections: CollectionFilter = Field(default_factory=dict)

    _coerce_collection_filters = field_validator(
        "include_collections", "exclude_collections", mode="before"
    )(_coerce_collection_filter)

    output_path: str = ""
    output_prefix: str = ""
    processed_data_path: str = ""
    chunk_start: int = 0


class MetabaseOutput(Output):
    """Output from the ``extract_metadata`` @entrypoint.

    Field naming matches what the platform's PublishNode, QueryIntelligenceNode
    and LineagePublishNode JSONPath-thread off of:

    - ``transformed_data_prefix`` — where the ``transformed/`` tree was
      uploaded; read by the QI node (``inputPrefix``) and the PublishNode
      (``transformed_data_prefix``).
    - ``connection_qualified_name`` — read by every downstream node that
      needs to scope state buckets per connection.
    - ``view_lineage_output_prefix`` — where the QI node will WRITE its
      parsed-SQL output. Forwarded as input to ``extract_lineage``.
    - ``publish_state_prefix`` / ``current_state_prefix`` — read by
      PublishNode; auto-derived under ``persistent-artifacts/`` and
      ``argo-artifacts/`` respectively.
    - ``lineage_publish_state_prefix`` / ``lineage_current_state_prefix``
      — read by LineagePublishNode; same pattern but scoped under
      ``…/lineage/``.
    - ``residual_failures`` — set only when at least one tolerated
      extract failure was recorded (see ``app/residuals.py``); a durable
      (``RETAINED`` tier) reference to the uploaded ``residual/`` directory,
      so tolerated failures survive pod teardown and are reviewable after
      the run instead of being stranded on ephemeral local disk.
    """

    transformed_data_prefix: str = ""
    connection_qualified_name: str = ""
    output_path: str = ""
    view_lineage_output_prefix: str = ""
    publish_state_prefix: str = ""
    current_state_prefix: str = ""
    lineage_publish_state_prefix: str = ""
    lineage_current_state_prefix: str = ""
    lineage_stage_prefix: str = ""
    total_records: int = 0
    residual_failures: FileReference | None = None


# ---------------------------------------------------------------------------
# extract_lineage @entrypoint contracts
# ---------------------------------------------------------------------------


class MetabaseLineageInput(Input):
    """Input for the ``extract_lineage`` @entrypoint.

    Reads the QueryIntelligence app's parsed-SQL output (NDJSON or parquet)
    from ``view_lineage_input_prefix`` and produces Process + ColumnProcess
    NDJSON records at ``lineage_stage_prefix`` for LineagePublishNode to
    consume.

    Threaded by JSONPath from the metadata entrypoint's output via
    ``$.extract.outputs.view_lineage_output_prefix`` (QI's output) and
    ``$.extract.outputs.connection_qualified_name``.
    """

    workflow_id: str = ""
    connection: ConnectionRef = Field(default_factory=ConnectionRef)
    connection_qualified_name: str = ""

    # QI app writes its parsed-SQL output here; we read it.
    view_lineage_input_prefix: str = ""

    # Where to write the Process / ColumnProcess NDJSON.
    output_path: str = ""
    output_prefix: str = ""


class MetabaseLineageOutput(Output):
    """Output from the ``extract_lineage`` @entrypoint.

    Field names match what the LineagePublishNode JSONPath-threads off of:
    - ``lineage_stage_prefix`` — where the NDJSON output lives.
    - ``connection_qualified_name`` — for state-bucket scoping.
    - ``lineage_publish_state_prefix`` / ``lineage_current_state_prefix`` —
      auto-derived blue-green cache paths scoped to lineage.
    """

    lineage_stage_prefix: str = ""
    connection_qualified_name: str = ""
    lineage_publish_state_prefix: str = ""
    lineage_current_state_prefix: str = ""
    process_count: int = 0
    column_process_count: int = 0


# ---------------------------------------------------------------------------
# Per-@task contracts
# ---------------------------------------------------------------------------


class FetchInput(Input):
    """Input shared by all simple extract @tasks.

    Carries the credential ref (or inline credentials) so the task can
    rebuild its own client. Each ``@task`` runs as its own Temporal activity
    in a separate worker, so a shared client cache via ``app_state`` would
    only be reachable inside one activity context.
    """

    output_path: str = ""
    credential_ref: CredentialRef | None = None
    inline_credentials: BoundedCredentialDict = Field(default_factory=dict)


class FetchOutput(Output):
    """Output for an extract @task that wrote a single JSONL file."""

    typename: str = ""
    record_count: int = 0
    output_file: FileReference | None = None


class FilterInput(Input):
    """Input for the filter @task.

    Receives ``FileReference``s for each of the four raw entity files and
    the include/exclude collection filters. The SDK auto-downloads any
    ``FileReference`` referenced here before the task runs.
    """

    output_path: str = ""
    include_collections: CollectionFilter = Field(default_factory=dict)
    exclude_collections: CollectionFilter = Field(default_factory=dict)

    _coerce_collection_filters = field_validator(
        "include_collections", "exclude_collections", mode="before"
    )(_coerce_collection_filter)

    collections_file: FileReference | None = None
    dashboards_file: FileReference | None = None
    questions_file: FileReference | None = None
    databases_file: FileReference | None = None
    credential_ref: CredentialRef | None = None
    inline_credentials: BoundedCredentialDict = Field(default_factory=dict)


class FilterOutput(Output):
    """Output for the filter @task — four filtered JSONL files."""

    collections_filtered_file: FileReference | None = None
    dashboards_filtered_file: FileReference | None = None
    questions_filtered_file: FileReference | None = None
    databases_filtered_file: FileReference | None = None
    total_records: int = 0


class FetchDetailInput(Input):
    """Input for tasks that fetch per-entity detail from a filtered file."""

    output_path: str = ""
    source_file: FileReference | None = None
    credential_ref: CredentialRef | None = None
    inline_credentials: BoundedCredentialDict = Field(default_factory=dict)


class ProcessInput(Input):
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
    inline_credentials: BoundedCredentialDict = Field(default_factory=dict)
    connection_qualified_name: str = ""


class ProcessOutput(Output):
    """Output for the process @task — four enriched JSONL files plus stats."""

    collections_processed_file: FileReference | None = None
    dashboards_processed_file: FileReference | None = None
    questions_processed_file: FileReference | None = None
    questions_dashboards_processed_file: FileReference | None = None
    total_records: int = 0


class BuildLineageInput(Input):
    """Input for ``build_lineage_records`` — the file-I/O half of extract_lineage.

    Lives in a ``@task`` (not the entrypoint) so file reads/writes happen in
    an activity rather than inside Temporal's workflow sandbox, which blocks
    built-in ``open()``.
    """

    output_path: str = ""
    # Local directory holding QI parsed-SQL NDJSON (already downloaded from
    # ``view_lineage_input_prefix`` by the entrypoint).
    qi_local_path: str = ""
    connection_qualified_name: str = ""
    connection_name: str = ""


class BuildLineageOutput(Output):
    """Output for ``build_lineage_records``."""

    stage_dir: str = ""
    process_count: int = 0
    column_process_count: int = 0


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
