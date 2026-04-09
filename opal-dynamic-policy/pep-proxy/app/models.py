# app/models.py
from pydantic import BaseModel
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
    required_group: str
    description: str = ""


class PathRuleUpdate(BaseModel):
    path_prefix: Optional[str] = None
    required_group: Optional[str] = None
    description: Optional[str] = None


class PathRuleResponse(BaseModel):
    id: int
    path_prefix: str
    required_group: str
    description: str = ""
    created_at: str = ""
