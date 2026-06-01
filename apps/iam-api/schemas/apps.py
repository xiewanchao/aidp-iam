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


class ResourceActionUpdate(BaseModel):
    action: Optional[str] = None
    method: Optional[str] = None
    path_suffix: Optional[str] = None
    success_status: Optional[int] = None
    min_permission: Optional[str] = None


class ResourcePatternIn(BaseModel):
    """A single resource pattern attached to an application.

    The (app_name, resource_prefix, method) triple is a composite primary key;
    `method` defaults to '' (empty string) which resource-sync/pep-proxy treat
    as a fallback matching all HTTP verbs.
    """
    resource_prefix: str = Field(..., examples=["/v1/kb"])
    method: str = Field("", examples=["", "GET", "POST"], description="Empty = fallback for all methods")
    resource_type: str = Field(..., examples=["kb"])
    id_source: str = Field("path", examples=["path", "query", "body"])
    id_field: str = Field("id", examples=["id", "data.kb_id"])
    id_query_param: Optional[str] = Field(None, examples=["kb_id"])
    response_id_field: Optional[str] = Field(
        None,
        examples=["data.KDSID"],
        description=(
            "Override id_field for response-body extraction (2xx create). NULL = reuse id_field. "
            "Useful when request/response field names differ, e.g. KB request uses kbs_id but "
            "response uses data.KDSID."
        ),
    )
    share_to_admin_group_on_create: bool = Field(
        False,
        description=(
            "If true, ext_proc writes a second ACL row granting {app_name}-admins owner permission "
            "on resource create"
        ),
    )
    share_to_all_users_on_create: bool = Field(
        False,
        description="If true, ext_proc writes a third ACL row granting all-users viewer permission on resource create",
    )
    on_create_acl: List[dict] = Field(
        default_factory=list,
        description=(
            "Extra ACL entries written by ext_proc when this resource is created. "
            "Each entry: {user_template, path_suffix, role_path}. "
            "{tenantId} and {instanceId} are substituted at runtime. "
            "path_suffix is appended to the new instance path, e.g. '/Memories' → "
            "Instances/{id}/Memories gets a Contributor ACL for all-users."
        ),
    )
    allow_create_without_acl: bool = Field(
        False,
        description=(
            "If true, pep-proxy skips the ACL check for PUT requests on the collection path. "
            "Enables 'create-then-own' isolation: any authenticated user may create a resource, "
            "and ext_proc writes an Owner ACL for the creator. Used for Sessions, Dashboards, Memories."
        ),
    )
    actions: List[ResourceActionIn] = Field(default_factory=list)


class ResourcePatternUpdate(BaseModel):
    resource_type: Optional[str] = None
    id_source: Optional[str] = None
    id_field: Optional[str] = None
    id_query_param: Optional[str] = None
    response_id_field: Optional[str] = None
    share_to_admin_group_on_create: Optional[bool] = None
    share_to_all_users_on_create: Optional[bool] = None
    on_create_acl: Optional[List[dict]] = None
    allow_create_without_acl: Optional[bool] = None


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
    method: str = ""
    resource_type: str
    id_source: str
    id_field: str
    id_query_param: Optional[str] = None
    response_id_field: Optional[str] = None
    share_to_admin_group_on_create: bool = False
    share_to_all_users_on_create: bool = False
    on_create_acl: List[dict] = Field(default_factory=list)
    allow_create_without_acl: bool = False
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
