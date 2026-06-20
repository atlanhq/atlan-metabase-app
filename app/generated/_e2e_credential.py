# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from pydantic import Field

from application_sdk.testing.e2e.credential import CredentialBody


class MetabaseCredentialBody(CredentialBody):
    name: str = Field(alias="name")
    auth_type: str = Field(default="basic", alias="authType")
    host: str = Field(alias="host")
    port: int = Field(default=443, alias="port")
    username: str = Field(default="", alias="username")
    password: str = Field(default="", alias="password")
