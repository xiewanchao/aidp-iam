"""
Pydantic models for API Key management endpoints.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# --- Request models ---

class ApiKeyCreate(BaseModel):
    app_name: str = Field(..., examples=["my-service"])
    description: Optional[str] = Field(None, examples=["Production API key for my-service"])
    allowed_paths: Optional[List[str]] = Field(None, examples=[["/api/v1/data", "/api/v1/query"]])
    rate_limit: Optional[int] = Field(100, ge=1, le=10000, examples=[100])
    expires_at: Optional[datetime] = None


class ApiKeyUpdate(BaseModel):
    description: Optional[str] = None
    enabled: Optional[bool] = None
    allowed_paths: Optional[List[str]] = None
    rate_limit: Optional[int] = Field(None, ge=1, le=10000)


# --- Response models ---

class ApiKeyResponse(BaseModel):
    id: str
    key_prefix: str
    tenant_id: str
    app_name: str
    description: Optional[str] = None
    subject_id: str
    subject_type: str = "service"
    allowed_paths: Optional[List[str]] = None
    rate_limit: int = 100
    expires_at: Optional[datetime] = None
    enabled: bool = True
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None


class ApiKeyCreateResponse(ApiKeyResponse):
    """Returned only on create and rotate -- includes the plaintext key (shown once)."""
    api_key: str = Field(..., description="Plaintext API key. Store securely; it will not be shown again.")
