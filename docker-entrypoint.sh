#!/bin/sh
set -e

echo "Running migrations..."
python manage.py migrate --noinput

# Only the web server needs schedules seeded and static files collected.
# Celery worker and beat skip these to start faster.
if [ "${1:-}" = "gunicorn" ]; then
  echo "Seeding Celery beat schedules..."
  python manage.py setup_schedules

  echo "Collecting static files..."
  python manage.py collectstatic --noinput --clear
fi

echo "Starting: $*"
exec "$@"
