"""
OPA (Open Policy Agent) 客户端
用于管理角色与策略的绑定关系
"""
import os
import httpx
from typing import Optional, Dict, Any
from pydantic import BaseModel

# 从环境变量获取 OPA 服务地址
OPA_BASE_URL = os.getenv("OPA_BASE_URL", "http://pep-proxy.opa.svc.cluster.local:8000")


class Policy(BaseModel):
    """策略信息模型"""
    id: str
    tenant_id: str
    rules: list = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RolePolicyResponse(BaseModel):
    """角色策略绑定响应"""
    role_id: str
    policy: Optional[Policy] = None


class OPAClient:
    """OPA 策略管理客户端"""

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or OPA_BASE_URL).rstrip('/')
        self.client = httpx.Client(timeout=10.0)

    async def get_role_policy(self, role_id: str, tenant_id: str) -> Optional[Policy]:
        """
        查询角色绑定的策略
        
        Args:
            role_id: 角色 UUID
            tenant_id: 租户 ID（即 realm 名称）
            
        Returns:
            Policy 对象，如果未绑定则返回 None
        """
        url = f"{self.base_url}/api/v1/roles/{role_id}/policy"
        response = await self.client.get(url)

        if response.status_code == 200:
            data = response.json()
            return Policy(**data)
        elif response.status_code == 404:
            return None
        else:
            raise Exception(f"Failed to get role policy: {response.status_code} - {response.text}")

    async def bind_role_policy(self, role_id: str, policy_id: str, tenant_id: str) -> bool:
        """
        为角色绑定策略

        Args:
            role_id: 角色 UUID
            policy_id: 策略 ID
            tenant_id: 租户 ID

        Returns:
            是否绑定成功
        """
        url = f"{self.base_url}/api/v1/roles/{role_id}/policy"
        payload = {
            "policy_id": policy_id,
            "tenant_id": tenant_id
        }
        response = await self.client.post(url, json=payload)
        
        if 200 <= response.status_code < 300:
            return True
        else:
            raise Exception(f"Failed to bind policy: {response.status_code} - {response.text}")

    async def update_role_policy(self, role_id: str, policy_id: str, tenant_id: str) -> bool:
        """
        为角色更换绑定的策略

        Args:
            role_id: 角色 UUID
            policy_id: 新策略 ID
            tenant_id: 租户 ID

        Returns:
            是否更新成功
        """
        url = f"{self.base_url}/api/v1/roles/{role_id}/policy"
        payload = {
            "policy_id": policy_id,
            "tenant_id": tenant_id
        }
        response = await self.client.put(url, json=payload)
        
        if 200 <= response.status_code < 300:
            return True
        else:
            raise Exception(f"Failed to update policy: {response.status_code} - {response.text}")

    async def unbind_role_policy(self, role_id: str, tenant_id: str) -> bool:
        """
        为角色解绑策略（调用 OPA 的更新接口，传入空策略 ID）

        Args:
            role_id: 角色 UUID
            tenant_id: 租户 ID

        Returns:
            是否解绑成功
        """
        # 使用 OPA 的更新接口，传入空策略 ID 进行解绑
        # 或者可以调用专门的解绑接口（如果 OPA 提供）
        # 这里假设 OPA 提供解绑接口，或者使用更新接口传 None
        try:
            # 尝试调用解绑接口（如果存在）
            url = f"{self.base_url}/api/v1/roles/{role_id}/policy"
            # 实际需要根据 OPA 的 API 确定解绑方式
            # 这里先假设调用 PUT 并传入 null 或空来解绑
            response = await self.client.delete(url) if hasattr(self.client, "delete") else None
            
            # 如果 OPA 不支持 DELETE，可能需要用 PUT 传特殊值
            if response:
                if 200 <= response.status_code < 300:
                    return True
            
            # 备用方案：调用 PUT 更新为空
            response = await self.client.put(url, json={"policy_id": None, "tenant_id": tenant_id})
            if 200 <= response.status_code < 300:
                return True
                
            raise Exception(f"Failed to unbind policy: {response.status_code}")
        except Exception as e:
            raise Exception(f"Failed to unbind policy: {str(e)}")


# 创建全局单例客户端
opa_client = OPAClient()
