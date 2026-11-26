FROM node:24.22.0-bookworm-slim@sha256:91acba91b842bec0bf7ea6f5e09c644c7ccad72c4996ed9616d861d1c7cd60d4 AS assets
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build
WORKDIR /build/harness/qwen-live
COPY harness/qwen-live/package*.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM python:3.11.18-slim-bookworm@sha256:ef56ae98435cc40d8a27020b0b32a9941515acd5840f0fba34858bb34dd362d1 AS runtime
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/app/.venv/bin:$PATH
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.13.6 && uv sync --locked --no-dev --no-cache --no-install-project
COPY --from=assets /usr/local/bin/node /usr/local/bin/node
COPY --from=assets /build/web/dist web/dist
COPY --from=assets /build/harness/qwen-live/node_modules harness/qwen-live/node_modules
COPY harness/qwen-live/ harness/qwen-live/
COPY src/ src/
RUN useradd --create-home --uid 10001 agent && node harness/qwen-live/src/bridge.mjs --check && python -c "from livekit import rtc"
USER agent
