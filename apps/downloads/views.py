import re
import logging
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import DownloadItem, FileMove
from apps.qbt.client import pause_torrent, resume_torrent, delete_torrent
from apps.events.dispatch import emit
from apps.events.models import EventType

log = logging.getLogger('daredevil.downloads')


def queue(request):
    active = DownloadItem.objects.filter(
        status__in=[
            DownloadItem.Status.PENDING,
            DownloadItem.Status.SEARCHING,
            DownloadItem.Status.FOUND,
            DownloadItem.Status.DOWNLOADING,
        ]
    ).order_by('-added_at')
    waiting = DownloadItem.objects.filter(
        status=DownloadItem.Status.WAITING_RELEASE
    ).order_by('release_date')
    completed = DownloadItem.objects.filter(
        status=DownloadItem.Status.COMPLETED
    ).order_by('-completed_at')[:50]
    failed = DownloadItem.objects.filter(
        status=DownloadItem.Status.FAILED
    ).order_by('-added_at')

    context = {
        'active': list(active),
        'waiting': list(waiting),
        'completed': list(completed),
        'failed': list(failed),
    }
    return render(request, 'downloads/queue.html', context)


def queue_status_json(request):
    """Polling endpoint — syncs progress from qBittorrent and returns current active items."""
    from .sync import build_queue_status
    data, qbt_connected = build_queue_status()
    return JsonResponse({'items': data, 'qbt_connected': qbt_connected})


def queue_status_stream(request):
    """
    SSE replacement for the 3s browser poll — pushes updates as they occur.
    Each connection is capped at ~55s; EventSource reconnects transparently,
    which doubles as a self-heal for a generator sitting on a stale DB connection.
    """
    import json
    import time
    from django.db import close_old_connections
    from django.http import StreamingHttpResponse
    from .sync import build_queue_status

    def event_stream():
        deadline = time.monotonic() + 55
        last_payload = None
        while time.monotonic() < deadline:
            close_old_connections()
            try:
                data, qbt_connected = build_queue_status()
                payload = json.dumps({'items': data, 'qbt_connected': qbt_connected})
            except Exception as e:
                log.error('queue_status_stream: tick failed — %s', e)
                yield ': error\n\n'
                time.sleep(2)
                continue
            if payload != last_payload:
                yield f'data: {payload}\n\n'
                last_payload = payload
            else:
                yield ': heartbeat\n\n'
            time.sleep(2)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@require_POST
def item_pause(request, pk):
    item = get_object_or_404(DownloadItem, pk=pk)
    if item.torrent_hash:
        try:
            pause_torrent(item.torrent_hash)
        except Exception:
            pass
    return JsonResponse({'ok': True})


@require_POST
def item_resume(request, pk):
    item = get_object_or_404(DownloadItem, pk=pk)
    if item.torrent_hash:
        try:
            resume_torrent(item.torrent_hash)
        except Exception:
            pass
    return JsonResponse({'ok': True})


@require_POST
def item_delete(request, pk):
    item = get_object_or_404(DownloadItem, pk=pk)
    delete_files = request.POST.get('delete_files', 'false') == 'true'
    if item.torrent_hash:
        try:
            delete_torrent(item.torrent_hash, delete_files=delete_files)
        except Exception:
            pass
    _reset_media_status(item)
    item.delete()
    return JsonResponse({'ok': True})


@require_POST
def item_retry(request, pk):
    item = get_object_or_404(DownloadItem, pk=pk)

    # Rebuild search_query in case it was empty (items created before this was added)
    if not item.search_query:
        item.search_query = _build_search_query(item)
        log.info('item_retry pk=%s: rebuilt search_query=%r', pk, item.search_query)

    item.status = DownloadItem.Status.SEARCHING
    item.error_message = ''
    item.result_count = -1
    item.retry_count += 1
    item.save(update_fields=['status', 'error_message', 'result_count', 'retry_count', 'search_query'])
    log.info('item_retry pk=%s: reset to SEARCHING, search_query=%r', pk, item.search_query)
    try:
        from apps.media_tracker.tasks import search_and_download
        search_and_download.delay(item.id)
    except Exception as e:
        log.warning('item_retry pk=%s: could not queue Celery task (%s) — frontend will handle it', pk, e)
    return JsonResponse({'ok': True, 'search_query': item.search_query})


