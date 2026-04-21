from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# --- Request models ---

class ResourceActionIn(BaseModel):
    """One action rule attached to a resource pattern.

    Empty `actions` on a pattern means resource-sync / pep-proxy fall back to
    the code-level DEFAULT_ACTIONS (standard RESTful: GET→viewer, POST→create,
    PUT/PATCH→contributor, DELETE→owner).
    """
    action: str = Field(..., examples=["create", "read", "update", "delete", "list"])
    method: str = Field(..., examples=["GET", "POST", "PUT", "PATCH", "DELETE"])
    path_suffix: Optional[str] = Field(None, examples=["/remove", "/{id}/execute-sql"])
    success_status: Optional[int] = Field(None, examples=[201, 204])
    min_permission: str = Field("none", examples=["none", "viewer", "contributor", "owner"])


class ResourcePatternIn(BaseModel):
    """A single resource pattern attached to an application."""
    resource_prefix: str = Field(..., examples=["/v1/kb"])
    resource_type: str = Field(..., examples=["kb"])
    id_source: str = Field("path", examples=["path", "query", "body"])
    id_field: str = Field("id", examples=["id", "data.kb_id"])
    id_query_param: Optional[str] = Field(None, examples=["kb_id"])
    actions: List[ResourceActionIn] = Field(default_factory=list)


class AppCreate(BaseModel):
    app_name: str = Field(..., examples=["knowledgebase"])
    path_prefix: str = Field(..., examples=["/kb/"])
    display_name: Optional[str] = None
    description: Optional[str] = None
    resource_patterns: List[ResourcePatternIn] = Field(default_factory=list)


class AppUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


# --- Response models ---

class ResourceActionResponse(BaseModel):
    id: int
    action: str
    method: str
    path_suffix: Optional[str] = None
    success_status: Optional[int] = None
    min_permission: str


class ResourcePatternResponse(BaseModel):
    app_name: str
    resource_prefix: str
    resource_type: str
    id_source: str
    id_field: str
    id_query_param: Optional[str] = None
    actions: List[ResourceActionResponse] = Field(default_factory=list)


class AppResponse(BaseModel):
    app_name: str
    path_prefix: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    admin_group: Optional[str] = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    resource_patterns: List[ResourcePatternResponse] = Field(default_factory=list)
