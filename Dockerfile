# Dockerfile for RoC Support Desk
# Dockerfile for RoC Support Desk
#
# Fast rebuild strategy:
#   - Dockerfile.base holds all apt packages (rebuild only when apt deps change).
#   - This file only runs pip install + copies code.
#   - Code-only changes: only COPY . /app/ re-runs (seconds).
#   - Dependency changes (requirements.txt): pip re-runs with wheel cache (fast).
#   - Apt changes: rebuild Dockerfile.base, then this file.

# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    libffi-dev

COPY requirements.txt .

# Wheel cache persists on host — pip skips re-downloading unchanged packages
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt && \
    pip install --prefix=/install gunicorn psycopg2-binary

# ── Stage 2: runtime — use pre-built base image ───────────────────────────────
FROM roc-desk-base:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

# Copy project source (invalidated on code change — everything above is cached)
COPY . /app/

RUN chmod +x /app/scripts/docker-entrypoint.sh

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
