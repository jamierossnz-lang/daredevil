import os
import re
import logging
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

from apps.events.dispatch import emit
from apps.events.models import EventType

log = logging.getLogger('daredevil.tasks')

_JUNK_EXTENSIONS = {
    # metadata / info
    '.nfo', '.txt', '.nzb', '.torrent', '.url', '.xml', '.html', '.htm',
    # images (posters/covers left by torrent clients)
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    # checksums / verification
    '.sfv', '.md5', '.sha1', '.sha256', '.srr',
    # misc rubbish
    '.log', '.db', '.lnk', '.ini', '.dat',
}


@shared_task(name='cleanup_non_video_files')
def cleanup_non_video_files():
    """
    Walk every download_path and completed_path configured in CategoryPath, delete
    known junk files (.nfo, .txt, .jpg, etc.) then remove empty directories left behind.
    Only explicitly listed extensions are removed — video, subtitle, and unknown files
    are always left untouched.
    """
    from apps.qbt.models import CategoryPath

    paths = []
    for cp in CategoryPath.objects.all():
        if cp.download_path:
            paths.append(cp.download_path)
        if cp.completed_path:
            paths.append(cp.completed_path)

    # Deduplicate while preserving order
    seen = set()
    unique_paths = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    if not unique_paths:
        log.info('cleanup_non_video_files: no paths configured, nothing to do')
        return

    deleted_files = 0
    removed_dirs = 0

    for base_path in unique_paths:
        if not os.path.isdir(base_path):
            log.warning('cleanup_non_video_files: path %r does not exist, skipping', base_path)
            continue

        # Delete known junk files (bottom-up so we can catch empty dirs in same pass)
        for root, _dirs, files in os.walk(base_path, topdown=False):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in _JUNK_EXTENSIONS:
                    fpath = os.path.join(root, fname)
                    try:
                        os.remove(fpath)
                        log.info('cleanup_non_video_files: deleted %r', fpath)
                        deleted_files += 1
                    except Exception as e:
                        log.warning('cleanup_non_video_files: could not delete %r — %s', fpath, e)

            # Remove directory if now empty (never remove the base_path itself)
            if root != base_path:
                try:
                    if not os.listdir(root):
                        os.rmdir(root)
                        log.info('cleanup_non_video_files: removed empty dir %r', root)
                        removed_dirs += 1
                except Exception as e:
                    log.warning('cleanup_non_video_files: could not remove dir %r — %s', root, e)

    log.info('cleanup_non_video_files: done — deleted %d file(s), removed %d empty dir(s)',
             deleted_files, removed_dirs)
    return f'deleted {deleted_files} files, {removed_dirs} dirs'


@shared_task(name='auto_search_queue')
def auto_search_queue():
    """
    Pick up any DownloadItems stuck in SEARCHING status that haven't had an active
    search task running in the last 3 minutes, and fire search_and_download for each.
    This means the search runs even when nobody has the queue page open.

    3 minutes is a grace period, not a discovery delay — every item gets
    search_started_at stamped at creation time (see tv_show_queue_download /
    _queue_movie), so this only fires for items the browser hasn't finished
    with yet. It's comfortably longer than the browser's own worst-case
    multi-tier search (a handful of qBittorrent search-plugin queries,
    typically well under a minute), so it won't race a tab that's open.
    """
    from datetime import timedelta
    from apps.downloads.models import DownloadItem

    cutoff = timezone.now() - timedelta(minutes=3)
    from django.db.models import Q
    stuck = DownloadItem.objects.filter(
        status=DownloadItem.Status.SEARCHING,
    ).filter(
        Q(search_started_at__isnull=True) | Q(search_started_at__lt=cutoff)
    )

    dispatched = 0
    for item in stuck:
        search_and_download.delay(item.pk)
        dispatched += 1
        log.info('auto_search_queue: dispatched search_and_download for pk=%d %r', item.pk, item.title)

    log.info('auto_search_queue: dispatched %d item(s)', dispatched)
    return f'dispatched {dispatched}'


@shared_task(name='remove_empty_folders')
def remove_empty_folders():
    """
    Walk every completed_path configured in CategoryPath and remove any empty
    directories (bottom-up, so nested empties collapse in one pass).
    """
    from apps.qbt.models import CategoryPath

    paths = []
    for cp in CategoryPath.objects.all():
        if cp.download_path:
            paths.append(cp.download_path)

    seen = set()
    unique_paths = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    if not unique_paths:
        log.info('remove_empty_folders: no download_path configured, nothing to do')
        return

    removed = 0
    for base_path in unique_paths:
        if not os.path.isdir(base_path):
            log.warning('remove_empty_folders: path %r does not exist, skipping', base_path)
            continue
        for root, _dirs, _files in os.walk(base_path, topdown=False):
            if root == base_path:
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
                    log.info('remove_empty_folders: removed %r', root)
                    removed += 1
            except Exception as e:
                log.warning('remove_empty_folders: could not remove %r — %s', root, e)

    log.info('remove_empty_folders: done — removed %d empty dir(s)', removed)
    return f'removed {removed} empty dirs'


