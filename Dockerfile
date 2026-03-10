FROM ghcr.io/atlanhq/application-sdk-main:2.6.1

WORKDIR /app

COPY --chown=appuser:appuser pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=1000 \
    uv venv .venv && \
    uv sync --locked --no-install-project

COPY --chown=appuser:appuser . .

ENV ATLAN_APP_HTTP_PORT=8000

RUN uv run poe download-components
