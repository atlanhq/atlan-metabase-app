from typing import Any, Dict, List, Type

from application_sdk.constants import APPLICATION_NAME
from application_sdk.workflows.metadata_extraction import MetadataExtractionWorkflow
from temporalio import workflow
from temporalio.common import RetryPolicy

from app.activities.metadata_extraction import MetabaseMetadataExtractionActivities

logger = workflow.logger


@workflow.defn
class MetabaseMetadataExtractionWorkflow(MetadataExtractionWorkflow):
    """Temporal Workflow 1 — orchestrates Metabase metadata extraction only.

    Executes the full extraction pipeline in order:

    1. ``get_workflow_args`` — fetch workflow config from state store.
    2. ``preflight_check`` — validate credentials and counts.
    3. ``extract_collections`` — GET api/collection → raw/collections/
    4. ``extract_dashboards`` — GET api/dashboard → raw/dashboards/
    5. ``extract_questions`` — GET api/card → raw/questions/
    6. ``extract_databases`` — GET api/database → raw/databases/
    7. ``filter_data`` — apply include/exclude collection filters.
    8. ``extract_individual_dashboards`` — GET api/dashboard/<id> per dashboard.
    9. ``extract_individual_databases`` — GET api/database/<id>/metadata per DB.
    10. ``fetch_question_queries_activity`` — POST api/dataset/native per question.
    11. ``process_metabaseprocess`` — pure Python enrichment → processed/*.

    This workflow does **not** run ``transform_data`` or ``upload_to_atlan``.
    Transformation is handled by :class:`MetabaseTransformWorkflow` (Workflow 2).
    """

    activities_cls: Type[MetabaseMetadataExtractionActivities] = (
        MetabaseMetadataExtractionActivities
    )
    application_name: str = APPLICATION_NAME

    @workflow.run
    async def run(self, workflow_config: Dict[str, Any]) -> None:
        """Execute the Metabase metadata extraction pipeline.

        Args:
            workflow_config: Dict with ``workflow_id`` from the frontend.
        """
        logger.info("MetabaseMetadataExtractionWorkflow: starting")
        logger.info(f"Workflow config received: {workflow_config}")

        try:
            workflow_run_id = workflow.info().run_id
            workflow_args: Dict[str, Any] = {}

            retry_policy = RetryPolicy(maximum_attempts=2, backoff_coefficient=2)
            activities_instance = self.activities_cls()

            # ------------------------------------------------------------------
            # STEP 1: Fetch workflow configuration from state store.
            # ------------------------------------------------------------------
            logger.info("Executing get_workflow_args")
            workflow_args = await workflow.execute_activity_method(
                activities_instance.get_workflow_args,
                args=[workflow_config],
                retry_policy=RetryPolicy(maximum_attempts=3, backoff_coefficient=2),
                start_to_close_timeout=self.default_start_to_close_timeout,
                heartbeat_timeout=self.default_heartbeat_timeout,
            )
            workflow_args["workflow_run_id"] = workflow_run_id

            # ------------------------------------------------------------------
            # STEP 2: Preflight checks.
            # ------------------------------------------------------------------
            logger.info("Executing preflight_check")
            await workflow.execute_activity_method(
                activities_instance.preflight_check,
                args=[workflow_args],
                retry_policy=retry_policy,
                start_to_close_timeout=self.default_start_to_close_timeout,
                heartbeat_timeout=self.default_heartbeat_timeout,
            )

            # ------------------------------------------------------------------
            # STEPS 3–11: Run extraction activities in order.
            # ------------------------------------------------------------------
            for activity_name in self.get_asset_extraction_activities():
                logger.info(f"Executing activity: {activity_name}")
                activity_method = getattr(activities_instance, activity_name, None)
                if not activity_method:
                    logger.warning(f"Activity method not found: {activity_name}")
                    continue

                try:
                    stats = await workflow.execute_activity_method(
                        activity_method,
                        args=[workflow_args],
                        retry_policy=retry_policy,
                        start_to_close_timeout=self.default_start_to_close_timeout,
                        heartbeat_timeout=self.default_heartbeat_timeout,
                    )
                    logger.info(f"Activity {activity_name} complete: {stats}")
                except Exception as exc:
                    logger.error(
                        f"Activity {activity_name} failed: {str(exc)}", exc_info=True
                    )
                    raise

            logger.info(
                "MetabaseMetadataExtractionWorkflow: all extraction steps complete"
            )

        except Exception as e:
            logger.error(
                f"MetabaseMetadataExtractionWorkflow failed: {str(e)}", exc_info=True
            )
            raise

    @staticmethod
    def get_activities(
        activities: MetabaseMetadataExtractionActivities,
    ) -> list:
        """Return all activity methods for Temporal worker registration.

        Args:
            activities: Initialised activities instance.

        Returns:
            List of activity method references (used for worker registration,
            not execution ordering).
        """
        return [
            activities.get_workflow_args,
            activities.preflight_check,
            activities.extract_collections,
            activities.extract_dashboards,
            activities.extract_questions,
            activities.extract_databases,
            activities.filter_data,
            activities.extract_individual_dashboards,
            activities.extract_individual_databases,
            activities.fetch_question_queries_activity,
            activities.process_metabaseprocess,
        ]

    @staticmethod
    def get_asset_extraction_activities() -> List[str]:
        """Return the ordered list of asset extraction activity method names.

        This defines the execution order for Steps 3–11 in the workflow run.

        Returns:
            List of activity method name strings in execution order.
        """
        return [
            "extract_collections",
            "extract_dashboards",
            "extract_questions",
            "extract_databases",
            "filter_data",
            "extract_individual_dashboards",
            "extract_individual_databases",
            "fetch_question_queries_activity",
            "process_metabaseprocess",
        ]
