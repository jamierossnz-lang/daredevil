from django.db import models
from django.utils import timezone


class DownloadItem(models.Model):
    class MediaType(models.TextChoices):
        EPISODE = 'episode', 'TV Episode'
        MOVIE = 'movie', 'Movie'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SEARCHING = 'searching', 'Searching'
        FOUND = 'found', 'Found'
        DOWNLOADING = 'downloading', 'Downloading'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        WAITING_RELEASE = 'waiting_release', 'Waiting for Release'

    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # References (only one set at a time)
    episode_id = models.IntegerField(null=True, blank=True)
    movie_id = models.IntegerField(null=True, blank=True)

    # Display info (cached to avoid extra queries)
    title = models.CharField(max_length=500)
    subtitle = models.CharField(max_length=500, blank=True)
    poster_path = models.CharField(max_length=500, blank=True)

    # Torrent info
    torrent_hash = models.CharField(max_length=100, blank=True)
    torrent_name = models.CharField(max_length=500, blank=True)
    magnet_link = models.TextField(blank=True)

    # Progress
    progress = models.FloatField(default=0)
    size_bytes = models.BigIntegerField(default=0)
    download_speed = models.BigIntegerField(default=0)
    eta_seconds = models.IntegerField(default=0)

    # Quality preference (movies only)
    quality = models.CharField(max_length=10, default='1080p')

    # Search info for UI display
    search_query = models.CharField(max_length=500, blank=True)
    result_count = models.IntegerField(default=-1)  # -1 = not yet searched

    # Scheduling
    release_date = models.DateField(null=True, blank=True)

    added_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)

    class Meta:
        ordering = ['-added_at']

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'

    @property
    def poster_url(self):
        if self.poster_path:
            return f'https://image.tmdb.org/t/p/w500{self.poster_path}'
        return None

    @property
    def size_formatted(self):
        if not self.size_bytes:
            return '—'
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if self.size_bytes < 1024:
                return f'{self.size_bytes:.1f} {unit}'
            self.size_bytes /= 1024
        return f'{self.size_bytes:.1f} PB'

    @property
    def speed_formatted(self):
        if not self.download_speed:
            return '—'
        speed = self.download_speed
        for unit in ['B/s', 'KB/s', 'MB/s', 'GB/s']:
            if speed < 1024:
                return f'{speed:.1f} {unit}'
            speed /= 1024
        return f'{speed:.1f} GB/s'


class FileMove(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        MOVING = 'moving', 'Moving'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    download_item = models.ForeignKey(
        DownloadItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='file_moves',
    )
    title = models.CharField(max_length=500)
    source_path = models.CharField(max_length=2000)
    dest_path = models.CharField(max_length=2000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'
