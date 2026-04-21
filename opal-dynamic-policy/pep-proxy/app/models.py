# app/models.py
from pydantic import BaseModel, Field
from typing import Optional, List


class AuthRequest(BaseModel):
    tenant_id: str = ""
    resource: str = ""
    path: str = ""
    method: str = ""
    context: dict = {}


class AuthResponse(BaseModel):
    allowed: bool
    user: str = ""
    tenant_id: str = ""
    resource: str = ""
    reason: str = ""


class PathRuleCreate(BaseModel):
    path_prefix: str
    method: Optional[str] = None
    required_groups: List[str] = Field(..., min_length=1)
    description: str = ""


class PathRuleUpdate(BaseModel):
    path_prefix: Optional[str] = None
    method: Optional[str] = None
    required_groups: Optional[List[str]] = Field(default=None, min_length=1)
    description: Optional[str] = None


class PathRuleResponse(BaseModel):
    id: int
    path_prefix: str
    method: Optional[str] = None
    required_groups: List[str] = []
    description: str = ""
    created_at: str = ""
