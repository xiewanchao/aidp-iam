from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List


class IDPRequest(BaseModel):
    alias: Optional[str] = None
    displayName: Optional[str] = None
    enabled: bool = True
    trustEmail: bool = False
    # 前端传来的 SAML 技术参数（如 singleSignOnServiceUrl）放在这里
    config: Dict[str, Any] = Field(default_factory=dict)


class IDPInstanceResponse(BaseModel):
    """Response for SAML IDP instance"""
    alias: str
    displayName: Optional[str] = None
    internalId: Optional[str] = None
    providerId: str
    enabled: bool
    trustEmail: Optional[bool] = None
    storeToken: Optional[bool] = None
    addReadTokenRoleOnCreate: Optional[bool] = None
    authenticateByDefault: Optional[bool] = None
    linkOnly: Optional[bool] = None
    hideOnLogin: Optional[bool] = None
    firstBrokerLoginFlowAlias: Optional[str] = None
    postBrokerLoginFlowAlias: Optional[str] = None
    config: Dict[str, str] = Field(default_factory=dict)


class SAMLMetadataImportResponse(BaseModel):
    """Response from SAML metadata import endpoint"""
    imported: Optional[Any] = None
    config: Optional[Dict[str, str]] = None


class IdPMapperBase(BaseModel):
    name: str = Field(..., description="Mapper 名称，例如 'Group Mapping'")
    identityProviderMapper: str = Field(..., description="Mapper 类型，例如 'saml-group-idp-mapper'")
    config: Dict[str, str] = Field(..., description="配置项，注意：所有 Value 必须为字符串")

class IdPMapperCreate(IdPMapperBase):
    identityProviderAlias: Optional[str] = None

class IdPMapperUpdate(BaseModel):
    name: Optional[str] = None
    identityProviderMapper: Optional[str] = None
    config: Optional[Dict[str, str]] = None

class IdPMapperResponse(IdPMapperBase):
    id: str
    identityProviderAlias: str