@shared_task(name='sync_all_shows')
def sync_all_shows():
    """
    Re-sync every tracked TV show from TMDB (metadata + episodes) and TVMaze (precise
    air times), then apply awaiting_release/waiting_for_download statuses for monitored shows.
    """
    from .models import TVShow
    from .tmdb import tmdb
    from .tvmaze import tvmaze

    shows = list(TVShow.objects.all())
    synced = 0
    for show in shows:
        try:
            tmdb.sync_show_to_db(show.tmdb_id)
            show.refresh_from_db()
            tvmaze.sync_airdates_for_show(show)
            show.refresh_from_db()
            _apply_episode_statuses(show)
            synced += 1
        except Exception as e:
            log.warning('sync_all_shows: failed for %s: %s', show.name, e)

    return f'synced {synced} of {len(shows)} shows'


@shared_task(name='sync_tvmaze_show')
def sync_tvmaze_show(show_pk):
    """
    Sync TVMaze air times for a single show then apply episode statuses.
    Fired on show add and monitor toggle so statuses are set without waiting for the
    daily sync_all_shows run.
    """
    from .models import TVShow
    from .tvmaze import tvmaze

    try:
        show = TVShow.objects.get(pk=show_pk)
    except TVShow.DoesNotExist:
        return

    tvmaze.sync_airdates_for_show(show)
    show.refresh_from_db()
    _apply_episode_statuses(show)
    log.info('sync_tvmaze_show: done for %s', show.name)
    return f'synced TVMaze airdates for {show.name}'


@shared_task(name='update_episode_statuses')
def update_episode_statuses():
    """
    Transition episodes from awaiting_release → waiting_for_download when their air
    time has passed in NZT. Runs frequently so the window between airing and queuing
    is small.

    Uses air_datetime (precise, UTC) when available, falls back to air_date (date only).

    Rules:
      - With air_datetime: wait 1 hour after the broadcast time (torrent release window).
      - Date-only fallback: wait until the day AFTER the air_date (no exact time known).
    """
    from .models import Episode

    now = timezone.now()
    today = timezone.localdate()
    one_hour_ago = now - timedelta(hours=1)

    # Precise: air_datetime — transition 1 hour after broadcast
    moved_dt = Episode.objects.filter(
        download_status=Episode.DownloadStatus.AWAITING_RELEASE,
        air_datetime__isnull=False,
        air_datetime__lte=one_hour_ago,
    ).update(download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD)

    # Fallback: date-only — transition the day after (air_date strictly before today)
    moved_date = Episode.objects.filter(
        download_status=Episode.DownloadStatus.AWAITING_RELEASE,
        air_datetime__isnull=True,
        air_date__lt=today,
    ).update(download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD)

    total = moved_dt + moved_date
    if total:
        log.info('update_episode_statuses: moved %d episodes to waiting_for_download', total)
    return f'moved {total} to waiting_for_download'


