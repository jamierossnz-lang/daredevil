from django.dispatch import receiver

from apps.events.dispatch import domain_event
from .notify import send as ntfy


@receiver(domain_event, dispatch_uid='notifications.relay')
def relay_to_ntfy(sender, title=None, message=None, priority='default', tags=None, **kwargs):
    """Listens to every domain event (no `sender` filter) and forwards to ntfy
    whenever the emitter included notification copy — `sender` is the EventType,
    which already matches the `category=` strings notify.send expects."""
    if title is None:
        return
    ntfy(title, message, priority=priority, tags=tags, category=sender)
