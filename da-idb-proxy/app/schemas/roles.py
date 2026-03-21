from pydantic import BaseModel, Field
from typing import Optional, Dict, List

# 基础公共字段
class RoleBase(BaseModel):
    description: Optional[str] = None
    attributes: Optional[Dict[str, List[str]]] = None
    composite: Optional[bool] = False

# 场景 1：创建 (POST) -> 强制要求 name
class RoleCreate(RoleBase):
    name: str = Field(..., json_schema_extra={"example": "business_admin"})

# 场景 2：更新 (PUT) -> 所有字段均为可选
class RoleUpdate(RoleBase):
    name: Optional[str] = None

# 场景 3：查询返回 (GET) -> 包含 Keycloak 自动生成的只读字段
class RoleResponse(RoleBase):
    id: str # Keycloak 生成的 UUID
    name: str
    clientRole: bool
    containerId: Optional[str] = None


class RoleUpdateByIdRequest(BaseModel):
    """Request body for updating a role by UUID (supports renaming)"""
    name: Optional[str] = None
    description: Optional[str] = None
    attributes: Optional[Dict[str, List[str]]] = None
    composite: Optional[bool] = None
