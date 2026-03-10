from typing import Any, Dict, Type

from application_sdk.constants import APPLICATION_NAME
from application_sdk.workflows import WorkflowInterface
from temporalio import workflow
from temporalio.common import RetryPolicy

from app.activities.transform import TRANSFORM_ASSET_TYPES, MetabaseTransformActivities

logger = workflow.logger


@workflow.defn
class MetabaseTransformWorkflow(WorkflowInterface):
    """Temporal Workflow 2 — orchestrates Metabase data transformation only.

    Reads processed NDJSON files produced by Workflow 1 (and optionally the
    Argo parse-lineage step), transforms each entity type using
    :class:`~app.transformers.MetabaseTransformer`, and writes Atlas JSON to
    the ``transformed/`` output directory.

    Entity types transformed (in order):

    - ``COLLECTION``
    - ``DASHBOARD``
    - ``QUESTION``
    - ``QUESTION_DASHBOARD``
    - ``PROCESS``
    - ``COLUMN_PROCESS``

    The workflow does **not** run extraction, preflight checks, or Atlan upload.
    Upload is expected to be triggered separately or by extending this workflow.
    """

    activities_cls: Type[MetabaseTransformActivities] = MetabaseTransformActivities
    application_name: str = APPLICATION_NAME

    @workflow.run
    async def run(self, workflow_config: Dict[str, Any]) -> None:
        """Execute the Metabase transform pipeline for all entity types.

        Args:
            workflow_config: Dict with ``workflow_id`` from the frontend.
        """
        logger.info("MetabaseTransformWorkflow: starting")
        logger.info(f"Workflow config received: {workflow_config}")

        try:
            workflow_run_id = workflow.info().run_id
            retry_policy = RetryPolicy(maximum_attempts=2, backoff_coefficient=2)
            activities_instance = self.activities_cls()

            # ------------------------------------------------------------------
            # STEP 1: Fetch workflow configuration from state store.
            # ------------------------------------------------------------------
            logger.info("Executing transform_get_workflow_args")
            workflow_args: Dict[str, Any] = await workflow.execute_activity_method(
                activities_instance.get_workflow_args,
                args=[workflow_config],
                retry_policy=RetryPolicy(maximum_attempts=3, backoff_coefficient=2),
                start_to_close_timeout=self.default_start_to_close_timeout,
                heartbeat_timeout=self.default_heartbeat_timeout,
            )
            workflow_args["workflow_run_id"] = workflow_run_id

            # ------------------------------------------------------------------
            # STEP 2: Transform each entity type sequentially.
            # ------------------------------------------------------------------
            for typename in TRANSFORM_ASSET_TYPES:
                logger.info(f"Transforming entity type: {typename}")
                transform_args = {**workflow_args, "typename": typename}

                try:
                    stats = await workflow.execute_activity_method(
                        activities_instance.transform_data,
                        args=[transform_args],
                        retry_policy=retry_policy,
                        start_to_close_timeout=self.default_start_to_close_timeout,
                        heartbeat_timeout=self.default_heartbeat_timeout,
                    )
                    logger.info(
                        f"Transform complete for {typename}: records={stats.total_record_count if stats else 0}"
                    )
                except Exception as exc:
                    logger.error(
                        f"Failed to transform {typename}: {str(exc)}", exc_info=True
                    )
                    raise

            logger.info("MetabaseTransformWorkflow: all entity types transformed")

        except Exception as e:
            logger.error(f"MetabaseTransformWorkflow failed: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def get_activities(activities: MetabaseTransformActivities) -> list:
        """Return all activity methods for Temporal worker registration.

        Args:
            activities: Initialised activities instance.

        Returns:
            List of activity method references.
        """
        return [
            activities.get_workflow_args,
            activities.transform_data,
        ]
