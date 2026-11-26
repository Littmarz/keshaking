FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /usr/local/bin/uv
WORKDIR /opt/punkt/backend
ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY backend/pyproject.toml ./
RUN uv sync --no-dev
COPY backend/app ./app
COPY deploy/container_app.py ./container_app.py
COPY --from=frontend-build /build/dist /opt/punkt/web

RUN groupadd --gid 10001 punkt \
    && useradd --uid 10001 --gid punkt --no-create-home punkt \
    && install -d -o punkt -g punkt /var/lib/punkt \
    && install -o punkt -g punkt -m 0644 /dev/null /var/lib/punkt/.initialized

USER punkt
EXPOSE 8080
CMD [".venv/bin/uvicorn", "container_app:app", "--host", "0.0.0.0", "--port", "8080"]
