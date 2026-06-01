from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime


class UserBase(BaseModel):
    username: Optional[str] = None
    enabled: Optional[bool] = None
    attributes: Optional[Dict[str, List[str]]] = None


class UserCreate(BaseModel):
    username: str = Field(..., description="Username is required for user creation")
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
    email: Optional[str] = Field(default=None, description="Email address")
    nickname: Optional[str] = Field(default=None, description="Display nickname")
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
    enabled: Optional[bool] = None
    nickname: Optional[str] = None
    email: Optional[str] = Field(default=None, description="Email address")
    groups: Optional[List[str]] = Field(
        default=None,
        description="List of group IDs to assign the user to (replaces current groups)",
    )


class PasswordResetRequest(BaseModel):
    """Request body for resetting a user password"""
    password: str = Field(..., description="New password")


class PasswordVerifyRequest(BaseModel):
    """Request body for verifying a user password"""
    password: str = Field(..., description="Password to verify")


class PasswordVerifyResponse(BaseModel):
    """Response for password verification"""
    valid: bool = Field(..., description="Whether the password is correct")


class BatchDeleteRequest(BaseModel):
    """Request body for batch-deleting users"""
    user_ids: List[str] = Field(..., description="List of user IDs to delete")


class UserListResponse(UserResponse):
    """Enhanced user response with account type and groups"""
    account_type: str = Field(
        "internal",
        description="'internal' or 'federated'"
    )
    email: Optional[str] = Field(default=None, description="Email address")
    nickname: Optional[str] = Field(
        default=None,
        description="Display nickname (from Keycloak user attributes)"
    )
    groups: List[dict] = Field(
        default_factory=list,
        description="Groups the user belongs to (id and name)"
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="Account creation time (converted from Keycloak createdTimestamp)"
    )


class UserListPageResponse(BaseModel):
    """Paginated user list response"""
    users: List[UserListResponse]
    total: int


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


class PasswordStatusResponse(BaseModel):
    """Password status for a single user."""
    user_id: str
    credential_created_at: Optional[datetime] = Field(
        None, description="When the password was last set (from Keycloak credentials)"
    )
    is_temporary: bool = Field(
        False, description="True when the user has not yet changed their temporary password"
    )
    expiry_days: Optional[int] = Field(
        None, description="Password validity in days from Realm policy; null = no expiry policy"
    )
    days_remaining: Optional[int] = Field(
        None, description="Days until expiry; null when no expiry policy or credential date unknown"
    )
    is_expired: bool = Field(
        False, description="True when days_remaining <= 0"
    )


class PasswordPolicyRequest(BaseModel):
    """Subset of Realm password policy fields exposed via the API."""
    expire_days: Optional[int] = Field(
        None, ge=1, description="Password validity in days (forceExpiredPasswordChange); null removes the policy"
    )
    min_length: Optional[int] = Field(
        None, ge=1, description="Minimum password length (length)"
    )
    require_uppercase: Optional[bool] = Field(
        None, description="Require at least one uppercase letter (upperCase)"
    )
    require_lowercase: Optional[bool] = Field(
        None, description="Require at least one lowercase letter (lowerCase)"
    )
    require_digits: Optional[bool] = Field(
        None, description="Require at least one digit (digits)"
    )
    require_special: Optional[bool] = Field(
        None, description="Require at least one special character (specialChars)"
    )
    history_count: Optional[int] = Field(
        None, ge=1, description="Disallow reuse of last N passwords (passwordHistory); null removes the policy"
    )


class PasswordPolicyResponse(PasswordPolicyRequest):
    """Current Realm password policy (same fields as request, all nullable)."""
    pass
