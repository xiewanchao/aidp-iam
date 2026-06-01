from pydantic import BaseModel, Field
from typing import Optional, List
from typing import Any


class TenantCreate(BaseModel):
    realm: str
    displayName: str


class TenantResponse(BaseModel):
    realm: str
    id: str
    admin_role: str
    admin_user: Optional[str] = None


class TenantListResponse(BaseModel):
    """Response for listing all tenants (realms)"""
    id: str
    realm: str
    displayName: Optional[str] = None
    displayNameHtml: Optional[str] = None
    enabled: bool = True
    notBefore: Optional[int] = None
    defaultSignatureAlgorithm: Optional[str] = None
    sslRequired: Optional[str] = None
    registrationAllowed: Optional[bool] = None
    loginWithEmailAllowed: Optional[bool] = None
    duplicateEmailsAllowed: Optional[bool] = None
    resetPasswordAllowed: Optional[bool] = None
    editUsernameAllowed: Optional[bool] = None
    bruteForceProtected: Optional[bool] = None


class MessageResponse(BaseModel):
    """Standard message response for operations that don't return data"""
    msg: str
    alias: Optional[str] = None
    id: Optional[str] = None


class EmailSettingsRequest(BaseModel):
    """Email feature toggles. loginWithEmailAllowed is always false and not exposed."""
    reset_password_allowed: Optional[bool] = Field(
        None, description="Allow users to reset their password via email (forgot-password flow)"
    )
    verify_email: Optional[bool] = Field(
        None, description="Require new users to verify their email address before first login"
    )


class EmailSettingsResponse(BaseModel):
    reset_password_allowed: Optional[bool] = None
    verify_email: Optional[bool] = None
    login_with_email_allowed: bool = Field(
        False, description="Always false — email is not a login method in this system"
    )


class SmtpSettingsRequest(BaseModel):
    """SMTP server configuration for outbound email (password reset, verification, etc.).
    Only provided fields are updated; omitted fields keep their current values.
    """
    host: Optional[str] = Field(None, description="SMTP server hostname or IP")
    port: Optional[int] = Field(None, ge=1, le=65535, description="SMTP port (25 / 465 / 587)")
    from_address: Optional[str] = Field(None, description="Sender email address (From:)")
    from_display_name: Optional[str] = Field(None, description="Sender display name")
    reply_to: Optional[str] = Field(None, description="Reply-To email address")
    reply_to_display_name: Optional[str] = Field(None, description="Reply-To display name")
    envelope_from: Optional[str] = Field(
        None,
        description="Envelope sender (MAIL FROM); leave empty to use from_address",
    )
    ssl: Optional[bool] = Field(None, description="Use implicit SSL/TLS (typically port 465)")
    starttls: Optional[bool] = Field(None, description="Use STARTTLS upgrade (typically port 587)")
    auth: Optional[bool] = Field(None, description="Enable SMTP authentication")
    user: Optional[str] = Field(None, description="SMTP username")
    password: Optional[str] = Field(None, description="SMTP password (write-only, never returned in GET)")


class SmtpSettingsResponse(BaseModel):
    """Current SMTP configuration. Password is never returned."""
    host: Optional[str] = None
    port: Optional[int] = None
    from_address: Optional[str] = None
    from_display_name: Optional[str] = None
    reply_to: Optional[str] = None
    reply_to_display_name: Optional[str] = None
    envelope_from: Optional[str] = None
    ssl: Optional[bool] = None
    starttls: Optional[bool] = None
    auth: Optional[bool] = None
    user: Optional[str] = None
