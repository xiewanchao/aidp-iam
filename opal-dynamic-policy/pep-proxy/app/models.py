# app/models.py
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from enum import Enum


# ---------------------------------------------------------------------------
# Resource Policy（无 role，role 通过 binding 关联）
# ---------------------------------------------------------------------------

class PolicyRule(BaseModel):
    resource: str                       # 资源名称（如 documents、reports）
    effect: str                         # "allow" | "deny"
    path: Optional[str] = None          # URL 路径前缀（如 /api/v1/documents）；
                                        # 设置后按前缀匹配 input.path，优先于 resource 精确匹配


class PolicyCreateRequest(BaseModel):
    name: str                           # 策略显示名称（human-readable label）
    rules: List[PolicyRule]             # each resource has its own allow/deny
    tenant_id: str
    conditions: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Role-Policy Binding（role UUID → policy，1:1 upsert）
# ---------------------------------------------------------------------------

class RoleBindingRequest(BaseModel):
    policy_id: str                      # single policy (1:1 binding)
    tenant_id: str


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------

class AuthRequest(BaseModel):
    resource: str
    tenant_id: str
    path: Optional[str] = None          # 实际请求 URL 路径（可选，用于 path 规则匹配）
    method: Optional[str] = None        # HTTP 方法（可选，预留）
    context: Optional[Dict[str, Any]] = None


class AuthResponse(BaseModel):
    allowed: bool
    user: str
    tenant_id: str
    resource: str
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Policy template（保留，仅 role_based 模板）
# ---------------------------------------------------------------------------

class PolicyTemplate(BaseModel):
    name: str
    content: str
    parameters: Dict[str, str]
