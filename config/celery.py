import os

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('daredevil')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@worker_ready.connect
def _warm_on_start(sender, **kwargs):
    """After a deploy or restart the shared cache may be empty, and the
    first visit to the Dashboard, Streaming or Plex would wait on TMDB and
    Plex — fill it straight away rather than at the next 10-minute run."""
    sender.app.send_task('warm_caches')
