# The Frame Shop website: React frontend (built here) + FastAPI backend, one image.
# Build context is the repo root:  docker build -f webapp/Dockerfile .
FROM node:22-slim AS frontend
WORKDIR /fe
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci
COPY webapp/frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY webapp/requirements.txt ./
RUN pip install -r requirements.txt
COPY stores.toml ./
COPY core/ core/
COPY sqlite_extract/__init__.py sqlite_extract/importer.py sqlite_extract/
COPY lsreports/ lsreports/
COPY webapp/*.py webapp/
COPY --from=frontend /fe/dist webapp/frontend/dist
RUN useradd -r app && mkdir -p /cache && chown app /cache
USER app
ENV CACHE_DIR=/cache
CMD exec uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers
