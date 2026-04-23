# Dockerfile for RoC Support Desk
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    curl \
    libmagic1t64 \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install gunicorn psycopg2-binary

# Copy project
COPY . /app/

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

# Run entrypoint script
ENTRYPOINT ["/app/docker-entrypoint.sh"]
