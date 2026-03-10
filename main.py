import asyncio

from application_sdk.application import BaseApplication
from application_sdk.constants import APPLICATION_NAME
from application_sdk.observability.decorators.observability_decorator import (
    observability,
)
from application_sdk.observability.logger_adaptor import get_logger
from application_sdk.observability.metrics_adaptor import get_metrics
from application_sdk.observability.traces_adaptor import get_traces

from app.activities.metadata_extraction import MetabaseMetadataExtractionActivities
from app.activities.transform import MetabaseTransformActivities
from app.client import MetabaseApiClient
from app.handler import MetabaseHandler
from app.workflows.metadata_extraction import MetabaseMetadataExtractionWorkflow
from app.workflows.transform import MetabaseTransformWorkflow

logger = get_logger(__name__)
metrics = get_metrics()
traces = get_traces()


@observability(logger=logger, metrics=metrics, traces=traces)
async def main():
    application = BaseApplication(
        name=APPLICATION_NAME,
        client_class=MetabaseApiClient,
        handler_class=MetabaseHandler,
    )

    await application.setup_workflow(
        workflow_and_activities_classes=[
            (MetabaseMetadataExtractionWorkflow, MetabaseMetadataExtractionActivities),
            (MetabaseTransformWorkflow, MetabaseTransformActivities),
        ]
    )

    await application.start(
        workflow_class=MetabaseMetadataExtractionWorkflow, has_configmap=True
    )


if __name__ == "__main__":
    asyncio.run(main())
