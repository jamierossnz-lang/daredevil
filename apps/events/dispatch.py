import logging
from django.dispatch import Signal

log = logging.getLogger('daredevil.events')

# Generic in-process pub/sub for domain state changes. Receivers filter with
# @receiver(domain_event, sender=EventType.X) — Signal matches `sender` by
# object identity, so always pass the EventType enum member, never a raw
# string (e.g. one read back from DomainEvent.event_type after a DB round-trip).
domain_event = Signal()

_KEEP_NEWEST = 500


def emit(event_type, log_payload=None, **kwargs):
    """
    Record `event_type` to the DomainEvent audit log and fan it out to every
    connected receiver. `log_payload` must be JSON-safe (pks/strings/numbers) —
    it's what gets persisted. `**kwargs` is passed straight to receivers and
    may carry real ORM objects, since signal dispatch is in-process/synchronous.

    Uses send_robust so one failing receiver can't block its siblings — e.g. a
    notification bug shouldn't prevent a row from being marked downloaded.
    """
    from .models import DomainEvent

    DomainEvent.objects.create(event_type=event_type, payload=log_payload or {})
    keep_ids = list(DomainEvent.objects.values_list('pk', flat=True)[:_KEEP_NEWEST])
    DomainEvent.objects.exclude(pk__in=keep_ids).delete()

    results = domain_event.send_robust(sender=event_type, **kwargs)
    for receiver, response in results:
        if isinstance(response, Exception):
            log.error('event %s: receiver %r failed: %s', event_type, receiver, response)
