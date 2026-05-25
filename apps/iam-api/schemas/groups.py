from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict
from app.schemas.users import UserListResponse


class GroupPermission(BaseModel):
    id: Optional[int] = None
    app_name: Optional[str] = None
    app_display_name: Optional[str] = None
    path_prefix: Optional[str] = None
    method: Optional[str] = None
    required_groups: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class GroupDetailResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    source: str = Field(default="custom", description="preset / app-preset / custom")
    member_total: int = Field(0, description="组内成员总数")
    members: List[UserListResponse] = []
    permissions: List[GroupPermission] = []

    class Config:
        from_attributes = True


class GroupBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., examples=["Engineering_Dept"])
    path: Optional[str] = Field(None, description="组的全路径，例如 /Parent/Child")
    attributes: Optional[Dict[str, List[str]]] = Field(None, description="组的扩展属性")
    description: Optional[str] = Field(None, description="组的描述信息")


class GroupCreate(BaseModel):
    name: str = Field(..., examples=["Engineering_Dept"])
    description: Optional[str] = Field(None, description="组的描述信息")
    users: Optional[List[str]] = []


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    users: Optional[List[str]] = []


class GroupListResponse(GroupBase):
    id: str = Field(..., description="Keycloak 自动生成的 UUID")
    source: str = Field(default="custom", description="preset / app-preset / custom")
    member_count: int = 0
    subGroups: List["GroupListResponse"] = Field(default_factory=list)


class GroupListPageResponse(BaseModel):
    groups: List["GroupListResponse"]
    total: int


class GroupResponse(GroupBase):
    id: str = Field(..., description="Keycloak 自动生成的 UUID")
    subGroups: List["GroupResponse"] = Field(default_factory=list)


class BatchMembersRequest(BaseModel):
    user_ids: List[str] = Field(..., min_length=1)


# Pydantic V2 必须调用此方法来解析循环引用
GroupResponse.model_rebuild()
GroupListResponse.model_rebuild()
GroupListPageResponse.model_rebuild()
