#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

if [ "${SKIP_STARTUP_MIGRATIONS:-false}" != "true" ]; then
    echo "Running migrations..."
    python manage.py migrate --noinput
else
    echo "Skipping startup migrations."
fi

# Separate flag from migrations: collectstatic only writes to this container's own
# (ephemeral) filesystem, so unlike migrations it's safe to run on every replica —
# it never races against other pods. Kept independent so multi-replica deployments
# (e.g. Kubernetes) can skip the shared migration step while every pod still serves static.
if [ "${SKIP_STARTUP_COLLECTSTATIC:-false}" != "true" ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput
else
    echo "Skipping startup collectstatic."
fi

echo "Starting server..."
# Daphne handles both HTTP and WebSocket (ASGI). Replaces gunicorn.
if [ $# -eq 0 ]; then
    exec daphne -b 0.0.0.0 -p 8001 roc_desk.asgi:application
else
    exec "$@"
fi
