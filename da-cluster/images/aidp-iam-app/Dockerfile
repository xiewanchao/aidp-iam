# aidp-iam-app: 4-in-1 IAM service container
# Bundles keycloak-proxy + pep-proxy + bundle-server + resource-sync
# under supervisord. Pairs with a separate `opa` container in the
# iam-services Pod.
# Build from the repository root:
#   docker build -f da-cluster/images/aidp-iam-app/Dockerfile -t aidp-iam-app:v1 .
#
# Layout inside the image (matches each service's original Dockerfile so
# existing `from app.core...` / `import ext_proc_pb2` imports keep working):
#   /app/app/             keycloak-proxy source (keeps its `app.*` imports)
#   /app/pep_proxy/       pep-proxy source
#   /app/bundle_server/   bundle-server source
#   /app/resource_sync/   resource-sync source
#   /app/ext_authz_pb2*.py  pep-proxy gRPC stubs (top-level for `import ext_authz_pb2`)
#   /app/ext_proc_pb2*.py   resource-sync gRPC stubs (top-level for `import ext_proc_pb2`)
#   /app/data/            bundle-server seed data
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

ARG TARGETARCH
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || true \
    && sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true \
    && for i in 1 2 3 4 5; do \
        apt-get update && break || echo "apt-get update retry $i..." && sleep 5; \
    done \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install combined deps (keycloak-proxy + pep-proxy + bundle-server + resource-sync)
COPY da-cluster/images/aidp-iam-app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    supervisor==4.2.5 \
    -r /app/requirements.txt

# Required runtime directories
RUN mkdir -p /app/data/templates \
             /app/data/tenants \
             /app/bundles \
             /var/log/supervisor

# ── Service code ─────────────────────────────────────────────────────
# keycloak-proxy → /app/app  (must be `app/` so `from app.core ...` resolves)
COPY da-idb-proxy/app /app/app

# pep-proxy → /app/pep_proxy + ext_authz proto → /app/proto
COPY opal-dynamic-policy/pep-proxy/app /app/pep_proxy
COPY opal-dynamic-policy/pep-proxy/proto /app/proto

# bundle-server → /app/bundle_server. Runtime data directories are created
# above; the previous setup script supplied an empty bundle-server/data dir.
COPY opal-dynamic-policy/bundle-server/app /app/bundle_server

# resource-sync → /app/resource_sync + ext_proc proto → /app/rs_proto
COPY resource-sync/app /app/resource_sync
COPY resource-sync/proto /app/rs_proto

# Ensure all are valid Python packages
RUN touch /app/app/__init__.py \
          /app/pep_proxy/__init__.py \
          /app/bundle_server/__init__.py \
          /app/resource_sync/__init__.py

# Compile gRPC stubs to /app top-level (matches each original Dockerfile —
# both `import ext_authz_pb2` and `import ext_proc_pb2` work because /app
# is on PYTHONPATH).
RUN python -m grpc_tools.protoc \
        -I/app/proto \
        --python_out=/app \
        --grpc_python_out=/app \
        /app/proto/ext_authz.proto \
 && python -m grpc_tools.protoc \
        -I/app/rs_proto \
        --python_out=/app \
        --grpc_python_out=/app \
        /app/rs_proto/ext_proc.proto

# Build-time smoke tests — fail the build if any service won't import
RUN python -c "import ext_authz_pb2, ext_authz_pb2_grpc; print('ext_authz proto OK')" \
 && python -c "import ext_proc_pb2, ext_proc_pb2_grpc; print('ext_proc proto OK')" \
 && python -c "from pep_proxy.main import app; print('pep_proxy OK')" \
 && python -c "from bundle_server.main import app; print('bundle_server OK')" \
 && python -c "from resource_sync.main import app; print('resource_sync OK')" \
 && python -c "from app.core.keycloak import KeycloakError; print('keycloak_proxy OK')"

COPY da-cluster/images/aidp-iam-app/supervisord.conf /etc/supervisord.conf

# keycloak-proxy:8090 / pep-proxy:8000 / bundle-server:8001 /
# resource-sync:8080 / pep-proxy gRPC:9000 / resource-sync gRPC:8082
EXPOSE 8000 8001 8080 8082 8090 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health \
     && curl -f http://localhost:8090/AccessManager/Tenants/common/health \
     && curl -f http://localhost:8080/health \
     || exit 1

CMD ["/usr/local/bin/supervisord", "-c", "/etc/supervisord.conf"]
