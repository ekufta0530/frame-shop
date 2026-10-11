# One image for both Cloud Run services; deploy_mcp.sh overrides CMD for the MCP server.
# The data comes from GCS at runtime.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lsreports/ lsreports/
COPY web/ web/
COPY mcp_server/ mcp_server/
RUN useradd -r app
USER app
CMD exec uvicorn web.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers
