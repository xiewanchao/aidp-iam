import os
from contextlib import asynccontextmanager

import app.bootstrap
from app.api.v1 import acls, api_keys, apps, common, identity, idp, log_collect, manifests, permissions, tenants, token
from app.core.db import close_pool, get_pool
from app.core.keycloak import KeycloakError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(application: FastAPI):
    await get_pool()
    log_collect.recover_interrupted_log_collect_tasks()
    log_collect.start_oms_log_type_registration()
    yield
    await close_pool()


app = FastAPI(title="AccessManager API", version="2.0.0", lifespan=lifespan)


@app.exception_handler(KeycloakError)
async def global_kc_exception_handler(request: Request, exc: KeycloakError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# Unified URL format: /AccessManager/Tenants/{tid}/...
# identity router has prefix="/{realm}" and mounts as /AccessManager/Tenants/{tid}/{realm}/...
# We remap it to /AccessManager/Tenants so {realm} == {tid} in the path.
app.include_router(tenants.router, prefix="/AccessManager")
app.include_router(idp.router, prefix="/AccessManager/Tenants")
app.include_router(identity.router, prefix="/AccessManager/Tenants")
app.include_router(common.router, prefix="/AccessManager/Tenants")
app.include_router(token.router, prefix="/AccessManager/Tenants")
app.include_router(apps.router, prefix="/AccessManager/Tenants/System")
app.include_router(permissions.router, prefix="/AccessManager/Tenants/System")
app.include_router(api_keys.router, prefix="/AccessManager/Tenants")

# New unified ACL and Manifest management APIs (no additional prefix needed,
# endpoints declare their full paths internally).
app.include_router(acls.router)
app.include_router(manifests.router)
app.include_router(log_collect.router)

# Demo-only static UI mount.
ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
app.mount("/ui", StaticFiles(directory=ui_path), name="ui")


@app.get("/")
async def read_index():
    return RedirectResponse(url="/ui/index.html")


@app.get("/api/v1/export-spec", include_in_schema=False)
def export_spec():
    return app.openapi()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8090, reload=False)