@require_POST
def item_begin_download(request, pk):
    """Called by the queue page after it finds a torrent — add magnet and move item to DOWNLOADING."""
    item = get_object_or_404(DownloadItem, pk=pk)
    magnet = request.POST.get('magnet', '').strip()
    torrent_name = request.POST.get('name', '')
    result_count = int(request.POST.get('result_count', 0))
    log.info('item_begin_download pk=%s: name=%r result_count=%d magnet=%s', pk, torrent_name, result_count, magnet[:60] if magnet else '(none)')

    if not magnet:
        return JsonResponse({'error': 'No magnet provided'}, status=400)

    try:
        from apps.qbt.client import add_torrent_and_resolve_hash
        from apps.qbt.models import CategoryConfig, CategoryPath
        cfg = CategoryConfig.get()
        is_tv = item.media_type in (DownloadItem.MediaType.EPISODE, DownloadItem.MediaType.SEASON)
        category = cfg.tv_category if is_tv else cfg.movie_category
        cat_path = CategoryPath.objects.filter(category_name=category).first()
        qbt_save_path = cat_path.qbt_save_path if cat_path else None

        torrent_hash = add_torrent_and_resolve_hash(magnet, save_path=qbt_save_path or None, category=category or None)
        if not torrent_hash:
            log.warning('item_begin_download pk=%s: could not resolve hash — will retry via poll name-match', pk)

        item.status = DownloadItem.Status.DOWNLOADING
        item.torrent_name = torrent_name
        item.magnet_link = magnet
        item.torrent_hash = torrent_hash
        item.result_count = result_count
        item.started_at = timezone.now()
        item.save(update_fields=[
            'status', 'torrent_name', 'magnet_link', 'torrent_hash',
            'result_count', 'started_at',
        ])
        _mark_media_downloading(item)
        return JsonResponse({'ok': True, 'hash': torrent_hash})
    except Exception as e:
        item.status = DownloadItem.Status.FAILED
        item.error_message = f'Failed to add to qBittorrent: {e}'
        item.save(update_fields=['status', 'error_message'])
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def item_season_fallback(request, pk):
    """Called by the queue page when a season-pack search finds nothing — splits
    the season item into individual per-episode downloads (the pre-pack method)."""
    item = get_object_or_404(DownloadItem, pk=pk)
    if item.media_type != DownloadItem.MediaType.SEASON:
        return JsonResponse({'error': 'not a season item'}, status=400)
    from apps.media_tracker.tasks import _fallback_season_to_episodes
    created = _fallback_season_to_episodes(item)
    log.info('item_season_fallback pk=%s: queued %d individual episode(s)', pk, created)
    return JsonResponse({'ok': True, 'queued': created})


@require_POST
def item_search_failed(request, pk):
    """Called by the queue page when no results were found."""
    item = get_object_or_404(DownloadItem, pk=pk)
    item.status = DownloadItem.Status.FAILED
    item.error_message = request.POST.get('error', 'No results found')
    item.result_count = int(request.POST.get('result_count', 0))
    item.save(update_fields=['status', 'error_message', 'result_count'])
    log.warning('item_search_failed pk=%s: result_count=%d error=%r', pk, item.result_count, item.error_message)
    _reset_media_status(item)
    return JsonResponse({'ok': True})


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_search_query(item):
    """Derive a search query from whatever data is available on the item."""
    if item.media_type == DownloadItem.MediaType.EPISODE:
        # subtitle is str(Episode) = "Show S01E11 - Episode Name" — strip after " - "
        base = item.subtitle.split(' - ')[0].strip() if item.subtitle else item.title
        # Ensure it has SxxExx; if not just use as-is
        return f'{base} 1080p'
    else:
        year = str(item.release_date.year) if item.release_date else ''
        quality = item.quality or '1080p'
        return ' '.join(filter(None, [item.title, year, quality]))


def _reset_media_status(item):
    """When a download item is removed, restore the media's status to NONE so it can be re-queued."""
    from apps.media_tracker.models import Episode, Movie
    if item.media_type == DownloadItem.MediaType.EPISODE and item.episode_id:
        Episode.objects.filter(pk=item.episode_id).update(
            download_status=Episode.DownloadStatus.NONE
        )
    elif item.media_type == DownloadItem.MediaType.SEASON and item.season_id:
        Episode.objects.filter(season_id=item.season_id).update(
            download_status=Episode.DownloadStatus.NONE
        )
    elif item.media_type == DownloadItem.MediaType.MOVIE and item.movie_id:
        Movie.objects.filter(pk=item.movie_id).update(
            download_status=Movie.DownloadStatus.NONE
        )


