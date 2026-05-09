import os
from dotenv import load_dotenv

# 必须在导入应用模块之前加载环境变量
load_dotenv()
load_dotenv('.env.local', override=True)

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.core.keycloak import KeycloakError
from app.core.db import get_pool, close_pool
from app.api.v1 import tenants, idp, identity, common, token, apps, api_keys, permissions, acls, manifests

@asynccontextmanager
async def lifespan(application: FastAPI):
    await get_pool()
    yield
    await close_pool()

app = FastAPI(title="AccessManager API", version="2.0.0", lifespan=lifespan)

@app.exception_handler(KeycloakError)
async def global_kc_exception_handler(request: Request, exc: KeycloakError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

# Unified URL format: /AccessManager/Tenants/{tid}/...
# identity router has prefix="/{realm}" → mounts as /AccessManager/Tenants/{tid}/{realm}/...
# We remap it to /AccessManager/Tenants so {realm} == {tid} in the path.
app.include_router(tenants.router,     prefix="/AccessManager")
app.include_router(idp.router,         prefix="/AccessManager/Tenants")
app.include_router(identity.router,    prefix="/AccessManager/Tenants")
app.include_router(common.router,      prefix="/AccessManager/Tenants")
app.include_router(token.router,       prefix="/AccessManager/Tenants")
app.include_router(apps.router,        prefix="/AccessManager/Tenants/System")
app.include_router(permissions.router, prefix="/AccessManager/Tenants/System")
app.include_router(api_keys.router,    prefix="/AccessManager/Tenants")

# New unified ACL and Manifest management APIs (no additional prefix needed,
# endpoints declare their full paths internally)
app.include_router(acls.router)
app.include_router(manifests.router)

'''[仅供演示!!!]挂载静态文件服务 开始'''
ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
app.mount("/ui", StaticFiles(directory=ui_path), name="ui")

@app.get("/")
async def read_index():
    return RedirectResponse(url="/ui/index.html")
'''[仅供演示!!!]挂载静态文件服务 结束'''


@app.get("/api/v1/export-spec", include_in_schema=False)
def export_spec():
    return app.openapi()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8090, reload=False)
