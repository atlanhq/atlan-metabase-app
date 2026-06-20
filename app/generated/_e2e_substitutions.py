# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from application_sdk.testing.e2e.substitutions import MustacheSubstitutions


class MetabaseMustacheSubstitutions(MustacheSubstitutions):
    extraction_method: Literal["direct"] = Field(
        default="direct",
        alias="{{extraction-method}}",
    )
    agent_json: dict[str, Any] | None = Field(default=None, alias="{{agent-json}}")
    include_collections: dict[str, Any] | None = Field(
        default=None,
        alias="{{include-collections}}",
    )
    exclude_collections: dict[str, Any] | None = Field(
        default=None,
        alias="{{exclude-collections}}",
    )
    preflight_check: str = Field(default="", alias="{{preflight-check}}")
