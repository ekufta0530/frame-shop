# The SQL MCP server (claude.ai connector) for an on-prem store's lifesaver.sqlite.
# deploy/deploy_mcp.sh stages this directory plus the repo's shared lsreports/ package and builds
# from that. The data comes from GCS at runtime.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lsreports/ lsreports/
COPY mcp_server/ mcp_server/
RUN useradd -r app
USER app
CMD exec uvicorn mcp_server.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers
