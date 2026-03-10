"""Unit tests for app.models Pydantic models."""

import pytest
from pydantic import ValidationError

from app.models import (
    MetabaseCredentials,
    PreflightCheckResult,
    PreflightCheckResults,
    WorkflowArgs,
)


class TestMetabaseCredentials:
    """Tests for MetabaseCredentials model."""

    def test_happy_path_all_fields(self):
        creds = MetabaseCredentials(
            host="https://myinstance.metabaseapp.com",
            port=443,
            username="admin@example.com",
            password="s3cr3t",
        )
        assert creds.host == "https://myinstance.metabaseapp.com"
        assert creds.port == 443
        assert creds.username == "admin@example.com"
        assert creds.password == "s3cr3t"

    def test_default_port_is_443(self):
        creds = MetabaseCredentials(host="https://mb.example.com")
        assert creds.port == 443

    def test_host_is_required(self):
        with pytest.raises(ValidationError):
            MetabaseCredentials()  # type: ignore[call-arg]

    def test_username_defaults_to_none(self):
        creds = MetabaseCredentials(host="https://mb.example.com")
        assert creds.username is None

    def test_password_defaults_to_none(self):
        creds = MetabaseCredentials(host="https://mb.example.com")
        assert creds.password is None

    def test_extra_allow_accepts_unknown_fields(self):
        """model_config extra=allow must not raise on extra fields."""
        creds = MetabaseCredentials(
            host="https://mb.example.com",
            some_unknown_field="value",  # type: ignore[call-arg]
        )
        assert creds.host == "https://mb.example.com"

    def test_extra_field_accessible_via_model(self):
        creds = MetabaseCredentials(
            host="https://mb.example.com",
            custom_key="custom_value",  # type: ignore[call-arg]
        )
        assert creds.custom_key == "custom_value"  # type: ignore[attr-defined]

    def test_port_can_be_custom(self):
        creds = MetabaseCredentials(host="http://localhost", port=3000)
        assert creds.port == 3000

    def test_model_validate_from_dict(self):
        raw = {"host": "https://mb.example.com", "username": "user", "password": "pass"}
        creds = MetabaseCredentials.model_validate(raw)
        assert creds.host == "https://mb.example.com"
        assert creds.username == "user"


class TestPreflightCheckResult:
    """Tests for PreflightCheckResult model."""

    def test_success_true(self):
        result = PreflightCheckResult(
            success=True, successMessage="All good", failureMessage=""
        )
        assert result.success is True
        assert result.successMessage == "All good"
        assert result.failureMessage == ""

    def test_success_false_with_message(self):
        result = PreflightCheckResult(
            success=False, failureMessage="Something went wrong"
        )
        assert result.success is False
        assert result.failureMessage == "Something went wrong"

    def test_success_message_defaults_to_empty_string(self):
        result = PreflightCheckResult(success=True)
        assert result.successMessage == ""

    def test_failure_message_defaults_to_empty_string(self):
        result = PreflightCheckResult(success=False)
        assert result.failureMessage == ""

    def test_success_field_required(self):
        with pytest.raises(ValidationError):
            PreflightCheckResult()  # type: ignore[call-arg]


class TestPreflightCheckResults:
    """Tests for PreflightCheckResults container model."""

    def test_all_fields_none_by_default(self):
        results = PreflightCheckResults()
        assert results.collectionCountCheck is None
        assert results.dashboardCountCheck is None
        assert results.questionCountCheck is None
        assert results.nativeQueryPermissionCheck is None

    def test_model_dump_exclude_none_omits_null_fields(self):
        results = PreflightCheckResults(
            collectionCountCheck=PreflightCheckResult(
                success=True, successMessage="Total collections: 5"
            )
        )
        dumped = results.model_dump(exclude_none=True)
        assert "collectionCountCheck" in dumped
        assert "dashboardCountCheck" not in dumped
        assert "questionCountCheck" not in dumped
        assert "nativeQueryPermissionCheck" not in dumped

    def test_model_dump_includes_all_set_fields(self):
        results = PreflightCheckResults(
            collectionCountCheck=PreflightCheckResult(
                success=True, successMessage="Total collections: 5"
            ),
            dashboardCountCheck=PreflightCheckResult(
                success=True, successMessage="Total dashboards: 3"
            ),
            questionCountCheck=PreflightCheckResult(
                success=True, successMessage="Total questions: 10"
            ),
            nativeQueryPermissionCheck=PreflightCheckResult(
                success=True, successMessage="Check successful"
            ),
        )
        dumped = results.model_dump(exclude_none=True)
        assert len(dumped) == 4

    def test_model_dump_exclude_none_empty_when_no_checks_set(self):
        results = PreflightCheckResults()
        dumped = results.model_dump(exclude_none=True)
        assert dumped == {}

    def test_construction_with_single_check(self):
        results = PreflightCheckResults(
            collectionCountCheck=PreflightCheckResult(
                success=False,
                failureMessage="Metabase client not initialized",
            )
        )
        assert results.collectionCountCheck is not None
        assert results.collectionCountCheck.success is False
        assert (
            "Metabase client not initialized"
            in results.collectionCountCheck.failureMessage
        )


class TestWorkflowArgs:
    """Tests for WorkflowArgs model."""

    def test_all_fields_optional_and_default_to_none(self):
        args = WorkflowArgs()
        assert args.workflow_id is None
        assert args.workflow_run_id is None
        assert args.output_path is None
        assert args.output_prefix is None
        assert args.processed_data_path is None
        assert args.typename is None
        assert args.chunk_start is None
        assert args.credentials is None
        assert args.metadata is None
        assert args.connection is None

    def test_construction_with_all_fields(self):
        args = WorkflowArgs(
            workflow_id="wf-123",
            workflow_run_id="run-456",
            output_path="/tmp/output",
            output_prefix="/tmp",
            processed_data_path="/tmp/processed",
            typename="COLLECTION",
            chunk_start=0,
            credentials={"host": "https://mb.example.com", "username": "u"},
            metadata={"include-collections": "{}"},
            connection={"connection_qualified_name": "default/metabase/1"},
        )
        assert args.workflow_id == "wf-123"
        assert args.typename == "COLLECTION"
        assert args.chunk_start == 0
        assert args.credentials is not None
        assert args.credentials["host"] == "https://mb.example.com"

    def test_extra_allow_accepts_unknown_fields(self):
        """WorkflowArgs has model_config extra=allow."""
        args = WorkflowArgs(extra_field="extra_value")  # type: ignore[call-arg]
        assert args.extra_field == "extra_value"  # type: ignore[attr-defined]

    def test_model_validate_from_dict(self):
        raw = {
            "workflow_id": "wf-001",
            "output_path": "/tmp/out",
        }
        args = WorkflowArgs.model_validate(raw)
        assert args.workflow_id == "wf-001"
        assert args.output_path == "/tmp/out"
        assert args.typename is None