def _mark_media_downloading(item):
    from apps.media_tracker.models import Episode, Movie
    if item.media_type == DownloadItem.MediaType.EPISODE and item.episode_id:
        Episode.objects.filter(pk=item.episode_id).update(
            download_status=Episode.DownloadStatus.DOWNLOADING
        )
    elif item.media_type == DownloadItem.MediaType.SEASON and item.season_id:
        Episode.objects.filter(season_id=item.season_id).update(
            download_status=Episode.DownloadStatus.DOWNLOADING
        )
    elif item.media_type == DownloadItem.MediaType.MOVIE and item.movie_id:
        Movie.objects.filter(pk=item.movie_id).update(
            download_status=Movie.DownloadStatus.DOWNLOADING
        )


def _mark_media_downloaded(item):
    from apps.media_tracker.models import Episode, Movie
    if item.media_type == DownloadItem.MediaType.EPISODE and item.episode_id:
        Episode.objects.filter(pk=item.episode_id).update(
            download_status=Episode.DownloadStatus.DOWNLOADED
        )
    elif item.media_type == DownloadItem.MediaType.SEASON and item.season_id:
        Episode.objects.filter(season_id=item.season_id).update(
            download_status=Episode.DownloadStatus.DOWNLOADED
        )
    elif item.media_type == DownloadItem.MediaType.MOVIE and item.movie_id:
        Movie.objects.filter(pk=item.movie_id).update(
            download_status=Movie.DownloadStatus.DOWNLOADED
        )


# ── File-move helpers ─────────────────────────────────────────────────────────