@shared_task(name='queue_waiting_episodes')
def queue_waiting_episodes():
    """
    For all monitored shows, find episodes in waiting_for_download status and queue
    them for download. This is the authoritative auto-download trigger — it only fires
    for episodes that have already aired (status set by update_episode_statuses).
    """
    from .models import TVShow, Episode
    from apps.downloads.models import DownloadItem

    monitored_show_ids = set(
        TVShow.objects.filter(monitor_new_episodes=True).values_list('id', flat=True)
    )
    if not monitored_show_ids:
        return 'no monitored shows'

    waiting = Episode.objects.filter(
        download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD,
        season__show_id__in=monitored_show_ids,
    ).select_related('season__show')

    queued = 0
    newly_queued = []

    for ep in waiting:
        show = ep.season.show
        season_num = ep.season.season_number

        ep_quality = show.preferred_quality if show.preferred_quality != 'auto' else '1080p'
        sq = f'{show.name} S{season_num:02d}E{ep.episode_number:02d} {ep_quality}'
        item, created = DownloadItem.objects.get_or_create(
            media_type=DownloadItem.MediaType.EPISODE,
            episode_id=ep.id,
            defaults={
                'title': show.name,
                'subtitle': str(ep),
                'poster_path': show.poster_path,
                'status': DownloadItem.Status.SEARCHING,
                'release_date': ep.air_date,
                'quality': ep_quality,
                'search_query': sq,
            },
        )
        if created:
            ep.download_status = Episode.DownloadStatus.QUEUED
            ep.save(update_fields=['download_status'])
            search_and_download.delay(item.id)
            log.info('queue_waiting_episodes: queued %s', ep)
            queued += 1
            newly_queued.append((show.name, season_num, ep.episode_number))

    if newly_queued:
        from collections import Counter
        if len(newly_queued) == 1:
            show_name, sn, en = newly_queued[0]
            emit(
                EventType.EPISODE_QUEUED,
                log_payload={'count': 1, 'show': show_name, 'season': sn, 'episode': en},
                title='New Episode Queued', message=f'{show_name} S{sn:02d}E{en:02d}', tags=['tv', 'tada'],
            )
        else:
            by_show = Counter(name for name, _, _ in newly_queued)
            parts = ', '.join(f'{n} ({c})' for n, c in by_show.most_common(5))
            emit(
                EventType.EPISODE_QUEUED,
                log_payload={'count': len(newly_queued)},
                title='New Episodes Queued', message=f'{len(newly_queued)} episodes: {parts}', tags=['tv', 'tada'],
            )

    return f'queued {queued} waiting episodes'


@shared_task(name='check_movie_releases')
def check_movie_releases():
    """
    Move waiting movies into the active download queue when they appear on any
    streaming or rental service (Watch Providers API).  TMDB release dates are
    unreliable, so this is the authoritative availability signal.
    """
    from .models import Movie
    from apps.downloads.models import DownloadItem
    from .tmdb import tmdb, is_available_on_watch_providers

    region = getattr(__import__('django.conf', fromlist=['settings']).settings, 'TMDB_REGION', 'US')
    waiting = list(Movie.objects.filter(download_status=Movie.DownloadStatus.WAITING_RELEASE))
    queued = 0

    for movie in waiting:
        try:
            providers = tmdb.get_movie_watch_providers(movie.tmdb_id)
            available = is_available_on_watch_providers(providers, region)
        except Exception as e:
            log.warning('check_movie_releases: watch-providers call failed for %r — %s', movie.title, e)
            continue

        if not available:
            log.debug('check_movie_releases: %r not yet on any service', movie.title)
            continue

        log.info('check_movie_releases: %r is now available — queuing download', movie.title)
        item = DownloadItem.objects.filter(
            media_type=DownloadItem.MediaType.MOVIE,
            movie_id=movie.id,
        ).first()
        if item and item.status == DownloadItem.Status.WAITING_RELEASE:
            item.status = DownloadItem.Status.SEARCHING
            item.save(update_fields=['status'])
            movie.download_status = Movie.DownloadStatus.QUEUED
            movie.save(update_fields=['download_status'])
            search_and_download.delay(item.id)
            queued += 1
            year = movie.release_date.year if movie.release_date else ''
            emit(
                EventType.MOVIE_AVAILABLE,
                log_payload={'movie_pk': movie.pk, 'title': movie.title, 'year': year},
                title='Movie Now Available',
                message=f'{movie.title} ({year}) is now streaming — downloading',
                tags=['clapper', 'tada'],
            )

    return f'checked {len(waiting)} waiting, queued {queued}'


@shared_task(name='refresh_movie_release_dates')
def refresh_movie_release_dates():
    """
    Daily task: re-fetch TMDB type-4 (Digital) release dates for all waiting movies.
    If a confirmed date is now available, replace the +45-day estimate and update
    the DownloadItem so the correct date shows in the Awaiting Release tab.
    """
    from .models import Movie
    from apps.downloads.models import DownloadItem
    from .tmdb import tmdb, _extract_digital_release
    from datetime import timedelta

    waiting = Movie.objects.filter(download_status=Movie.DownloadStatus.WAITING_RELEASE)
    updated = 0

    for movie in waiting:
        try:
            data = tmdb.get_movie_release_dates(movie.tmdb_id)
            confirmed = _extract_digital_release(data.get('results', []))
        except Exception as e:
            log.warning('refresh_movie_release_dates: TMDB call failed for %r — %s', movie.title, e)
            continue

        # Use confirmed date if available, otherwise recalculate estimate
        new_date = confirmed or (
            movie.release_date + timedelta(days=45) if movie.release_date else None
        )

        if new_date and new_date != movie.digital_release_date:
            log.info('refresh_movie_release_dates: updating %r digital date %s → %s',
                     movie.title, movie.digital_release_date, new_date)
            movie.digital_release_date = new_date
            movie.save(update_fields=['digital_release_date'])
            # Keep DownloadItem.release_date in sync so the UI shows the right date
            DownloadItem.objects.filter(
                media_type=DownloadItem.MediaType.MOVIE,
                movie_id=movie.id,
                status=DownloadItem.Status.WAITING_RELEASE,
            ).update(release_date=new_date)
            updated += 1

    return f'updated {updated} release dates'


