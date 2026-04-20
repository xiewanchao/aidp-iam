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
    """Response from SAML metadata import endpoint - matches Keycloak's actual response"""
    addExtensionsElementWithKeyInfo: Optional[str] = None
    artifactBindingResponse: Optional[str] = None
    artifactResolutionServiceUrl: Optional[str] = None
    enabledFromMetadata: Optional[str] = None
    idpEntityId: Optional[str] = None
    loginHint: Optional[str] = None
    metadataDescriptorUrl: Optional[str] = None
    nameIDPolicyFormat: Optional[str] = None
    postBindingAuthnRequest: Optional[str] = None
    postBindingLogout: Optional[str] = None
    postBindingResponse: Optional[str] = None
    signingCertificate: Optional[str] = None
    singleLogoutServiceUrl: Optional[str] = None
    singleSignOnServiceUrl: Optional[str] = None
    validateSignature: Optional[str] = None
    wantAuthnRequestsSigned: Optional[str] = None


class IdPMapperCreate(BaseModel):
    """简化的 IDP Mapper 创建请求"""
    name: str = Field(..., description="Mapper 名称")
    attributeKey: str = Field(..., description="Remote Attribute（SAML 属性名）")
    attributeValue: str = Field(..., description="Local Attribute（Keycloak 用户属性名）")
    friendlyName: Optional[str] = Field(None, description="Friendly Name（可选）")


class IdPMapperUpdate(BaseModel):
    """简化的 IDP Mapper 更新请求"""
    name: Optional[str] = None
    attributeKey: Optional[str] = None
    attributeValue: Optional[str] = None
    friendlyName: Optional[str] = None


class IdPMapperResponse(BaseModel):
    """简化的 IDP Mapper 响应"""
    id: str
    name: str
    attributeKey: str
    attributeValue: str
    friendlyName: Optional[str] = None


# ---------------------------------------------------------------------------
# Advanced Group Mapper
# ---------------------------------------------------------------------------
# Keycloak 原生 mapper type: "saml-advanced-group-idp-mapper"
# 作用：SAML 断言中出现一组指定属性/值时，自动把用户加入指定的 Keycloak 组。
# 例如：Department=RD-Infra & Level=P7 → 自动加入 /rd-admins


class IdPGroupMapperCondition(BaseModel):
    """单个属性条件（多个条件之间为 AND 语义）"""
    attribute: str = Field(..., description="SAML 属性名（Remote Attribute）")
    value: str = Field(..., description="期望的属性值，regex=True 时可填正则表达式")


class IdPGroupMapperCreate(BaseModel):
    """创建 Advanced Group Mapper 的请求"""
    name: str = Field(..., description="Mapper 名称（同一 IdP 下唯一）")
    conditions: List[IdPGroupMapperCondition] = Field(
        ..., min_length=1,
        description="一组属性条件，全部命中才会触发加组（AND 语义）",
    )
    group: str = Field(..., description="Keycloak 组路径，必须以 / 开头，如 /rd-admins")
    regex: bool = Field(False, description="是否把条件 value 当作正则表达式匹配")


class IdPGroupMapperUpdate(BaseModel):
    """更新 Advanced Group Mapper 的请求（所有字段可选）"""
    name: Optional[str] = None
    conditions: Optional[List[IdPGroupMapperCondition]] = None
    group: Optional[str] = None
    regex: Optional[bool] = None


class IdPGroupMapperResponse(BaseModel):
    """Advanced Group Mapper 响应"""
    id: str
    name: str
    conditions: List[IdPGroupMapperCondition]
    group: str
    regex: bool