def _sanitise_name(name):
    """Strip characters that are illegal in Windows/macOS filenames."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name or '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().rstrip('. ')
    return cleaned or 'Unknown'


def _path_join(base, *parts):
    """Join path components using the same separator already present in base."""
    sep = '\\' if (len(base) >= 2 and base[1] == ':') or ('\\' in base) else '/'
    result = base.rstrip('/\\')
    for part in parts:
        result = f'{result}{sep}{part}'
    return result


def _compute_plex_dest(item, base_path):
    """
    Return the Plex-standard destination for a DownloadItem.

    Movies : {base}/{Title} ({Year})
    TV     : {base}/{Show} ({Year})/Season XX/EXX-Episode Name
    """
    clean_title = _sanitise_name(item.title)

    if item.media_type == DownloadItem.MediaType.MOVIE:
        # Always use the theatrical release year for the Plex folder, never the digital date
        year = None
        if item.movie_id:
            try:
                from apps.media_tracker.models import Movie
                m = Movie.objects.only('release_date').get(pk=item.movie_id)
                if m.release_date:
                    year = m.release_date.year
            except Exception:
                pass
        if year is None and item.release_date:
            year = item.release_date.year
        title_year = f'{clean_title} ({year})' if year else clean_title
        return _path_join(base_path, title_year)

    year       = item.release_date.year if item.release_date else None
    title_year = f'{clean_title} ({year})' if year else clean_title

    # TV episode — pull season + episode info and use the show's premiere year
    season_num = 1
    ep_num     = None
    ep_name    = None
    if item.season_id and not item.episode_id:
        # Season pack — files inside are already named SxxExx, so they land flat in
        # the season folder and Plex's own scanner resolves each episode from the name.
        try:
            from apps.media_tracker.models import Season
            season     = Season.objects.select_related('show').get(pk=item.season_id)
            season_num = season.season_number
            show_year  = season.show.first_air_date.year if season.show.first_air_date else None
            if show_year:
                title_year = f'{clean_title} ({show_year})'
        except Exception:
            pass
    elif item.episode_id:
        try:
            from apps.media_tracker.models import Episode
            ep         = Episode.objects.select_related('season__show').get(pk=item.episode_id)
            season_num = ep.season.season_number
            ep_num     = ep.episode_number
            ep_name    = _sanitise_name(ep.name) if ep.name else None
            show_year  = ep.season.show.first_air_date.year if ep.season.show.first_air_date else None
            if show_year:
                title_year = f'{clean_title} ({show_year})'
        except Exception:
            pass

    season_folder = f'Season {season_num:02d}'

    if ep_num is not None:
        ep_folder = f'E{ep_num:02d}-{ep_name}' if ep_name else f'E{ep_num:02d}'
        return _path_join(base_path, title_year, season_folder, ep_folder)

    return _path_join(base_path, title_year, season_folder)





def _maybe_queue_file_move(item, torrent):
    """If the category has a completed_path configured, create a FileMove and start it."""
    from apps.qbt.models import CategoryConfig, CategoryPath
    import os

    cfg      = CategoryConfig.get()
    is_tv    = item.media_type in (DownloadItem.MediaType.EPISODE, DownloadItem.MediaType.SEASON)
    category = cfg.tv_category if is_tv else cfg.movie_category
    cat_path = CategoryPath.objects.filter(category_name=category).first()
    if not cat_path or not cat_path.completed_path:
        return

    # Source: prefer content_path when it's accessible on this filesystem (same-machine
    # setup) because it includes the correct filename and extension.  Fall back to
    # download_path + torrent name for cross-machine / network-mount setups where the
    # qBT-side path isn't valid here.
    torrent_name  = getattr(torrent, 'name', '')
    content_path  = getattr(torrent, 'content_path', None)

    # Translate content_path from qBT's container path to Daredevil's container path.
    # e.g. /downloads/www.UIndex.org - Show S01E02/ → /media/downloads/www.UIndex.org - Show S01E02/
    # This handles torrents whose folder name differs from torrent.name (site-prefixed releases).
    source = None
    if content_path and cat_path and cat_path.qbt_save_path and cat_path.download_path:
        qbt_prefix = cat_path.qbt_save_path.rstrip('/')
        if content_path.startswith(qbt_prefix):
            translated = cat_path.download_path.rstrip('/') + content_path[len(qbt_prefix):]
            # Use the top-level folder (first path component after the prefix) as source
            rel = translated[len(cat_path.download_path.rstrip('/')):]
            top = rel.lstrip('/').split('/')[0]
            if top:
                candidate = os.path.join(cat_path.download_path.rstrip('/'), top)
                if os.path.exists(candidate):
                    source = candidate

    if source is None:
        if content_path and os.path.exists(content_path):
            source = content_path
        elif content_path and cat_path and cat_path.download_path:
            # qbt_save_path not configured or prefix didn't match — try resolving
            # the actual filename from content_path against our download_path.
            # This handles cases where torrent.name differs from the real file name.
            fname = os.path.basename(content_path.rstrip('/'))
            candidate = os.path.join(cat_path.download_path.rstrip('/'), fname)
            if os.path.exists(candidate):
                source = candidate
            elif cat_path.download_path and torrent_name:
                source = os.path.join(cat_path.download_path, torrent_name)
            else:
                source = content_path or getattr(torrent, 'save_path', None)
        elif cat_path and cat_path.download_path and torrent_name:
            source = os.path.join(cat_path.download_path, torrent_name)
        else:
            source = content_path or getattr(torrent, 'save_path', None)

    log.info(
        '_maybe_queue_file_move item=%d: torrent_name=%r content_path=%r qbt_save_path=%r download_path=%r → source=%r',
        item.id, torrent_name, content_path,
        cat_path.qbt_save_path if cat_path else None,
        cat_path.download_path if cat_path else None,
        source,
    )

    if not source:
        log.warning('_maybe_queue_file_move item=%d: cannot determine source path, skipping', item.id)
        return

    if FileMove.objects.filter(download_item=item).exists():
        return

    dest = _compute_plex_dest(item, cat_path.completed_path)

    move = FileMove.objects.create(
        download_item=item,
        title=f'{item.title} — {item.subtitle}' if item.subtitle else item.title,
        source_path=source,
        dest_path=dest,
        status=FileMove.Status.PENDING,
    )
    log.info('_maybe_queue_file_move item=%d: FileMove id=%d  %r → %r', item.id, move.id, source, dest)

    import threading
    if _claim_move_slot(move.id):
        threading.Thread(target=_run_file_move, args=(move.id,), daemon=True).start()
    else:
        log.info('_maybe_queue_file_move item=%d: another auto move is in progress — FileMove id=%d queued as pending', item.id, move.id)


# Only one auto-triggered (queue-completion) move runs at a time so a batch of
# downloads finishing together doesn't saturate disk I/O with parallel moves.
# Manually clicking "Run" on the moves page bypasses this and force-starts
# immediately (see move_retry) — that's the "unless manually forced" escape hatch.
_STALE_MOVE_MINUTES = 30


def _claim_move_slot(move_id):
    """
    Atomically flip this FileMove to MOVING only if no other auto-queued move is
    currently MOVING. A single UPDATE is atomic even across the separate
    threads/processes that can trigger a move, so no extra locking is needed.
    """
    from datetime import timedelta
    from django.db import connection

    # Recover from a server restart that killed a move mid-flight — otherwise a
    # permanently-stuck MOVING row would block the queue forever.
    cutoff = timezone.now() - timedelta(minutes=_STALE_MOVE_MINUTES)
    FileMove.objects.filter(status=FileMove.Status.MOVING, created_at__lt=cutoff).update(
        status=FileMove.Status.FAILED,
        error_message='Move interrupted (app restarted) — click Run to retry',
    )

    table = FileMove._meta.db_table
    with connection.cursor() as cur:
        cur.execute(
            f'UPDATE {table} SET status = %s '
            f'WHERE id = %s AND NOT EXISTS (SELECT 1 FROM {table} WHERE status = %s)',
            [FileMove.Status.MOVING, move_id, FileMove.Status.MOVING],
        )
        return cur.rowcount == 1


def _advance_move_queue():
    """After an auto-queued move finishes (or fails), start the oldest PENDING one."""
    import threading
    next_move = FileMove.objects.filter(status=FileMove.Status.PENDING).order_by('created_at').first()
    if next_move and _claim_move_slot(next_move.id):
        threading.Thread(target=_run_file_move, args=(next_move.id,), daemon=True).start()


def _path_size(path):
    import os
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(root, fname))
            except OSError:
                pass
    return total


def _move_one_file(src_file, dest_file, move_id, moved_so_far):
    """
    Move a single file into dest_file, returning the new cumulative bytes-moved total.

    Same-filesystem moves are an instant atomic rename — no bytes are actually
    copied, so progress just jumps by the full file size. Cross-filesystem moves
    let shutil do the copy (OS-accelerated — sendfile/fcopyfile — rather than a
    manual Python read/write loop); progress is derived from the destination
    file's actual on-disk size, polled once a second, so it reflects real bytes
    written rather than our own bookkeeping.
    """
    import os, shutil, threading

    same_fs = False
    try:
        same_fs = os.stat(src_file).st_dev == os.stat(os.path.dirname(dest_file)).st_dev
    except OSError:
        pass

    file_size = os.path.getsize(src_file)

    if same_fs:
        shutil.move(src_file, dest_file)
        moved_so_far += file_size
        FileMove.objects.filter(pk=move_id).update(bytes_moved=moved_so_far)
        return moved_so_far

    stop_event = threading.Event()

    def _poll_dest_size():
        while not stop_event.wait(1):
            try:
                size_now = os.path.getsize(dest_file)
            except OSError:
                continue
            FileMove.objects.filter(pk=move_id).update(bytes_moved=moved_so_far + min(size_now, file_size))

    poller = threading.Thread(target=_poll_dest_size, daemon=True)
    poller.start()
    try:
        shutil.move(src_file, dest_file)
    finally:
        stop_event.set()
        poller.join()

    moved_so_far += file_size
    FileMove.objects.filter(pk=move_id).update(bytes_moved=moved_so_far)
    return moved_so_far


def _run_file_move(move_id):
    """Move everything at source_path into dest_path."""
    import os
    try:
        move = FileMove.objects.get(pk=move_id)
        move.status = FileMove.Status.MOVING
        move.save(update_fields=['status'])

        source_path = move.source_path
        dest_path   = move.dest_path

        if not os.path.exists(source_path):
            raise FileNotFoundError(f'Source not found: {source_path}')

        os.makedirs(dest_path, exist_ok=True)

        total_bytes = _path_size(source_path)
        FileMove.objects.filter(pk=move_id).update(bytes_total=total_bytes, bytes_moved=0)
        moved = 0

        if os.path.isfile(source_path):
            # Single file — move it directly into dest_path
            dest_file = os.path.join(dest_path, os.path.basename(source_path))
            moved = _move_one_file(source_path, dest_file, move_id, moved)
            log.info('_run_file_move id=%d: moved file → %r', move_id, dest_file)
        else:
            # Directory — move every file inside it into dest_path (flat)
            for root, _dirs, files in os.walk(source_path):
                for fname in files:
                    src_file  = os.path.join(root, fname)
                    dest_file = os.path.join(dest_path, fname)
                    moved = _move_one_file(src_file, dest_file, move_id, moved)
                    log.info('_run_file_move id=%d: moved %r', move_id, fname)

        move.status        = FileMove.Status.COMPLETED
        move.completed_at  = timezone.now()
        move.error_message = ''
        move.save(update_fields=['status', 'completed_at', 'error_message'])
        log.info('_run_file_move id=%d: completed → %r', move_id, dest_path)

        # Torrent removal from qBittorrent happens via the FILE_MOVED signal
        # (downloads.signals.remove_torrent_from_qbt) — same event, same
        # receiver the manual move-from-file-browser path already uses.
        emit(
            EventType.FILE_MOVED,
            log_payload={'move_id': move.pk, 'title': move.title},
            move=move,
            title='Ready to Watch', message=f'{move.title} moved to library',
            priority='low', tags=['tada'],
        )
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        try:
            move_obj = FileMove.objects.filter(pk=move_id).first()
            if move_obj:
                move_obj.status = FileMove.Status.FAILED
                move_obj.error_message = err
                move_obj.save(update_fields=['status', 'error_message'])
                emit(
                    EventType.FILE_MOVE_FAILED,
                    log_payload={'move_id': move_obj.pk, 'error': err},
                    move=move_obj, error=err,
                    title='File Move Failed', message=f'{move_obj.title} — {err}',
                    priority='high', tags=['x'],
                )
        except Exception:
            pass
        log.error('_run_file_move id=%d: failed — %s', move_id, err)
    finally:
        _advance_move_queue()


# ── File Move views ───────────────────────────────────────────────────────────

def moves_page(request):
    from django.core.paginator import Paginator
    all_moves = FileMove.objects.select_related('download_item').order_by('-created_at')
    counts = {
        'pending': all_moves.filter(status=FileMove.Status.PENDING).count(),
        'moving': all_moves.filter(status=FileMove.Status.MOVING).count(),
        'completed': all_moves.filter(status=FileMove.Status.COMPLETED).count(),
        'failed': all_moves.filter(status=FileMove.Status.FAILED).count(),
    }
    status_filter = request.GET.get('status', '')
    valid_statuses = {s.value for s in FileMove.Status}
    filtered = all_moves.filter(status=status_filter) if status_filter in valid_statuses else all_moves
    paginator = Paginator(filtered, 25)
    moves = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'downloads/moves.html', {
        'moves': moves, 'counts': counts, 'page_obj': moves, 'status_filter': status_filter,
    })


def _serialize_move(m):
    return {
        'id': m.pk,
        'status': m.status,
        'bytes_moved': m.bytes_moved,
        'bytes_total': m.bytes_total,
        'progress_pct': m.progress_pct,
    }


def moves_status_json(request):
    """Polling endpoint for the moves page — bytes-moved progress for active moves."""
    active = FileMove.objects.filter(status__in=[FileMove.Status.MOVING, FileMove.Status.PENDING])
    return JsonResponse({'moves': [_serialize_move(m) for m in active]})


def moves_status_stream(request):
    """SSE replacement for the 1s browser poll on the moves page. Move progress is
    local DB state (bytes_moved/bytes_total updated by _run_file_move) — no external
    API involved, so each tick is just a cheap local query."""
    import json
    import time
    from django.db import close_old_connections
    from django.http import StreamingHttpResponse

    def event_stream():
        deadline = time.monotonic() + 55
        last_payload = None
        while time.monotonic() < deadline:
            close_old_connections()
            try:
                active = FileMove.objects.filter(status__in=[FileMove.Status.MOVING, FileMove.Status.PENDING])
                payload = json.dumps({'moves': [_serialize_move(m) for m in active]})
            except Exception as e:
                log.error('moves_status_stream: tick failed — %s', e)
                yield ': error\n\n'
                time.sleep(1)
                continue
            if payload != last_payload:
                yield f'data: {payload}\n\n'
                last_payload = payload
            else:
                yield ': heartbeat\n\n'
            time.sleep(1)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@require_POST
def move_retry(request, pk):
    """Manually (re)run a move right now — bypasses the 1-at-a-time auto-move queue."""
    move = get_object_or_404(FileMove, pk=pk)
    move.status = FileMove.Status.PENDING
    move.error_message = ''
    move.completed_at = None
    move.bytes_moved = 0
    move.save(update_fields=['status', 'error_message', 'completed_at', 'bytes_moved'])
    import threading
    threading.Thread(target=_run_file_move, args=(move.id,), daemon=True).start()
    return JsonResponse({'ok': True})


@require_POST
def move_delete(request, pk):
    move = get_object_or_404(FileMove, pk=pk)
    move.delete()
    return JsonResponse({'ok': True})
