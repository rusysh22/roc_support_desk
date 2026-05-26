# Dockerfile for RoC Support Desk
# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Build-time only: gcc, build-essential, libpq-dev, libffi-dev needed to compile C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt && \
    pip install --no-cache-dir --prefix=/install gunicorn psycopg2-binary

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Runtime only: libpq5, libmagic1 (python-magic), curl (healthcheck), Pango/Cairo (WeasyPrint PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libmagic1t64 \
    curl \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled packages from builder
COPY --from=builder /install /usr/local

# Copy project
COPY . /app/

RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
