#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting server..."
# Check if a custom command is passed, else default to gunicorn
if [ $# -eq 0 ]; then
    exec gunicorn roc_desk.wsgi:application --bind 0.0.0.0:8001 --workers 3 --timeout 120 --access-logfile -
else
    exec "$@"
fi
