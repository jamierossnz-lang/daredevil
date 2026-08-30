import os
import shutil
import logging
from celery import shared_task
from django.utils import timezone

from apps.events.dispatch import emit
from apps.events.models import EventType

log = logging.getLogger('daredevil.downloads.tasks')


@shared_task(name='poll_download_progress')
def poll_download_progress():
    """
    Background replacement for the browser-only progress poll.
    Runs every 5 min; finds DOWNLOADING items that are done in qBT and
    triggers the completed → file-move pipeline without the user needing
    to have the queue page open.
    """
    from .models import DownloadItem
    from .sync import find_torrent_for_item, sync_item_progress
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

    completed = 0
    for item in items:
        torrent = find_torrent_for_item(item, torrent_map)
        if torrent is None:
            continue
        sync_item_progress(item, torrent)
        if item.status == DownloadItem.Status.COMPLETED:
            completed += 1

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
        emit(
            EventType.FILE_MOVED,
            log_payload={'move_id': move.pk, 'title': move.title},
            move=move,
            title='Ready to Watch', message=f'{move.title} moved to library',
            priority='low', tags=['tada'],
        )
    except Exception as e:
        move.status = FileMove.Status.FAILED
        move.error_message = str(e)
        move.save(update_fields=['status', 'error_message'])
        log.error('execute_file_move id=%d: failed — %s', file_move_id, e)
        emit(
            EventType.FILE_MOVE_FAILED,
            log_payload={'move_id': move.pk, 'error': str(e)},
            move=move, error=str(e),
            title='File Move Failed', message=f'{move.title} — {e}',
            priority='high', tags=['x'],
        )


def _remove_torrent_from_qbt(move):
    """After a successful file move, remove the torrent (not the files) from qBittorrent."""
    try:
        if not move.download_item:
            return
        torrent_hash = move.download_item.torrent_hash
        if not torrent_hash:
            return
        from apps.qbt.client import delete_torrent
        delete_torrent(torrent_hash, delete_files=False)
        log.info('execute_file_move id=%d: removed torrent %s from qBittorrent', move.pk, torrent_hash)
    except Exception as e:
        log.warning('execute_file_move id=%d: could not remove torrent from qBittorrent — %s', move.pk, e)


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
