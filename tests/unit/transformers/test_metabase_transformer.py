"""Unit tests for app.transformers MetabaseTransformer and YAML templates."""

import os

import pytest
import yaml

TRANSFORMER_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "app", "transformers"
)


# =============================================================================
# YAML file existence
# =============================================================================


class TestYamlFilesExist:
    """Assert that all required YAML template files are present on disk."""

    def test_collection_yaml_exists(self):
        assert os.path.isfile(os.path.join(TRANSFORMER_DIR, "metabasecollection.yaml"))

    def test_dashboard_yaml_exists(self):
        assert os.path.isfile(os.path.join(TRANSFORMER_DIR, "metabasedashboard.yaml"))

    def test_question_yaml_exists(self):
        assert os.path.isfile(os.path.join(TRANSFORMER_DIR, "metabasequestion.yaml"))

    def test_biprocess_yaml_exists(self):
        assert os.path.isfile(os.path.join(TRANSFORMER_DIR, "biprocess.yaml"))

    def test_process_yaml_exists(self):
        assert os.path.isfile(os.path.join(TRANSFORMER_DIR, "process.yaml"))

    def test_column_process_yaml_exists(self):
        assert os.path.isfile(os.path.join(TRANSFORMER_DIR, "columnprocess.yaml"))


# =============================================================================
# YAML structure: each file must have 'table' and 'columns' top-level keys
# =============================================================================


def _load_yaml(filename: str) -> dict:
    path = os.path.join(TRANSFORMER_DIR, filename)
    with open(path) as f:
        return yaml.safe_load(f)


class TestYamlTopLevelKeys:
    """Each YAML file must declare both 'table' and 'columns' at the top level."""

    @pytest.mark.parametrize(
        "filename",
        [
            "metabasecollection.yaml",
            "metabasedashboard.yaml",
            "metabasequestion.yaml",
            "biprocess.yaml",
            "process.yaml",
            "columnprocess.yaml",
        ],
    )
    def test_yaml_has_table_key(self, filename):
        data = _load_yaml(filename)
        assert "table" in data, f"{filename} is missing 'table' key"

    @pytest.mark.parametrize(
        "filename",
        [
            "metabasecollection.yaml",
            "metabasedashboard.yaml",
            "metabasequestion.yaml",
            "biprocess.yaml",
            "process.yaml",
            "columnprocess.yaml",
        ],
    )
    def test_yaml_has_columns_key(self, filename):
        data = _load_yaml(filename)
        assert "columns" in data, f"{filename} is missing 'columns' key"


# =============================================================================
# qualifiedName uses concat( in its source_query
# =============================================================================


class TestQualifiedNameUsesConcat:
    """qualifiedName in every YAML must use concat() for qualified name construction."""

    def _get_qualified_name_source_query(self, filename: str) -> str:
        data = _load_yaml(filename)
        return (
            data.get("columns", {})
            .get("attributes", {})
            .get("qualifiedName", {})
            .get("source_query", "")
        )

    @pytest.mark.parametrize(
        "filename",
        [
            "metabasecollection.yaml",
            "metabasedashboard.yaml",
            "metabasequestion.yaml",
            "biprocess.yaml",
            "process.yaml",
            "columnprocess.yaml",
        ],
    )
    def test_qualified_name_source_query_uses_concat(self, filename):
        query = self._get_qualified_name_source_query(filename)
        assert (
            "concat(" in query
        ), f"{filename}: qualifiedName.source_query should use 'concat(', got: {query!r}"


# =============================================================================
# MetabaseTransformer.__init__ registers correct entity types
# =============================================================================


class TestMetabaseTransformerInit:
    """MetabaseTransformer must register exactly the expected entity types."""

    EXPECTED_KEYS = {
        "METABASECOLLECTION",
        "METABASEDASHBOARD",
        "METABASEQUESTION",
        "BIPROCESS",
        "PROCESS",
        "COLUMNPROCESS",
    }

    @pytest.fixture
    def transformer(self):
        """Instantiate MetabaseTransformer with default args."""
        from app.transformers import MetabaseTransformer

        return MetabaseTransformer(connector_name="metabase", tenant_id="default")

    def test_entity_class_definitions_is_populated(self, transformer):
        assert transformer.entity_class_definitions is not None
        assert len(transformer.entity_class_definitions) > 0

    def test_collection_registered(self, transformer):
        assert "METABASECOLLECTION" in transformer.entity_class_definitions

    def test_dashboard_registered(self, transformer):
        assert "METABASEDASHBOARD" in transformer.entity_class_definitions

    def test_question_registered(self, transformer):
        assert "METABASEQUESTION" in transformer.entity_class_definitions

    def test_biprocess_registered(self, transformer):
        assert "BIPROCESS" in transformer.entity_class_definitions

    def test_process_registered(self, transformer):
        assert "PROCESS" in transformer.entity_class_definitions

    def test_column_process_registered(self, transformer):
        assert "COLUMNPROCESS" in transformer.entity_class_definitions

    def test_no_unexpected_extra_keys(self, transformer):
        actual = set(transformer.entity_class_definitions.keys())
        assert actual == self.EXPECTED_KEYS
