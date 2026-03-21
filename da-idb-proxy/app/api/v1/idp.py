from typing import List

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status

from app.core.keycloak import kc
from app.api.v1.common import skip_master_realm
from app.schemas.idp import (
    IDPRequest,
    IdPMapperCreate,
    IdPMapperUpdate,
    IdPMapperResponse,
    IDPInstanceResponse,
    SAMLMetadataImportResponse
)

import os

router = APIRouter(prefix="/{realm}/idp", tags=["IDP"], dependencies=[Depends(skip_master_realm)])


@router.post("/saml/import", response_model=SAMLMetadataImportResponse)
async def import_saml_metadata(realm: str, file: UploadFile = File(...)):
    xml_content = await file.read()

    files = {
        'file': (file.filename, xml_content, file.content_type)
    }
    data = {"providerId": "saml"}

    resp = kc.request(
        "POST",
        f"/realms/{realm}/identity-provider/import-config",
        data=data,
        files=files
    )
    return resp.json()


def _validate_saml_config(config: dict):
    """
    模拟 Keycloak 界面校验逻辑：确保 SAML 核心配置不为空
    防止 API 创建/更新出“Add/Save 按钮灰色”的无效实例
    """
    # 26.5 界面最核心的三个必填项
    required_fields = {
        "singleSignOnServiceUrl": "SSO Service URL"
        # "entityId": "Service Provider Entity ID",
    }

    missing = [desc for field, desc in required_fields.items() if not config.get(field)]

    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required SAML configuration: {', '.join(missing)}"
        )


@router.post("/saml/instances", status_code=status.HTTP_201_CREATED, response_model=IDPInstanceResponse)
def create_idp_instance(realm: str, payload: IDPRequest):

    existing = kc.request("GET", f"/realms/{realm}/identity-provider/instances").json()
    if len(existing) > 0:
        raise HTTPException(status_code=400, detail="Realm already has an IDP instance.")

    _validate_saml_config(payload.config)

    alias = os.getenv("DEFAULT_IDP_ALIAS", "da-saml-idp")

    idp_data = {
        "alias": alias,
        "displayName": payload.displayName or alias,
        "providerId": "saml",
        "enabled": payload.enabled,
        "trustEmail": payload.trustEmail,
        "firstBrokerLoginFlowAlias": "first broker login",
        "config": payload.config
    }

    kc.request("POST", f"/realms/{realm}/identity-provider/instances", json=idp_data)
    return kc.request("GET", f"/realms/{realm}/identity-provider/instances/{alias}").json()


@router.put("/saml/instances", response_model=IDPInstanceResponse)
def update_idp_instance(realm: str, payload: IDPRequest):
    alias = os.getenv("DEFAULT_IDP_ALIAS", "da-saml-idp")

    check = kc.request("GET", f"/realms/{realm}/identity-provider/instances/{alias}")
    if check.status_code == 404:
        raise HTTPException(status_code=404, detail=f"IDP {alias} not found.")

    current_full_data = check.json()

    current_full_data["enabled"] = payload.enabled
    current_full_data["trustEmail"] = payload.trustEmail
    if payload.displayName:
        current_full_data["displayName"] = payload.displayName

    current_full_data["config"].update(payload.config)

    _validate_saml_config(current_full_data["config"])

    current_full_data["alias"] = alias

    kc.request("PUT", f"/realms/{realm}/identity-provider/instances/{alias}", json=current_full_data)

    return kc.request("GET", f"/realms/{realm}/identity-provider/instances/{alias}").json()


@router.get("/saml/instances", response_model=List[IDPInstanceResponse])
def list_idp_instances(realm: str):
    return kc.request("GET", f"/realms/{realm}/identity-provider/instances").json()


@router.delete("/saml/instances/{alias}", status_code=status.HTTP_204_NO_CONTENT)
def delete_idp_instance(realm: str, alias: str):
    """
    删除 SAML 2.0 IDP 实例
    :param realm: 租户名称
    :param alias: IDP 的唯一别名 (比如 'saml-idp-01')
    """
    # Keycloak 14.0 标准路径: /auth/admin/realms/{realm}/identity-provider/instances/{alias}
    resp = kc.request("DELETE", f"/realms/{realm}/identity-provider/instances/{alias}")

    # 如果别名不存在，14.0 可能会报 404，我们通过 kc.request 内部处理或这里补充逻辑
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"IDP instance '{alias}' not found in realm '{realm}'")

    return None  # 204 No Content 不需要返回 body


# --- Protocol Mappers 管理 ---

@router.get("/saml/instances/{alias}/mappers", response_model=List[IdPMapperResponse])
def list_idp_mappers(realm: str, alias: str):
    """获取指定 IDP 的所有 Mappers"""
    path = f"/realms/{realm}/identity-provider/instances/{alias}/mappers"
    return kc.request("GET", path).json()


@router.post("/saml/instances/{alias}/mappers", status_code=status.HTTP_201_CREATED, response_model=IdPMapperResponse)
def create_idp_mapper(realm: str, alias: str, payload: IdPMapperCreate):
    """创建 IDP Mapper"""
    # 1. 转换模型并确保没有多余的 protocol 字段
    mapper_data = payload.model_dump(exclude_none=True)
    mapper_data["identityProviderAlias"] = alias

    path = f"/realms/{realm}/identity-provider/instances/{alias}/mappers"
    res = kc.request("POST", path, json=mapper_data)

    # Keycloak 26.x 成功返回 201，但新mapper的id在response header的location中，需要提取
    if res.status_code == 201:
        location = res.headers.get("Location")
        if location:
            new_id = location.split("/")[-1]
            return {**mapper_data, "id": new_id}
        return mapper_data

    raise HTTPException(status_code=res.status_code, detail=res.text)


@router.put("/saml/instances/{alias}/mappers/{mapper_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_idp_mapper(realm: str, alias: str, mapper_id: str, payload: IdPMapperUpdate):
    base_path = f"/realms/{realm}/identity-provider/instances/{alias}/mappers/{mapper_id}"
    check = kc.request("GET", base_path)
    if check.status_code != 200:
        raise HTTPException(status_code=404, detail="Mapper not found")

    current_data = check.json()

    update_dict = payload.model_dump(exclude_none=True)
    for key, value in update_dict.items():
        current_data[key] = value

    res = kc.request("PUT", base_path, json=current_data)

    if res.status_code != 204:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    return None


@router.delete("/saml/instances/{alias}/mappers/{mapper_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_idp_mapper(realm: str, alias: str, mapper_id: str):
    path = f"/realms/{realm}/identity-provider/instances/{alias}/mappers/{mapper_id}"
    res = kc.request("DELETE", path)

    if res.status_code == 404:
        raise HTTPException(status_code=404, detail="Mapper not found")
    if res.status_code != 204:
        raise HTTPException(status_code=res.status_code, detail="Delete failed")

    return None
