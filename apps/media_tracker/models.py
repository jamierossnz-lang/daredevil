from django.db import models
from django.utils import timezone


class TVShow(models.Model):
    class Status(models.TextChoices):
        RETURNING = 'returning', 'Returning Series'
        ENDED = 'ended', 'Ended'
        CANCELLED = 'cancelled', 'Cancelled'
        IN_PRODUCTION = 'in_production', 'In Production'
        PLANNED = 'planned', 'Planned'

    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=500)
    overview = models.TextField(blank=True)
    poster_path = models.CharField(max_length=500, blank=True)
    backdrop_path = models.CharField(max_length=500, blank=True)
    first_air_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, choices=Status.choices, default=Status.RETURNING)
    number_of_seasons = models.IntegerField(default=0)
    number_of_episodes = models.IntegerField(default=0)
    networks = models.CharField(max_length=500, blank=True)
    genres = models.CharField(max_length=500, blank=True)
    vote_average = models.FloatField(default=0)
    is_favourite = models.BooleanField(default=False)
    monitor_new_episodes = models.BooleanField(default=False)
    monitor_from = models.DateField(null=True, blank=True,
        help_text='Only auto-queue episodes that air on or after this date. Advances as new episodes are queued.')
    added_at = models.DateTimeField(auto_now_add=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def poster_url(self):
        if self.poster_path:
            return f'https://image.tmdb.org/t/p/w500{self.poster_path}'
        return None

    @property
    def backdrop_url(self):
        if self.backdrop_path:
            return f'https://image.tmdb.org/t/p/w1280{self.backdrop_path}'
        return None

    @property
    def is_ongoing(self):
        return self.status in (self.Status.RETURNING, self.Status.IN_PRODUCTION)


class Season(models.Model):
    show = models.ForeignKey(TVShow, on_delete=models.CASCADE, related_name='seasons')
    tmdb_id = models.IntegerField()
    season_number = models.IntegerField()
    name = models.CharField(max_length=500)
    overview = models.TextField(blank=True)
    poster_path = models.CharField(max_length=500, blank=True)
    air_date = models.DateField(null=True, blank=True)
    episode_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ('show', 'season_number')
        ordering = ['season_number']

    def __str__(self):
        return f'{self.show.name} - Season {self.season_number}'

    @property
    def poster_url(self):
        if self.poster_path:
            return f'https://image.tmdb.org/t/p/w300{self.poster_path}'
        return None


class Episode(models.Model):
    class DownloadStatus(models.TextChoices):
        NONE = 'none', 'Not Queued'
        QUEUED = 'queued', 'Queued'
        DOWNLOADING = 'downloading', 'Downloading'
        DOWNLOADED = 'downloaded', 'Downloaded'
        MISSING = 'missing', 'Missing'

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='episodes')
    tmdb_id = models.IntegerField()
    episode_number = models.IntegerField()
    name = models.CharField(max_length=500)
    overview = models.TextField(blank=True)
    air_date = models.DateField(null=True, blank=True)
    still_path = models.CharField(max_length=500, blank=True)
    runtime = models.IntegerField(null=True, blank=True)
    vote_average = models.FloatField(default=0)
    download_status = models.CharField(
        max_length=20, choices=DownloadStatus.choices, default=DownloadStatus.NONE
    )

    class Meta:
        unique_together = ('season', 'episode_number')
        ordering = ['episode_number']

    def __str__(self):
        return f'{self.season.show.name} S{self.season.season_number:02d}E{self.episode_number:02d} - {self.name}'

    @property
    def has_aired(self):
        if self.air_date:
            return self.air_date <= timezone.now().date()
        return False

    @property
    def still_url(self):
        if self.still_path:
            return f'https://image.tmdb.org/t/p/w300{self.still_path}'
        return None


class Movie(models.Model):
    class Status(models.TextChoices):
        RELEASED = 'released', 'Released'
        UPCOMING = 'upcoming', 'Upcoming'
        IN_PRODUCTION = 'in_production', 'In Production'

    class DownloadStatus(models.TextChoices):
        NONE = 'none', 'Not Queued'
        WAITING_RELEASE = 'waiting_release', 'Waiting for Digital Release'
        QUEUED = 'queued', 'Queued'
        DOWNLOADING = 'downloading', 'Downloading'
        DOWNLOADED = 'downloaded', 'Downloaded'

    tmdb_id = models.IntegerField(unique=True)
    title = models.CharField(max_length=500)
    overview = models.TextField(blank=True)
    poster_path = models.CharField(max_length=500, blank=True)
    backdrop_path = models.CharField(max_length=500, blank=True)
    release_date = models.DateField(null=True, blank=True)
    digital_release_date = models.DateField(null=True, blank=True)
    runtime = models.IntegerField(null=True, blank=True)
    genres = models.CharField(max_length=500, blank=True)
    vote_average = models.FloatField(default=0)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.RELEASED)
    download_status = models.CharField(
        max_length=20, choices=DownloadStatus.choices, default=DownloadStatus.NONE
    )
    is_favourite = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

    @property
    def poster_url(self):
        if self.poster_path:
            return f'https://image.tmdb.org/t/p/w500{self.poster_path}'
        return None

    @property
    def backdrop_url(self):
        if self.backdrop_path:
            return f'https://image.tmdb.org/t/p/w1280{self.backdrop_path}'
        return None

    @property
    def is_digitally_available(self):
        """True if the known/estimated digital release date has passed."""
        if self.digital_release_date:
            return self.digital_release_date <= timezone.now().date()
        return False