@shared_task(name='search_and_download')
def search_and_download(download_item_id):
    """
    Search qBittorrent for the best 1080p torrent, add it, and record the hash
    so sync_download_progress can track it.
    """
    from apps.downloads.models import DownloadItem
    from apps.qbt.client import search_torrents, add_torrent_and_resolve_hash, is_connected

    try:
        item = DownloadItem.objects.get(pk=download_item_id)
    except DownloadItem.DoesNotExist:
        return

    if not is_connected():
        log.warning('search_and_download pk=%s: qBittorrent not reachable — leaving as SEARCHING for browser to handle', download_item_id)
        # Don't mark FAILED — keep SEARCHING so the browser auto-search on the
        # queue page can pick this up when the user visits.
        return

    queries, quality = _build_queries(item)
    log.info('search_and_download pk=%s: queries=%r quality=%s', download_item_id, queries, quality)

    item.status = DownloadItem.Status.SEARCHING
    item.search_query = queries[0]
    item.result_count = -1
    item.search_started_at = timezone.now()
    item.save(update_fields=['status', 'search_query', 'result_count', 'search_started_at'])

    episode_code = None
    if item.media_type == DownloadItem.MediaType.EPISODE:
        m = re.search(r'S\d+E\d+', queries[0], re.IGNORECASE)
        if m:
            episode_code = m.group(0).upper()

    season_code = None
    episode_count = None
    if item.media_type == DownloadItem.MediaType.SEASON:
        m = re.search(r'S\d+', queries[0], re.IGNORECASE)
        if m:
            season_code = m.group(0).upper()
        try:
            from apps.media_tracker.models import Season
            episode_count = Season.objects.get(pk=item.season_id).episodes.count()
        except Exception:
            pass

    best = None
    tried = []
    for query in queries:
        tried.append(query)
        item.search_query = query
        item.save(update_fields=['search_query'])
        try:
            results = search_torrents(query)
            log.info('search_and_download pk=%s: query=%r → %d results', download_item_id, query, len(results))
        except Exception as e:
            log.error('search_and_download pk=%s: search_torrents raised %s: %s', download_item_id, type(e).__name__, e)
            continue
        if not results:
            continue
        best = _pick_best(results, quality, media_type=item.media_type, episode_code=episode_code,
                           season_code=season_code, show_name=item.title, episode_count=episode_count)
        if best:
            item.result_count = len(results)
            item.save(update_fields=['result_count'])
            break

    if not best and item.media_type == DownloadItem.MediaType.SEASON:
        log.info('search_and_download pk=%s: no season pack found — falling back to per-episode downloads', download_item_id)
        _fallback_season_to_episodes(item)
        return

    if not best:
        log.warning('search_and_download pk=%s: no suitable torrent found after %d queries', download_item_id, len(tried))
        # If the episode/movie air date is today (NZ), it may not be out yet — keep
        # searching rather than permanently failing.
        air_date = item.release_date
        if air_date and air_date >= timezone.localdate():
            item.status = DownloadItem.Status.SEARCHING
            item.error_message = f'Not yet available — retrying (tried: {", ".join(repr(q) for q in tried)})'
            item.result_count = 0
            item.search_started_at = None  # reset so auto_search_queue will retry in 10 min
            item.save(update_fields=['status', 'error_message', 'result_count', 'search_started_at'])
            log.info('search_and_download pk=%s: air date is today (%s) — will retry', download_item_id, air_date)
            return
        item.status = DownloadItem.Status.FAILED
        item.error_message = f'No results — tried: {", ".join(repr(q) for q in tried)}'
        item.result_count = 0
        item.save(update_fields=['status', 'error_message', 'result_count'])
        label = item.title + (f' — {item.subtitle}' if item.subtitle else '')
        emit(
            EventType.SEARCH_FAILED,
            log_payload={'item_pk': item.pk, 'title': item.title},
            title='Download Failed', message=f'{label} — no torrent found',
            priority='high', tags=['x'],
        )
        return

    magnet = best.get('fileUrl', '')
    try:
        from apps.qbt.models import CategoryConfig, CategoryPath
        cfg = CategoryConfig.get()
        is_tv = item.media_type in (DownloadItem.MediaType.EPISODE, DownloadItem.MediaType.SEASON)
        category = cfg.tv_category if is_tv else cfg.movie_category
        cat_path = CategoryPath.objects.filter(category_name=category).first()
        save_path = cat_path.qbt_save_path if cat_path else None
        torrent_hash = add_torrent_and_resolve_hash(magnet, save_path=save_path or None, category=category or None)
        if not torrent_hash:
            log.warning('search_and_download pk=%s: could not resolve torrent hash after adding — will rely on name-match recovery', download_item_id)

        item.status = DownloadItem.Status.DOWNLOADING
        item.torrent_name = best.get('fileName', '')
        item.magnet_link = magnet
        item.torrent_hash = torrent_hash
        item.started_at = timezone.now()
        item.save(update_fields=['status', 'torrent_name', 'magnet_link', 'torrent_hash', 'started_at'])
    except Exception as e:
        item.status = DownloadItem.Status.FAILED
        item.error_message = f'Failed to add to qBittorrent: {e}'
        item.save(update_fields=['status', 'error_message'])


