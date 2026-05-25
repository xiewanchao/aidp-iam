"""
Pydantic models for the resource-sync ACL Management API.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PermissionCreate(BaseModel):
    """Request body for creating a new permission (share a resource)."""

    subject_type: str       # "user" or "group"
    subject_id: str         # user UUID or group name
    permission: str         # "owner", "contributor", or "viewer"
    app_name: str           # application identifier (e.g. "knowledgebase")
    resource_type: str      # resource type (e.g. "kb")


class PermissionUpdate(BaseModel):
    """Request body for updating an existing permission."""

    permission: str         # new permission level


class PermissionResponse(BaseModel):
    """Response model for a single ACL entry."""

    id: int
    tenant_id: str
    app_name: str
    resource_type: str
    resource_id: str
    subject_type: str
    subject_id: str
    permission: str
    created_at: datetime
