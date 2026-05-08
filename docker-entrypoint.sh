#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting server..."
# Daphne handles both HTTP and WebSocket (ASGI). Replaces gunicorn.
if [ $# -eq 0 ]; then
    exec daphne -b 0.0.0.0 -p 8001 roc_desk.asgi:application
else
    exec "$@"
fi
