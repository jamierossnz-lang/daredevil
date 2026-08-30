import re
import logging
from django.utils import timezone

from .models import DownloadItem, FileMove
from apps.events.dispatch import emit
from apps.events.models import EventType

log = logging.getLogger('daredevil.downloads.sync')

# States that mean the download is finished (seeding or qBT is relocating files)
_DONE_STATES = {
    'uploading', 'stalledUP', 'forcedUP', 'queuedUP',
    'checkingUP', 'pausedUP',
    'moving',  # qBT is moving the completed files — still counts as done for us
}


def _norm(s):
    return re.sub(r'[._\-]+', ' ', (s or '').strip().lower())


def find_torrent_for_item(item, torrent_map):
    """Look up item's torrent by hash, falling back to name-match recovery for
    DOWNLOADING items whose hash is missing or stale."""
    torrent = torrent_map.get(item.torrent_hash.lower()) if item.torrent_hash else None
    if torrent is not None:
        return torrent
    if item.status != DownloadItem.Status.DOWNLOADING:
        return None

    stored_norm = _norm(item.torrent_name)
    title_norm = _norm(item.title)

    # TV episodes: require the SxxExx code so we never match the wrong episode —
    # item.title is just the show name, which would match any of its episodes.
    ep_code = None
    if item.media_type == DownloadItem.MediaType.EPISODE and item.subtitle:
        m = re.search(r's\d+e\d+', item.subtitle, re.IGNORECASE)
        if m:
            ep_code = m.group(0).lower()

    for t in torrent_map.values():
        qbt_norm = _norm(t.name)
        if stored_norm and qbt_norm == stored_norm:
            torrent = t
            break
        if item.media_type == DownloadItem.MediaType.EPISODE:
            if ep_code and ep_code in qbt_norm and title_norm and title_norm in qbt_norm:
                torrent = t
                break
        elif title_norm and len(title_norm) >= 8 and qbt_norm.startswith(title_norm):
            torrent = t
            break

    if torrent:
        item.torrent_hash = torrent.hash.lower()
        item.save(update_fields=['torrent_hash'])
        log.info('find_torrent_for_item: re-linked item pk=%d to hash=%s via name match', item.pk, item.torrent_hash)
    return torrent


def sync_item_progress(item, torrent):
    """
    Update `item` from `torrent`'s live qBT state and, if just-completed, mark it
    COMPLETED and emit DOWNLOAD_COMPLETED. Returns a JSON-serialisable status dict.

    Shared by the Celery Beat poller (poll_download_progress) and the queue
    page's live status endpoint (queue_status_json) so completion detection —
    previously forked between the two — exists in exactly one place.
    """
    if torrent is None:
        return _serialize(item, None)

    item.progress = (torrent.progress or 0) * 100
    item.download_speed = torrent.dlspeed or 0
    item.eta_seconds = torrent.eta or 0
    item.size_bytes = torrent.size or 0
    item.save(update_fields=['progress', 'download_speed', 'eta_seconds', 'size_bytes'])

    is_done = (torrent.progress or 0) >= 1.0 or (torrent.state or '') in _DONE_STATES
    if is_done and not FileMove.objects.filter(download_item=item).exists():
        item.status = DownloadItem.Status.COMPLETED
        item.progress = 100
        item.completed_at = timezone.now()
        item.save(update_fields=['status', 'progress', 'completed_at'])
        label = item.title + (f' — {item.subtitle}' if item.subtitle else '')
        emit(
            EventType.DOWNLOAD_COMPLETED,
            log_payload={'item_pk': item.pk, 'title': item.title, 'torrent_hash': torrent.hash},
            item=item, torrent=torrent,
            title='Download Complete', message=label, tags=['white_check_mark'],
        )
        log.info('sync_item_progress: completed item pk=%d %r', item.pk, item.title)

    return _serialize(item, torrent)


def build_queue_status():
    """
    Fetch qBT once, sync every active item against it, and return (data, qbt_connected).

    Shared by queue_status_json (one-shot poll) and queue_status_stream (SSE) so
    there's exactly one place that decides what "current queue status" means.
    """
    from .models import DownloadItem
    from apps.qbt.client import get_torrents

    items = list(DownloadItem.objects.filter(
        status__in=[DownloadItem.Status.DOWNLOADING, DownloadItem.Status.SEARCHING]
    ))

    torrent_map = {}
    qbt_connected = True
    needs_qbt = any(
        it.torrent_hash or it.status == DownloadItem.Status.DOWNLOADING
        for it in items
    )
    if needs_qbt:
        try:
            all_torrents = get_torrents()
            torrent_map = {t.hash.lower(): t for t in all_torrents}
        except Exception as e:
            qbt_connected = False
            log.warning('build_queue_status: could not fetch torrents from qBT — %s', e)

    data = [sync_item_progress(item, find_torrent_for_item(item, torrent_map)) for item in items]
    return data, qbt_connected


def _serialize(item, torrent):
    return {
        'id': item.pk,
        'status': item.status,
        'progress': round(item.progress, 1),
        'speed': item.speed_formatted,
        'eta': format_eta(item.eta_seconds),
        'qbt_state': getattr(torrent, 'state', None),
        'search_query': item.search_query,
        'result_count': item.result_count,
    }


def format_eta(seconds):
    if not seconds or seconds < 0:
        return '—'
    if seconds >= 3600:
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m'
    if seconds >= 60:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds}s'
