import os
from typing import Any

from application_sdk.observability.logger_adaptor import get_logger
from application_sdk.transformers.common.utils import (
    get_yaml_query_template_path_mappings,
)
from application_sdk.transformers.query import QueryBasedTransformer

logger = get_logger(__name__)


class MetabaseTransformer(QueryBasedTransformer):
    """Transformer that converts raw Metabase parquet data to Atlas JSON using YAML templates.

    Reads raw parquet files from the ``raw/`` directory, applies YAML-defined
    mapping rules per asset type, and writes Atlas entity JSON to the
    ``transformed/`` directory.

    Note:
        Called from ``MetabaseMetadataExtractionActivities.transform_data``.
        Each YAML template defines ``typeName``, ``attributes``, and ``status``
        for one Atlas entity type.

        PROCESS and COLUMNPROCESS entities are lineage-only and produce no output
        unless Argo's ``process-lineage`` step runs (requires Gudusoft SQL parser).
    """

    def __init__(
        self,
        connector_name: str = "metabase",
        tenant_id: str = "default",
        **kwargs: Any,
    ):
        """Initialize the transformer and register YAML templates for each asset type.

        Args:
            connector_name: Connector identifier (default ``"metabase"``).
            tenant_id: Tenant identifier for multi-tenant deployments.
            **kwargs: Additional arguments forwarded to ``QueryBasedTransformer``.
        """
        super().__init__(connector_name=connector_name, tenant_id=tenant_id, **kwargs)

        transformer_dir = os.path.dirname(__file__)

        self.entity_class_definitions = get_yaml_query_template_path_mappings(
            transformer_dir,
            [
                "METABASECOLLECTION",
                "METABASEDASHBOARD",
                "METABASEQUESTION",
                "BIPROCESS",
                "PROCESS",  # lineage-only — no output unless SQL lineage is implemented
                "COLUMNPROCESS",  # lineage-only — no output unless SQL lineage is implemented
            ],
        )

        logger.info("Metabase transformer initialized with entity class definitions")
        logger.info(
            f"Supported asset types: {list(self.entity_class_definitions.keys())}"
        )
