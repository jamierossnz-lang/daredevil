from django.db import models


class EventType(models.TextChoices):
    """Values match apps.notifications.notify `category=` strings 1:1 so the
    notification relay can pass `sender` straight through as `category`."""
    DOWNLOAD_COMPLETED = 'download_complete', 'Download Completed'
    FILE_MOVED = 'file_moved', 'File Moved'
    FILE_MOVE_FAILED = 'file_failed', 'File Move Failed'
    EPISODE_QUEUED = 'episodes_queued', 'Episode(s) Queued'
    MOVIE_AVAILABLE = 'movie_available', 'Movie Available'
    SEARCH_FAILED = 'download_failed', 'Search Failed'
    STORAGE_WARNING = 'storage_warning', 'Storage Warning'


class DomainEvent(models.Model):
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_event_type_display()} @ {self.created_at:%Y-%m-%d %H:%M:%S}'
