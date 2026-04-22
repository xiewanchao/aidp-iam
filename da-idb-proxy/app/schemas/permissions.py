"""Schemas for permission_groups / permission_group_paths / permission_group_bindings."""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class PermissionGroupPathIn(BaseModel):
    path_prefix: str = Field(..., examples=["/kb/knowledge_bases"])
    method: Optional[str] = Field(
        None,
        description="HTTP method (GET/POST/...); null = match all methods",
        examples=["GET", "POST", None],
    )


class PermissionGroupPathResponse(PermissionGroupPathIn):
    id: int


class PermissionGroupCreate(BaseModel):
    """Full create payload: core fields + nested paths + nested bindings."""
    app_name: str = Field(
        "",
        description="'' means cross-app / platform-level (e.g. iam_admin, acl_access)",
    )
    name: str = Field(..., examples=["kb_create", "rubik_query"])
    description: Optional[str] = None
    paths: List[PermissionGroupPathIn] = Field(default_factory=list)
    bindings: List[str] = Field(
        default_factory=list,
        description="Keycloak group names this permission_group grants. E.g. ['all-users', 'kb-admins']",
    )


class PermissionGroupUpdate(BaseModel):
    """Update payload: any subset of top-level fields + optional nested replace.

    If `paths` is set (non-None), it REPLACES the whole paths list (atomic).
    Same for `bindings`. Set to [] to clear; leave None to keep existing.
    """
    app_name: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    paths: Optional[List[PermissionGroupPathIn]] = None
    bindings: Optional[List[str]] = None


class PermissionGroupResponse(BaseModel):
    id: int
    app_name: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    paths: List[PermissionGroupPathResponse] = Field(default_factory=list)
    bindings: List[str] = Field(default_factory=list)