@shared_task(name='sync_download_progress')
def sync_download_progress():
    """Poll qBittorrent for progress on all active downloads and mark completed ones."""
    from apps.downloads.models import DownloadItem
    from apps.qbt.client import get_torrent, is_connected

    if not is_connected():
        return 'qBT unreachable'

    active = list(DownloadItem.objects.filter(
        status=DownloadItem.Status.DOWNLOADING,
        torrent_hash__gt='',
    ))
    completed = 0

    for item in active:
        t = get_torrent(item.torrent_hash)
        if not t:
            continue
        item.progress = t.progress * 100
        item.download_speed = t.dlspeed
        item.eta_seconds = t.eta
        item.size_bytes = t.size
        fields = ['progress', 'download_speed', 'eta_seconds', 'size_bytes']

        if t.progress >= 1.0:
            item.status = DownloadItem.Status.COMPLETED
            item.completed_at = timezone.now()
            fields += ['status', 'completed_at']
            _mark_downloaded(item)
            completed += 1

        item.save(update_fields=fields)

    return f'{len(active)} active, {completed} completed'


# ── Helpers ──────────────────────────────────────────────────────────────────

_QUALITY_KEYWORDS = {
    '1080p': ['1080p', '1080'],
    '2160p': ['2160p', '4k', 'uhd', '2160'],
}


def _build_queries(item):
    """
    Return (queries, quality) where queries is an ordered list from most to least specific.

    For TV episodes the 4-tier strategy is:
      1. {Show} SxxExx {quality}
      2. {Show} SxxExx {episode name} {quality}  (if name available)
      3. {Show} SxxExx
      4. {Show} SxxExx {episode name}             (if name available)

    For movies: [{title} {year} {quality}, {title} {year}]
    """
    from apps.downloads.models import DownloadItem
    from apps.media_tracker.models import Episode, Season

    if item.media_type == DownloadItem.MediaType.SEASON:
        # Single-tier: season-pack search only. If this comes back empty, the
        # caller falls back to the per-episode strategy below instead of retrying.
        try:
            season = Season.objects.select_related('show').get(pk=item.season_id)
            year = season.show.first_air_date.year if season.show.first_air_date else ''
            base = ' '.join(filter(None, [item.title, str(year) if year else '', f'S{season.season_number:02d}']))
        except Season.DoesNotExist:
            base = item.search_query or item.title
        quality = item.quality or '1080p'
        return [base], quality

    if item.media_type == DownloadItem.MediaType.EPISODE:
        try:
            ep = Episode.objects.select_related('season__show').get(pk=item.episode_id)
            base = f'{item.title} S{ep.season.season_number:02d}E{ep.episode_number:02d}'
            ep_name = (ep.name or '').strip()
        except Episode.DoesNotExist:
            subtitle = item.subtitle or ''
            base = subtitle.split(' - ')[0].strip() if ' - ' in subtitle else subtitle or item.title
            ep_name = ''

        try:
            preferred = ep.season.show.preferred_quality
        except Exception:
            preferred = 'auto'
        quality = preferred if preferred != 'auto' else '1080p'
        queries = [f'{base} {quality}']
        if ep_name:
            queries.append(f'{base} {ep_name} {quality}')
        queries.append(base)
        if ep_name:
            queries.append(f'{base} {ep_name}')
        return queries, quality

    else:
        year = str(item.release_date.year) if item.release_date else ''
        quality = item.quality or '1080p'
        base = ' '.join(filter(None, [item.title, year]))
        return [f'{base} {quality}', base], quality


