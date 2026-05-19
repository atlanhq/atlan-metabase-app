"""Integration-test bootstrap.

1. Load ``tests/.env`` + repo-root ``.env`` at collection time so module-level
   credential defaults see ``E2E_METABASE_*`` values before the class body runs.
   ``BaseIntegrationTest.setup_class`` also calls ``load_dotenv()``; this just
   makes it available earlier — the second call is a no-op.
2. Register extra pandera check methods used by the tightened raw/*.yaml
   schemas (upper-bound row count + equality). The SDK only ships
   ``check_record_count_ge``; we add ``_le`` here so per-scenario schemas
   can express exact or bounded row counts.
"""

from pathlib import Path

import pandas as pd
import pandera.extensions as extensions
from dotenv import load_dotenv

_TESTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TESTS_DIR.parent

for path in (_REPO_ROOT / ".env", _TESTS_DIR / ".env"):
    if path.exists():
        load_dotenv(path, override=False)


# ── Additional pandera check methods ──────────────────────────────────
# Registered at module import time so pandera's YAML loader can resolve
# them when building schemas from tests/integration/schema/**/*.yaml.


@extensions.register_check_method(statistics=["expected_record_count"])  # type: ignore[misc]
def check_record_count_le(df: pd.DataFrame, *, expected_record_count: int) -> bool:
    """Validate the DataFrame has at most ``expected_record_count`` rows.

    Pairs with the SDK's ``check_record_count_ge`` to express bounded or
    exact row counts. Exact count = both ge and le set to the same value.
    """
    if df.shape[0] <= expected_record_count:
        return True
    raise ValueError(
        f"Expected record count <= {expected_record_count}, got: {df.shape[0]}"
    )
