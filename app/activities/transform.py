import os
from typing import Any, Dict, Type, cast

import daft
from application_sdk.activities.common.models import ActivityStatistics
from application_sdk.activities.common.utils import auto_heartbeater
from application_sdk.activities.metadata_extraction.base import (
    BaseMetadataExtractionActivities,
    BaseMetadataExtractionActivitiesState,
)
from application_sdk.common.types import DataframeType
from application_sdk.io.json import JsonFileReader, JsonFileWriter
from application_sdk.observability.logger_adaptor import get_logger
from temporalio import activity

from app.client import MetabaseApiClient
from app.handler import MetabaseHandler
from app.transformers import MetabaseTransformer

logger = get_logger(__name__)
activity.logger = logger

# ---------------------------------------------------------------------------
# Mapping from transformer typename to subdirectory in processed_data_path
# ---------------------------------------------------------------------------
TYPENAME_TO_PROCESS_DIR: Dict[str, str] = {
    "METABASECOLLECTION": "collections",
    "METABASEDASHBOARD": "dashboards",
    "METABASEQUESTION": "questions",
    "BIPROCESS": "questions_dashboards",
    "PROCESS": "processes",
    "COLUMNPROCESS": "column_processes",
}

TRANSFORM_ASSET_TYPES = list(TYPENAME_TO_PROCESS_DIR.keys())


