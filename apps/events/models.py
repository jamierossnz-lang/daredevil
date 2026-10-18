from django.db import models


class EventType(models.TextChoices):
    """Values match apps.notifications.notify `category=` strings 1:1 so the
    notification relay can pass `sender` straight through as `category`.

    The values are kept from before Sonarr/Radarr took over (so old rows and
    notification preferences still line up); the labels describe what now
    emits them — mostly the Sonarr/Radarr webhooks (apps.arr.webhooks)."""
    EPISODE_QUEUED = 'episodes_queued', 'Episode(s) Grabbed'
    MOVIE_AVAILABLE = 'movie_available', 'Movie Grabbed'
    FILE_MOVED = 'file_moved', 'Imported to Library'
    FILE_MOVE_FAILED = 'file_failed', 'Import Problem'
    STORAGE_WARNING = 'storage_warning', 'Storage Warning'
    # No longer emitted — Daredevil doesn't run downloads itself any more.
    # Kept so older log entries still display.
    DOWNLOAD_COMPLETED = 'download_complete', 'Download Completed (legacy)'
    SEARCH_FAILED = 'download_failed', 'Search Failed (legacy)'


class DomainEvent(models.Model):
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_event_type_display()} @ {self.created_at:%Y-%m-%d %H:%M:%S}'
