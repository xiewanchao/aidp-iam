from fastapi import APIRouter, HTTPException, Request, status
from datetime import datetime
import os


# Keycloak's built-in `master` realm is reserved for Keycloak admin operations;
# our APIs MUST NOT mutate it. (Different from `KC_REALM`, which is the realm
# our service account uses to obtain tokens.)
PROTECTED_REALM = "master"

def skip_master_realm(request: Request):
    """Block any path-param `realm` / `realm_name` that targets Keycloak's master realm."""
    path_params = request.path_params
    realm_val = path_params.get("realm") or path_params.get("realm_name")
    if realm_val and realm_val.lower() == PROTECTED_REALM:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Operations on the Keycloak '{PROTECTED_REALM}' realm are not allowed.",
        )
    return realm_val


router = APIRouter(prefix="/common", tags=["Common"])


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """健康检查接口 (GET /api/v1/common/health)"""
    return {
        "status": "healthy",
        "code": 200,
        "timestamp": datetime.utcnow().isoformat()
    }