class MetabaseTransformActivities(BaseMetadataExtractionActivities):
    """Temporal activities for Metabase Workflow 2 — transform only.

    Reads processed NDJSON output from Workflow 1 (and the optional Argo
    parse-lineage step), transforms each entity type using
    :class:`~app.transformers.MetabaseTransformer`, and writes Atlas JSON
    to the ``transformed/`` directory.
    """

    def __init__(
        self,
        client_class: Type[MetabaseApiClient] | None = None,
        handler_class: Type[MetabaseHandler] | None = None,
        transformer_class: Type[MetabaseTransformer] | None = None,
    ):
        """Initialize with optional custom component classes.

        Args:
            client_class: Custom ``MetabaseApiClient`` subclass, or ``None`` for default.
            handler_class: Custom ``MetabaseHandler`` subclass, or ``None`` for default.
            transformer_class: Custom transformer class, or ``None`` for default.
        """
        super().__init__(
            client_class=client_class or MetabaseApiClient,
            handler_class=handler_class or MetabaseHandler,
            transformer_class=transformer_class or MetabaseTransformer,
        )

    # =========================================================================
    # SECTION 1 — STATE HELPERS
    # =========================================================================

    async def _set_state(self, workflow_args: Dict[str, Any]) -> None:
        """Initialize workflow state with Metabase client, handler, and transformer.

        Args:
            workflow_args: Workflow arguments dict.
        """
        from application_sdk.activities.common.utils import get_workflow_id

        workflow_id = get_workflow_id()
        if not self._state.get(workflow_id):
            self._state[workflow_id] = BaseMetadataExtractionActivitiesState()

        await super()._set_state(workflow_args)

        logger.info(
            "MetabaseTransformActivities: state initialised for workflow %s",
            workflow_id,
        )

    # =========================================================================
    # SECTION 2 — WORKFLOW ARGS (renamed to avoid Temporal name collision)
    # =========================================================================

    @auto_heartbeater
    @activity.defn(name="transform_get_workflow_args")
    async def get_workflow_args(
        self, workflow_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Retrieve workflow configuration for the transform workflow.

        Uses the inherited SDK implementation but registers under the name
        ``transform_get_workflow_args`` to avoid a Temporal activity name
        conflict with the extraction workflow's ``get_workflow_args``.

        Args:
            workflow_config: Dict containing ``workflow_id``.

        Returns:
            Dict with the complete workflow configuration including
            ``processed_data_path``.
        """
        return await super().get_workflow_args(workflow_config)

    # =========================================================================
    # SECTION 3 — TRANSFORM ACTIVITY
    # =========================================================================

    @auto_heartbeater
    @activity.defn
    async def transform_data(self, workflow_args: Dict[str, Any]) -> ActivityStatistics:
        """Transform processed Metabase NDJSON data into Atlas JSON for one asset type.

        Reads JSON from ``<processed_data_path>/<subdir>/`` (where *subdir*
        comes from :data:`TYPENAME_TO_PROCESS_DIR`), applies
        :class:`~app.transformers.MetabaseTransformer`, and writes the
        transformed Atlas entities to ``<output_path>/transformed/``.

        Args:
            workflow_args: Workflow arguments dict.  Required keys:

                - ``output_path`` (str): Base output directory for this run.
                - ``processed_data_path`` (str): Root of processed NDJSON output
                  from Workflow 1 / Argo.  Defaults to ``output_path`` if absent.
                - ``typename`` (str): Uppercase entity type, e.g. ``"COLLECTION"``.
                - ``connection`` (dict): Must include ``connection_qualified_name``
                  and ``connection_name``.

        Returns:
            :class:`ActivityStatistics` from :class:`~application_sdk.io.json.JsonFileWriter`.

        Raises:
            ValueError: If required keys are missing or the transformer is absent.
        """
        try:
            state = await self._get_state(workflow_args)

            if not state.transformer:
                raise ValueError("transform_data: Transformer not found in state")

            output_path: str = workflow_args.get("output_path", "")
            if not output_path:
                raise ValueError(
                    "transform_data: 'output_path' is required in workflow_args"
                )

            typename: str = workflow_args.get("typename", "")
            if not typename:
                raise ValueError(
                    "transform_data: 'typename' is required in workflow_args"
                )

            typename_upper = typename.upper()

            # Determine the subdirectory for the processed input files.
            # processed_data_path may differ from output_path when the Argo
            # process-lineage step writes to a separate location.
            processed_data_path: str = workflow_args.get(
                "processed_data_path", output_path
            )

            subdir = TYPENAME_TO_PROCESS_DIR.get(typename_upper, typename.lower())
            input_dir = os.path.join(processed_data_path, "processed", subdir)

            logger.info(
                "transform_data: typename=%s, input_dir=%s", typename_upper, input_dir
            )

            # Extract connection info from workflow_args for the transformer.
            connection: Dict[str, Any] = workflow_args.get("connection", {}) or {}
            connection_qualified_name: str = connection.get(
                "connection_qualified_name", ""
            )
            connection_name: str = connection.get("connection_name", "")

            workflow_id: str = workflow_args.get("workflow_id", "")
            workflow_run_id: str = workflow_args.get("workflow_run_id", "")

            # Build transform kwargs consumed by QueryBasedTransformer.
            transform_kwargs = {
                **workflow_args,
                "connection_name": connection_name,
                "connection_qualified_name": connection_qualified_name,
            }

            # Set up JSON reader for processed input data.
            reader = JsonFileReader(
                path=input_dir,
                dataframe_type=DataframeType.daft,
            )

            # Set up JSON writer for transformed Atlas output.
            chunk_start: int | None = workflow_args.get("chunk_start")
            json_writer = JsonFileWriter(
                path=os.path.join(output_path, "transformed"),
                typename=typename_upper,
                chunk_start=chunk_start,
                dataframe_type=DataframeType.daft,
            )

            # Process each batch of input data.
            async for _dataframe in reader.read_batches():
                dataframe = cast(daft.DataFrame, _dataframe)
                if dataframe is not None and dataframe.count_rows() > 0:
                    transformed_df = state.transformer.transform_metadata(
                        typename=typename_upper,
                        dataframe=dataframe,
                        workflow_id=workflow_id,
                        workflow_run_id=workflow_run_id,
                        **transform_kwargs,
                    )
                    if transformed_df is not None:
                        await json_writer.write(transformed_df)

            await reader.close()
            stats = await json_writer.close()

            logger.info(
                "transform_data complete: typename=%s, records=%d",
                typename_upper,
                stats.total_record_count if stats else 0,
            )
            return stats

        except Exception as e:
            logger.error("transform_data failed: %s", str(e), exc_info=e)
            raise
