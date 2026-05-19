import asyncio

from application_sdk.main import run_dev_combined
from application_sdk.observability.logger_adaptor import get_logger

from app.connector import MetabaseApp
from app.handler import MetabaseHandler  # noqa: F401 — registers handler with SDK

logger = get_logger(__name__)


if __name__ == "__main__":
    asyncio.run(run_dev_combined(MetabaseApp))
