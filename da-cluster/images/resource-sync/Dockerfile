FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/*.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    for i in 1 2 3 4 5; do \
      apt-get update && break || echo "apt-get update retry $i..." && sleep 5; \
    done \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r /app/requirements.txt

# Copy resource-sync application
COPY app /app/resource_sync

# Ensure directory is a proper Python package
RUN touch /app/resource_sync/__init__.py

# Compile ext_proc gRPC proto
COPY proto /app/proto
RUN python -m grpc_tools.protoc \
    -I/app/proto \
    --python_out=/app \
    --grpc_python_out=/app \
    /app/proto/ext_proc.proto

# Verify proto compilation
RUN python -c "import ext_proc_pb2; import ext_proc_pb2_grpc; print('proto OK')"

# Smoke-test application import
RUN python -c "from resource_sync.main import app; print('resource_sync OK')"

# http/api: 8080, grpc/ext-proc: 8082
EXPOSE 8080 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "resource_sync.main:app", "--host", "0.0.0.0", "--port", "8080"]
