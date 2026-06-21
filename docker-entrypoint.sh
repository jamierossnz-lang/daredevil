#!/bin/sh
set -e

if [ -n "$DB_HOST" ]; then
  echo "Waiting for PostgreSQL at $DB_HOST:${DB_PORT:-5432}..."
  until python -c "
import psycopg2, os, sys
try:
    psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '5432'),
        dbname=os.environ.get('DB_NAME', 'daredevil'),
        user=os.environ.get('DB_USER', 'daredevil'),
        password=os.environ.get('DB_PASSWORD', 'daredevil'),
        connect_timeout=3,
    )
except Exception as e:
    sys.exit(1)
" 2>/dev/null; do
    sleep 1
  done
  echo "PostgreSQL is ready."
fi

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
