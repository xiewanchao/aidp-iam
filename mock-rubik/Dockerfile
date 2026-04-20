FROM python:3.11-slim
RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    flask
WORKDIR /app
COPY app.py .
EXPOSE 8081
CMD ["python", "app.py"]
