from django.db import models


class PlexIgnored(models.Model):
    """Items the user has dismissed from the cleanup candidates list."""
    rating_key = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=500, blank=True)
    ignored_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ignored_at']

    def __str__(self):
        return f'{self.title} ({self.rating_key})'
