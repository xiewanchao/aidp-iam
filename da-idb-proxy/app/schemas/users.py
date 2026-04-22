from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class UserBase(BaseModel):
    username: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    emailVerified: Optional[bool] = None
    enabled: Optional[bool] = None
    attributes: Optional[Dict[str, List[str]]] = None


class UserCreate(BaseModel):
    username: str = Field(..., description="Username is required for user creation")
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    emailVerified: Optional[bool] = None
    enabled: Optional[bool] = None
    attributes: Optional[Dict[str, List[str]]] = None


class UserUpdate(UserBase):
    """All fields optional for updating users"""
    pass


class UserResponse(UserBase):
    id: str = Field(..., description="Keycloak generated user UUID")
    createdTimestamp: Optional[int] = None
    totp: Optional[bool] = None
    federationLink: Optional[str] = None
    serviceAccountClientId: Optional[str] = None
    notBefore: Optional[int] = None


class UserContextResponse(BaseModel):
    """Response for user context endpoint (groups and roles)"""
    groups: List[dict] = Field(default_factory=list, description="Groups the user belongs to")
    roles: List[dict] = Field(default_factory=list, description="Roles assigned to the user")


# --- New models for enhanced user management ---

class UserCreateRequest(BaseModel):
    """Request body for creating a new user"""
    username: str = Field(..., description="Username (required)")
    password: str = Field(..., description="Initial password (required)")
    email: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    groups: Optional[List[str]] = Field(
        default=None,
        description="List of group IDs to assign the user to"
    )
    temporary_password: bool = Field(
        default=True,
        description=(
            "If true (default), the user must change the password on first "
            "login. Set false for service/test accounts."
        ),
    )


class UserUpdateRequest(BaseModel):
    """Request body for updating user info (all fields optional)"""
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    enabled: Optional[bool] = None


class PasswordResetRequest(BaseModel):
    """Request body for resetting a user password"""
    password: str = Field(..., description="New password")


class BatchDeleteRequest(BaseModel):
    """Request body for batch-deleting users"""
    user_ids: List[str] = Field(..., description="List of user IDs to delete")


class UserListResponse(UserResponse):
    """Enhanced user response with account type and groups"""
    account_type: str = Field(
        "internal",
        description="'internal' or 'federated'"
    )
    groups: List[dict] = Field(
        default_factory=list,
        description="Groups the user belongs to (id and name)"
    )


class PermissionInfo(BaseModel):
    """A single path-rule permission entry"""
    app_name: Optional[str] = None
    app_display_name: Optional[str] = None
    path_prefix: Optional[str] = None
    required_group: Optional[str] = None
    description: Optional[str] = None


class UserDetailResponse(UserListResponse):
    """Full user detail including permissions from path_rules"""
    permissions: List[dict] = Field(
        default_factory=list,
        description="Path-rule permissions derived from the user's groups"
    )


class BatchImportRequest(BaseModel):
    """Request body for batch-importing users"""
    users: List[UserCreateRequest]


class BatchOperationResponse(BaseModel):
    """Response for batch operations (import / delete)"""
    succeeded: int = 0
    failed: int = 0
    errors: List[dict] = Field(
        default_factory=list,
        description="List of errors with index, username, and error message"
    )
