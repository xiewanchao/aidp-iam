FROM python:3.12-slim

RUN pip install --no-cache-dir \
      psycopg2-binary==2.9.9 \
      PyYAML==6.0.1 \
      kubernetes==29.0.0

WORKDIR /app
COPY provisioner.py bootstrap.py ./

CMD ["python", "provisioner.py"]
