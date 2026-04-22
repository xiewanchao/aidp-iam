# app/models.py
from pydantic import BaseModel


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
