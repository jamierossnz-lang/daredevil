import os
import re
import shutil
import logging
from celery import shared_task
from django.utils import timezone

log = logging.getLogger('daredevil.downloads.tasks')


_DONE_STATES = {
    'uploading', 'stalledUP', 'forcedUP', 'queuedUP',
    'checkingUP', 'pausedUP', 'moving',
}


@shared_task(name='poll_download_progress')
def poll_download_progress():
    """
    Background replacement for the browser-only progress poll.
    Runs every 5 min; finds DOWNLOADING items that are done in qBT and
    triggers the completed → file-move pipeline without the user needing
    to have the queue page open.
    """
    from .models import DownloadItem, FileMove
    from apps.qbt.client import get_torrents

    items = list(DownloadItem.objects.filter(status=DownloadItem.Status.DOWNLOADING))
    if not items:
        return 'no active downloads'

    try:
        all_torrents = get_torrents()
        torrent_map = {t.hash.lower(): t for t in all_torrents}
    except Exception as e:
        log.warning('poll_download_progress: could not reach qBT — %s', e)
        return f'qBT unreachable: {e}'

    def _norm(s):
        return re.sub(r'[._\-]+', ' ', (s or '').strip().lower())

    completed = 0
    for item in items:
        torrent = torrent_map.get((item.torrent_hash or '').lower())

        if torrent is None:
            # Try to recover hash via name match
            stored_norm = _norm(item.torrent_name)
            title_norm  = _norm(item.title)
            for t in torrent_map.values():
                qbt_norm = _norm(t.name)
                if stored_norm and qbt_norm == stored_norm:
                    torrent = t
                    break
                if title_norm and len(title_norm) >= 8 and qbt_norm.startswith(title_norm):
                    torrent = t
                    break
            if torrent:
                item.torrent_hash = torrent.hash.lower()
                item.save(update_fields=['torrent_hash'])

        if torrent is None:
            continue

        is_done = (torrent.progress or 0) >= 1.0 or (torrent.state or '') in _DONE_STATES
        if not is_done:
            # Update progress even if not done yet
            item.progress = (torrent.progress or 0) * 100
            item.download_speed = torrent.dlspeed or 0
            item.eta_seconds = torrent.eta or 0
            item.size_bytes = torrent.size or 0
            item.save(update_fields=['progress', 'download_speed', 'eta_seconds', 'size_bytes'])
            continue

        if FileMove.objects.filter(download_item=item).exists():
            continue

        item.status = DownloadItem.Status.COMPLETED
        item.progress = 100
        item.completed_at = timezone.now()
        item.save(update_fields=['status', 'progress', 'completed_at'])

        from apps.downloads.views import _mark_media_downloaded, _maybe_queue_file_move
        _mark_media_downloaded(item)
        _maybe_queue_file_move(item, torrent)
        completed += 1
        log.info('poll_download_progress: completed item pk=%d %r', item.pk, item.title)

        from apps.notifications.notify import send as ntfy
        label = item.title + (f' — {item.subtitle}' if item.subtitle else '')
        ntfy('Download Complete', label, tags=['white_check_mark'], category='download_complete')

    return f'checked {len(items)}, completed {completed}'


@shared_task(name='execute_file_move')
def execute_file_move(file_move_id, detected_type=None, completed_path=None):
    from .models import FileMove

    try:
        move = FileMove.objects.get(pk=file_move_id)
    except FileMove.DoesNotExist:
        return

    # TMDB enrichment — do this in the worker so the HTTP request returns immediately.
    if detected_type and completed_path:
        try:
            from apps.qbt.file_naming import (
                parse_movie, parse_tv, sanitise,
                tmdb_enrich_movie, tmdb_enrich_tv,
            )
            basename = os.path.basename(move.source_path.rstrip('/\\'))
            base = completed_path.rstrip(os.sep)
            if detected_type == 'movie':
                title, year = parse_movie(basename)
                movie_obj, proper_title, proper_year = tmdb_enrich_movie(title, year)
                if movie_obj:
                    move.movie_pk = movie_obj.pk
                    folder = f'{proper_title} ({proper_year})' if proper_year else proper_title
                    move.dest_path = os.path.join(base, sanitise(folder))
            else:
                show, year, season, ep_num = parse_tv(basename)
                ep_obj, proper_show, proper_year, ep_title = tmdb_enrich_tv(show, year, season, ep_num)
                if ep_obj:
                    move.episode_pk = ep_obj.pk
                show_folder = sanitise(f'{proper_show} ({proper_year})' if proper_year else proper_show)
                season_folder = f'Season {season:02d}' if season else 'Season 01'
                if ep_title and ep_num is not None:
                    move.dest_path = os.path.join(base, show_folder, season_folder, sanitise(f'E{ep_num:02d}-{ep_title}'))
                else:
                    move.dest_path = os.path.join(base, show_folder, season_folder)
            move.save(update_fields=['dest_path', 'movie_pk', 'episode_pk'])
        except Exception as enrich_err:
            log.warning('execute_file_move id=%d: TMDB enrich failed — %s', file_move_id, enrich_err)

    move.status = FileMove.Status.MOVING
    move.save(update_fields=['status'])
    log.info('execute_file_move id=%d: %r → %r', file_move_id, move.source_path, move.dest_path)

    try:
        os.makedirs(move.dest_path, exist_ok=True)
        src = move.source_path
        if os.path.isdir(src):
            # Move each item inside the folder into dest, then remove the (now empty) source dir
            for entry in os.scandir(src):
                shutil.move(entry.path, os.path.join(move.dest_path, entry.name))
            try:
                os.rmdir(src)
            except OSError:
                pass  # not empty (e.g. non-video files remain) — leave it
        else:
            shutil.move(src, move.dest_path)
        move.status = FileMove.Status.COMPLETED
        move.completed_at = timezone.now()
        move.error_message = ''
        move.save(update_fields=['status', 'completed_at', 'error_message'])
        log.info('execute_file_move id=%d: done', file_move_id)
        _mark_moved_downloaded(move)
        from apps.notifications.notify import send as ntfy
        ntfy('Ready to Watch', f'{move.title} moved to library', priority='low', tags=['tada'], category='file_moved')
    except Exception as e:
        move.status = FileMove.Status.FAILED
        move.error_message = str(e)
        move.save(update_fields=['status', 'error_message'])
        log.error('execute_file_move id=%d: failed — %s', file_move_id, e)
        from apps.notifications.notify import send as ntfy
        ntfy('File Move Failed', f'{move.title} — {e}', priority='high', tags=['x'], category='file_failed')


def _mark_moved_downloaded(move):
    """After a successful file move, mark the associated movie or episode as DOWNLOADED."""
    try:
        if move.movie_pk:
            from apps.media_tracker.models import Movie
            Movie.objects.filter(pk=move.movie_pk).update(
                download_status=Movie.DownloadStatus.DOWNLOADED
            )
            log.info('execute_file_move: marked movie pk=%d as DOWNLOADED', move.movie_pk)
        if move.episode_pk:
            from apps.media_tracker.models import Episode
            Episode.objects.filter(pk=move.episode_pk).update(
                download_status=Episode.DownloadStatus.DOWNLOADED
            )
            log.info('execute_file_move: marked episode pk=%d as DOWNLOADED', move.episode_pk)
    except Exception as e:
        log.warning('execute_file_move: could not update download status — %s', e)
