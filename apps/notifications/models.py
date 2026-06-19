from django.db import models


CATEGORY_META = [
    ('episodes_queued',   'New Episodes Queued',       'fa-tv',               'Monitored show has new aired episodes ready to download'),
    ('movie_available',   'Movie Available Digitally', 'fa-film',             'A waiting movie is now streaming and has started downloading'),
    ('download_complete', 'Download Complete',          'fa-circle-check',     'A torrent has finished downloading'),
    ('download_failed',   'Download / Search Failed',  'fa-circle-xmark',     'No torrent found after all query attempts'),
    ('file_moved',        'File Moved to Library',     'fa-folder-open',      'Download moved to the completed media folder'),
    ('file_failed',       'File Move Failed',          'fa-circle-xmark',     'An error occurred while moving a download to the library'),
    ('storage_warning',   'Storage Warning',           'fa-hard-drive',       'A drive has reached 75 % or 90 % capacity'),
]

CATEGORY_CHOICES = [(k, label) for k, label, _icon, _desc in CATEGORY_META]


class Notification(models.Model):
    class Level(models.TextChoices):
        INFO    = 'info',    'Info'
        SUCCESS = 'success', 'Success'
        WARNING = 'warning', 'Warning'
        ERROR   = 'error',   'Error'

    title      = models.CharField(max_length=200)
    message    = models.TextField()
    level      = models.CharField(max_length=20, choices=Level.choices, default=Level.INFO)
    icon       = models.CharField(max_length=60, blank=True, default='fa-bell')
    category   = models.CharField(max_length=50, blank=True, default='',
                                  choices=CATEGORY_CHOICES + [('', 'Uncategorised')])
    read       = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.level}] {self.title}'


class NotificationPrefs(models.Model):
    """Singleton — always use NotificationPrefs.get()."""

    episodes_queued   = models.BooleanField(default=True)
    movie_available   = models.BooleanField(default=True)
    download_complete = models.BooleanField(default=True)
    download_failed   = models.BooleanField(default=True)
    file_moved        = models.BooleanField(default=True)
    file_failed       = models.BooleanField(default=True)
    storage_warning   = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Notification Preferences'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def is_enabled(self, category):
        return bool(getattr(self, category, True))

    def as_dict(self):
        return {k: getattr(self, k, True) for k, *_ in CATEGORY_META}