_MIN_SEEDS = 3  # don't pick a torrent with fewer seeds than this


def _size_brackets(quality, media_type):
    """Return (min_bytes, max_bytes) for the given quality+media_type from DB.
    Falls back to sensible hardcoded defaults if no profile exists."""
    try:
        from apps.media_tracker.models import QualityProfile
        p = QualityProfile.objects.get(quality=quality, media_type=media_type)
        min_b = (p.min_size_mb or 0) * 1024 * 1024
        max_b = (p.max_size_mb or 0) * 1024 * 1024
        return min_b, max_b
    except Exception:
        # Hardcoded fallbacks
        if media_type == 'tv':
            return 500 * 1024 * 1024, 3 * 1024 ** 3
        return 1024 ** 3, 20 * 1024 ** 3


def _norm_title(s):
    """Lowercase, replace punctuation/separators with spaces, collapse whitespace."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', (s or '').lower())).strip()


def _pick_best(results, quality=None, media_type=None, episode_code=None, season_code=None,
                show_name=None, episode_count=None):
    """
    Pick the best torrent from search results.

    For TV episodes: filter to results where:
      1. The filename starts with the (normalised) show name
      2. The filename contains the expected SxxExx code
    Both are hard gates — returning the wrong show/episode is worse than failing.
    For TV seasons: filter to results containing the season code (SXX) but NOT a
    single-episode code (SXXEYY) — a season pack, not a lone episode.
    For movies: pick the best-seeded match regardless of size.
    """
    from apps.downloads.models import DownloadItem

    if not results:
        return None

    # ── Show name prefix filter (TV only) ────────────────────────────────────
    if show_name and media_type in (DownloadItem.MediaType.EPISODE, DownloadItem.MediaType.SEASON):
        show_norm = _norm_title(show_name)
        if show_norm:
            name_filtered = [
                r for r in results
                if _norm_title(r.get('fileName') or '').startswith(show_norm)
            ]
            if name_filtered:
                log.info('_pick_best: show-name filter %r → %d of %d results kept',
                         show_name, len(name_filtered), len(results))
                results = name_filtered
            else:
                log.warning('_pick_best: 0 results start with show name %r — skipping this query tier',
                            show_name)
                return None

    # ── Episode code filter (hard gate — wrong episode is worse than no download) ──
    if episode_code:
        m = re.match(r'S(\d+)E(\d+)', episode_code, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            ep     = int(m.group(2))
            # Matches S01E03, S1E3, S001E003 but NOT S04E03 or S01E30
            pat = re.compile(rf'S0*{season}E0*{ep}(?!\d)', re.IGNORECASE)
            ep_filtered = [r for r in results if pat.search(r.get('fileName') or '')]
            if ep_filtered:
                log.info('_pick_best: episode filter %r → %d of %d results kept',
                         episode_code, len(ep_filtered), len(results))
                results = ep_filtered
            else:
                log.warning('_pick_best: 0 results contain %r — refusing to pick wrong episode',
                            episode_code)
                return None

    # ── Season-pack filter (hard gate — a lone episode is not a season download) ──
    if season_code:
        m = re.match(r'S(\d+)', season_code, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            season_pat = re.compile(rf'S0*{season}(?!\d)', re.IGNORECASE)
            single_ep_pat = re.compile(rf'S0*{season}E\d+', re.IGNORECASE)
            pack_filtered = [
                r for r in results
                if season_pat.search(r.get('fileName') or '') and not single_ep_pat.search(r.get('fileName') or '')
            ]
            if pack_filtered:
                log.info('_pick_best: season-pack filter %r → %d of %d results kept',
                         season_code, len(pack_filtered), len(results))
                results = pack_filtered
            else:
                log.warning('_pick_best: 0 season-pack results for %r — refusing (caller falls back to per-episode)',
                            season_code)
                return None

    # ── Quality filter ────────────────────────────────────────────────────────
    if quality:
        keywords   = _QUALITY_KEYWORDS.get(quality, [quality.lower()])
        candidates = [
            r for r in results
            if any(kw in (r.get('fileName') or '').lower() for kw in keywords)
        ]
        if not candidates:
            candidates = list(results)
    else:
        candidates = list(results)

    # ── Seed filter (used for fallback only for TV) ───────────────────────────
    seeded = [r for r in candidates if (r.get('nbSeeders') or 0) >= _MIN_SEEDS]

    # ── TV size tiers ─────────────────────────────────────────────────────────
    if media_type == DownloadItem.MediaType.EPISODE:
        def _size(r):
            return r.get('fileSize') or 0

        size_min, size_max = _size_brackets(quality or '1080p', 'tv')
        # Check bracket against ALL quality-matched candidates, ignoring seed count.
        # A result inside the bracket is preferred over a higher-seeded result outside.
        in_range = [r for r in candidates if size_min <= _size(r) <= size_max]
        if in_range:
            seeded_in_range = [r for r in in_range if (r.get('nbSeeders') or 0) >= _MIN_SEEDS]
            best = max(seeded_in_range or in_range, key=lambda r: r.get('nbSeeders', 0))
            log.info('_pick_best: %d in bracket (%d seeded) — picked best-seeded in range',
                     len(in_range), len(seeded_in_range))
        else:
            # Nothing in bracket — fall back to most-seeded across all quality matches
            pool = seeded if seeded else candidates
            best = max(pool, key=lambda r: r.get('nbSeeders', 0))
            log.info('_pick_best: bracket empty — fell back to most-seeded (%d candidates)', len(candidates))
    elif media_type == DownloadItem.MediaType.SEASON:
        def _size(r):
            return r.get('fileSize') or 0

        ep_min, ep_max = _size_brackets(quality or '1080p', 'tv')
        n = episode_count or 1
        # Wide slack — season packs compress better per-episode than standalone files,
        # and episode_count may be off for in-progress seasons.
        size_min, size_max = ep_min * n * 0.4, ep_max * n * 1.3
        in_range = [r for r in candidates if size_min <= _size(r) <= size_max]
        if in_range:
            seeded_in_range = [r for r in in_range if (r.get('nbSeeders') or 0) >= _MIN_SEEDS]
            best = max(seeded_in_range or in_range, key=lambda r: r.get('nbSeeders', 0))
            log.info('_pick_best: %d season packs in bracket (%d seeded) — picked best-seeded in range',
                     len(in_range), len(seeded_in_range))
        else:
            pool = seeded if seeded else candidates
            best = max(pool, key=lambda r: r.get('nbSeeders', 0))
            log.info('_pick_best: season bracket empty — fell back to most-seeded (%d candidates)', len(candidates))
    else:
        pool = seeded if seeded else candidates
        best = max(pool, key=lambda r: r.get('nbSeeders', 0))

    sz_gb = (best.get('fileSize') or 0) / 1024 ** 3
    log.info('_pick_best: chose %r (%d seeds, %.2f GB) from %d candidates',
             (best.get('fileName') or '')[:60], best.get('nbSeeders', 0), sz_gb, len(candidates))
    return best


def _mark_downloaded(item):
    from apps.media_tracker.models import Episode, Movie
    from apps.downloads.models import DownloadItem

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


def queue_individual_episodes(show, season):
    """
    Create one per-episode DownloadItem for every not-yet-queued episode in `season`.
    This is the original per-episode search strategy — used both as the normal path
    for partial/episode-level queuing and as the fallback when a season-pack search
    (see search_and_download / MediaType.SEASON) finds nothing.
    """
    from apps.downloads.models import DownloadItem
    from apps.media_tracker.models import Episode

    ep_quality = show.preferred_quality if show.preferred_quality != 'auto' else '1080p'
    created = 0
    for ep in season.episodes.all():
        if ep.download_status in (Episode.DownloadStatus.QUEUED, Episode.DownloadStatus.DOWNLOADING, Episode.DownloadStatus.DOWNLOADED):
            continue
        sq = f'{show.name} S{season.season_number:02d}E{ep.episode_number:02d} {ep_quality}'
        item, is_new = DownloadItem.objects.get_or_create(
            media_type=DownloadItem.MediaType.EPISODE,
            episode_id=ep.id,
            defaults={
                'title': show.name,
                'subtitle': str(ep),
                'poster_path': show.poster_path,
                'status': DownloadItem.Status.SEARCHING,
                'release_date': ep.air_date,
                'quality': ep_quality,
                'search_query': sq,
                # Stamped at creation — see tv_show_queue_download for why:
                # a null timestamp makes auto_search_queue's backstop treat
                # this as immediately eligible, racing whichever path
                # (client tab or this same server-side search) is about to
                # search it anyway.
                'search_started_at': timezone.now(),
            },
        )
        if is_new:
            ep.download_status = Episode.DownloadStatus.QUEUED
            ep.save(update_fields=['download_status'])
            created += 1
    return created


def _fallback_season_to_episodes(item):
    """
    Called when a season-pack search (MediaType.SEASON) finds nothing usable.
    Replaces the season DownloadItem with individual per-episode ones — the
    pre-season-pack behaviour — so the download still completes.
    """
    from apps.media_tracker.models import Season

    try:
        season = Season.objects.select_related('show').get(pk=item.season_id)
    except Season.DoesNotExist:
        item.status = item.Status.FAILED
        item.error_message = 'Season no longer exists'
        item.save(update_fields=['status', 'error_message'])
        return 0

    created = queue_individual_episodes(season.show, season)
    if created:
        item.delete()
    else:
        # Nothing to fall back to. Either every episode in this season is
        # already queued/downloading/downloaded (nothing left to do — fine),
        # or the season has no episode data at all, e.g. sync never
        # populated it (a real problem worth surfacing). Either way, keep a
        # visible failure record instead of silently deleting the item with
        # nothing to show for it.
        if not season.episodes.exists():
            item.error_message = 'No season pack found, and this season has no episode data to fall back to — try re-syncing the show'
        else:
            item.error_message = 'No season pack found — remaining episodes are already queued or downloaded'
        item.status = item.Status.FAILED
        item.save(update_fields=['status', 'error_message'])
    log.info('_fallback_season_to_episodes: pk=%s → queued %d individual episode(s) for %s S%02d',
             item.pk, created, season.show.name, season.season_number)
    return created


def _apply_episode_statuses(show):
    """
    Set awaiting_release or waiting_for_download on episodes whose download_status is
    still NONE, for monitored shows only. Respects monitor_from as the earliest cutoff
    date so historical episodes are never auto-queued.

    Uses air_datetime (UTC, from TVMaze) when available; falls back to air_date (TMDB).
    """
    from .models import Episode

    if not show.monitor_new_episodes:
        return

    now = timezone.now()
    today = timezone.localdate()
    one_hour_ago = now - timedelta(hours=1)
    cutoff = show.monitor_from or show.added_at.date()

    base_qs = Episode.objects.filter(
        season__show=show,
        download_status=Episode.DownloadStatus.NONE,
        air_date__isnull=False,
        air_date__gte=cutoff,
    )

    # Precise: TVMaze air_datetime — awaiting until 1 hour after broadcast
    base_qs.filter(air_datetime__isnull=False, air_datetime__gt=one_hour_ago).update(
        download_status=Episode.DownloadStatus.AWAITING_RELEASE
    )
    base_qs.filter(air_datetime__isnull=False, air_datetime__lte=one_hour_ago).update(
        download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD
    )

    # Fallback: date-only — awaiting until the day after (no exact time known)
    base_qs.filter(air_datetime__isnull=True, air_date__gte=today).update(
        download_status=Episode.DownloadStatus.AWAITING_RELEASE
    )
    base_qs.filter(air_datetime__isnull=True, air_date__lt=today).update(
        download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD
    )


@shared_task(name='check_storage')
def check_storage():
    """Alert via ntfy when any configured drive reaches 75 % (caution) or 90 % (critical)."""
    from django.core.cache import cache
    from apps.plex.utils import get_disk_usage

    drives = get_disk_usage()
    for drive in drives:
        label_str = ' + '.join(l['label'] for l in drive['labels'])
        cache_key  = f'ntfy_storage_{drive["path"]}'

        if drive['warning']:
            if not cache.get(cache_key):
                emit(
                    EventType.STORAGE_WARNING,
                    log_payload={'path': drive['path'], 'pct': drive['pct'], 'level': 'critical'},
                    title='Storage Critical',
                    message=f'{label_str}: {drive["pct"]}% full — {drive["free_display"]} left of {drive["total_display"]}',
                    priority='urgent',
                    tags=['rotating_light', 'cd'],
                )
                cache.set(cache_key, 'warning', 4 * 60 * 60)
        elif drive['caution']:
            if not cache.get(cache_key):
                emit(
                    EventType.STORAGE_WARNING,
                    log_payload={'path': drive['path'], 'pct': drive['pct'], 'level': 'caution'},
                    title='Storage Getting Full',
                    message=f'{label_str}: {drive["pct"]}% used — {drive["free_display"]} remaining',
                    priority='high',
                    tags=['warning', 'cd'],
                )
                cache.set(cache_key, 'caution', 4 * 60 * 60)
        else:
            cache.delete(cache_key)  # clear so next breach re-alerts

    return f'checked {len(drives)} drive(s)'
