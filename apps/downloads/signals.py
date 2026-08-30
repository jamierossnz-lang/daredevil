from django.dispatch import receiver

from apps.events.dispatch import domain_event
from apps.events.models import EventType


@receiver(domain_event, sender=EventType.DOWNLOAD_COMPLETED, dispatch_uid='downloads.mark_media_downloaded')
def mark_media_downloaded(sender, item, **kwargs):
    from .views import _mark_media_downloaded
    _mark_media_downloaded(item)


@receiver(domain_event, sender=EventType.DOWNLOAD_COMPLETED, dispatch_uid='downloads.queue_file_move')
def queue_file_move(sender, item, torrent, **kwargs):
    from .views import _maybe_queue_file_move
    _maybe_queue_file_move(item, torrent)


@receiver(domain_event, sender=EventType.FILE_MOVED, dispatch_uid='downloads.mark_moved_downloaded')
def mark_moved_downloaded(sender, move, **kwargs):
    from .tasks import _mark_moved_downloaded
    _mark_moved_downloaded(move)


@receiver(domain_event, sender=EventType.FILE_MOVED, dispatch_uid='downloads.remove_torrent')
def remove_torrent_from_qbt(sender, move, **kwargs):
    from .tasks import _remove_torrent_from_qbt
    _remove_torrent_from_qbt(move)
