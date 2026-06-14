#!/bin/bash
# Daredevil — start dev server (and optionally Celery)
cd "$(dirname "$0")"
source venv/bin/activate

echo "🌀  Daredevil starting…"
echo "    http://127.0.0.1:8000"
echo ""

# Optionally start Celery in background if Redis is running
if redis-cli ping >/dev/null 2>&1; then
  echo "✅  Redis detected — starting Celery worker + beat…"
  celery -A config worker -l info --detach --logfile=celery-worker.log --pidfile=celery-worker.pid
  celery -A config beat   -l info --detach --logfile=celery-beat.log   --pidfile=celery-beat.pid \
         --scheduler django_celery_beat.schedulers:DatabaseScheduler
  echo "    Celery logs: celery-worker.log / celery-beat.log"
else
  echo "⚠️   Redis not found — background tasks (episode sync, auto-download) disabled."
  echo "    Install Redis and rerun to enable: brew install redis && brew services start redis"
fi

echo ""
python manage.py runserver
