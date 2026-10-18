from django.db import models


class CategoryConfig(models.Model):
    """LEGACY — which qBittorrent categories Daredevil used when it added
    torrents itself. Sonarr/Radarr/Prowlarr now choose their own categories
    (Settings → each app's tab); nothing reads this any more."""
    tv_category = models.CharField(max_length=200, default='tv-shows')
    movie_category = models.CharField(max_length=200, default='movies')

    class Meta:
        verbose_name = 'Category Config'


class ExtraTab(models.Model):
    """User-defined folders to display as additional tabs in the file browser."""
    label = models.CharField(max_length=100)
    path  = models.CharField(max_length=1000)

    class Meta:
        ordering = ['label']
        verbose_name = 'Extra File Browser Tab'

    def __str__(self):
        return f'{self.label} ({self.path})'


class CategoryPath(models.Model):
    """LEGACY — per-category folders from when Daredevil moved files itself.
    No longer editable; only read as the storage-usage fallback
    (apps.plex.utils) until Sonarr/Radarr are connected."""
    category_name = models.CharField(max_length=200, unique=True)
    qbt_save_path = models.CharField(max_length=1000, blank=True,
        help_text="Save path sent to qBittorrent when adding torrents (qBittorrent's container path, e.g. /downloads).")
    download_path = models.CharField(max_length=1000, blank=True,
        help_text="Where Daredevil looks for downloaded files (Daredevil's container path, e.g. /media/downloads).")
    completed_path = models.CharField(max_length=1000, blank=True,
        help_text='Where Daredevil moves files after the download finishes.')

    class Meta:
        ordering = ['category_name']
        verbose_name = 'Category Path'

    def __str__(self):
        return self.category_name
