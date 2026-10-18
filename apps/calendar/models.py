import secrets

from django.db import models


class CalendarFeed(models.Model):
    """
    Singleton — always use CalendarFeed.get(). The secret in the .ics feed's
    URL: Google and Apple Calendar fetch the feed on their own schedule and
    can't log in, so the URL itself is the credential. Reset it from the
    Calendar page if it ever leaks.
    """
    token = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        feed = cls.objects.first()
        if feed is None:
            feed = cls.objects.create(token=secrets.token_urlsafe(24))
        return feed

    def reset(self):
        self.token = secrets.token_urlsafe(24)
        self.save(update_fields=['token', 'updated_at'])
