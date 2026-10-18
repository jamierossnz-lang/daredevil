from django.db import models


# What each notification category means now that Sonarr/Radarr run the
# downloads — keys are unchanged from before so saved preferences still apply.
CATEGORY_META = [
    ('episodes_queued',   'Episodes Grabbed',          'fa-tv',               'Sonarr grabbed a release for a monitored episode'),
    ('movie_available',   'Movie Grabbed',             'fa-film',             'Radarr grabbed a release — e.g. a waiting movie came out'),
    ('file_moved',        'Imported to Library',       'fa-file-import',      'Sonarr/Radarr imported a download and Daredevil confirmed the file'),
    ('file_failed',       'Import Problem',            'fa-circle-xmark',     "An import needs attention, or the file couldn't be confirmed on disk"),
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
    download_complete = models.BooleanField(default=True)  # legacy — no longer sent
    download_failed   = models.BooleanField(default=True)  # legacy — no longer sent
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
