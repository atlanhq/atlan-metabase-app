"""Pydantic models for the Metabase connector."""

from typing import Any, Dict, Optional, Union

from pydantic import BaseModel


class MetabaseCredentials(BaseModel):
    """Metabase connection credentials parsed from the Atlan credential store.

    Fields mirror the ``{{variables}}`` present in the ``restCredentialTemplate``
    curl from the Metabase configmap:

        POST '{{host}}:{{port}}/api/session'
        body: {"username": "{{username}}", "password": "{{password}}"}

    ``host`` is stored **with** the protocol prefix (e.g.
    ``https://myinstance.metabaseapp.com``) because the curl URL is written as
    ``{{host}}:{{port}}/...`` without a leading scheme.
    """

    host: str
    port: int = 443
    username: Optional[str] = None
    password: Optional[str] = None
    extra: Union[str, dict] = {}

    model_config = {"extra": "allow"}


# ============================================================================
# Preflight Check Models
# ============================================================================


class PreflightCheckResult(BaseModel):
    """Result of a single preflight validation check."""

    success: bool
    successMessage: str = ""
    failureMessage: str = ""


class PreflightCheckResults(BaseModel):
    """Container for all Metabase preflight validation check results.

    Fields correspond directly to the keys in the ``sageTemplate`` section of
    the Metabase configmap (``atlan-connectors-metabase``):

    - ``collectionCountCheck``: Counts non-personal collections that match the
      include/exclude filter selections.
    - ``dashboardCountCheck``: Counts dashboards whose collection is not personal
      and matches the include/exclude filters.
    - ``questionCountCheck``: Counts questions (cards) whose collection is not
      personal and matches the include/exclude filters.
    - ``nativeQueryPermissionCheck``: Verifies that every connected database has
      ``native_permissions == "write"`` (required for SQL-based extraction).
    """

    collectionCountCheck: Optional[PreflightCheckResult] = None
    dashboardCountCheck: Optional[PreflightCheckResult] = None
    questionCountCheck: Optional[PreflightCheckResult] = None
    nativeQueryPermissionCheck: Optional[PreflightCheckResult] = None


# ============================================================================
# Workflow Args Model
# ============================================================================


class WorkflowArgs(BaseModel):
    """Workflow arguments model for the Metabase connector.

    Carries all runtime parameters needed across activities in both
    Workflow 1 (extraction) and Workflow 2 (transform).

    Attributes:
        workflow_id: Temporal workflow identifier.
        workflow_run_id: Temporal workflow run identifier.
        output_path: Absolute path to the base output directory for this run.
        output_prefix: Prefix/root under which ``output_path`` was constructed.
        processed_data_path: Path where Workflow 1 wrote processed NDJSON files.
            When provided to Workflow 2, it may differ from ``output_path``
            (e.g., when Argo's process-lineage step writes to a separate location).
            Defaults to ``output_path`` when ``None``.
        typename: Entity type being processed, e.g. ``"COLLECTION"``.
        chunk_start: Starting chunk index for the JSON writer (used when
            resuming or parallelising transform activities).
        credentials: Raw credential dict from the Atlan secret store.
        metadata: Workflow metadata dict (include/exclude filters, etc.).
        connection: Connection info dict with ``connection_qualified_name``
            and ``connection_name``.
    """

    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    output_path: Optional[str] = None
    output_prefix: Optional[str] = None
    processed_data_path: Optional[str] = None
    typename: Optional[str] = None
    chunk_start: Optional[int] = None
    credentials: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    connection: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}
