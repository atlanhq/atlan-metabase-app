# syntax=docker/dockerfile:1
FROM registry.atlan.com/public/app-runtime-base:3

# git is required for uv to fetch git-sourced dependencies (atlan-application-sdk)
USER root
RUN apk add --no-cache git
USER appuser

WORKDIR /app

# Copy lock files first for dependency caching
COPY --chown=appuser:appuser pyproject.toml uv.lock ./

# Install dependencies (excluding the project itself) into a new venv
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=1000 \
    uv venv .venv && \
    uv sync --locked --no-install-project --no-dev

# Copy application code only
COPY --chown=appuser:appuser app/ app/

ENV ATLAN_APP_MODULE=app.connector:MetabaseApp
ENV ATLAN_CONTRACT_GENERATED_DIR=/app/app/generated
ENV APPLICATION_SDK_ENABLE_EVENT_INTERCEPTOR=false
