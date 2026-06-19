import os
import re
import logging
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

log = logging.getLogger('daredevil.tasks')

_VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.m4v', '.wmv',
    '.ts', '.m2ts', '.mpg', '.mpeg', '.webm', '.flv', '.3gp',
}


@shared_task(name='cleanup_non_video_files')
def cleanup_non_video_files():
    """
    Walk every download_path and completed_path configured in CategoryPath, delete
    any file whose extension is not a recognised video format, then remove empty
    directories left behind.
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

        # Delete non-video files (bottom-up so we can catch empty dirs in same pass)
        for root, _dirs, files in os.walk(base_path, topdown=False):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _VIDEO_EXTENSIONS:
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
    search task running in the last 10 minutes, and fire search_and_download for each.
    This means the search runs even when nobody has the queue page open.
    """
    from datetime import timedelta
    from apps.downloads.models import DownloadItem

    cutoff = timezone.now() - timedelta(minutes=10)
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

        sq = f'{show.name} S{season_num:02d}E{ep.episode_number:02d} 1080p'
        item, created = DownloadItem.objects.get_or_create(
            media_type=DownloadItem.MediaType.EPISODE,
            episode_id=ep.id,
            defaults={
                'title': show.name,
                'subtitle': str(ep),
                'poster_path': show.poster_path,
                'status': DownloadItem.Status.SEARCHING,
                'release_date': ep.air_date,
                'quality': '1080p',
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
        from apps.notifications.notify import send as ntfy
        from collections import Counter
        if len(newly_queued) == 1:
            show_name, sn, en = newly_queued[0]
            ntfy('New Episode Queued', f'{show_name} S{sn:02d}E{en:02d}', tags=['tv', 'tada'], category='episodes_queued')
        else:
            by_show = Counter(name for name, _, _ in newly_queued)
            parts = ', '.join(f'{n} ({c})' for n, c in by_show.most_common(5))
            ntfy('New Episodes Queued', f'{len(newly_queued)} episodes: {parts}', tags=['tv', 'tada'], category='episodes_queued')

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
            from apps.notifications.notify import send as ntfy
            year = movie.release_date.year if movie.release_date else ''
            ntfy(
                'Movie Now Available',
                f'{movie.title} ({year}) is now streaming — downloading',
                tags=['clapper', 'tada'],
                category='movie_available',
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
    from apps.qbt.client import search_torrents, add_magnet, is_connected

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
        best = _pick_best(results, quality, media_type=item.media_type, episode_code=episode_code, show_name=item.title)
        if best:
            item.result_count = len(results)
            item.save(update_fields=['result_count'])
            break

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
        from apps.notifications.notify import send as ntfy
        label = item.title + (f' — {item.subtitle}' if item.subtitle else '')
        ntfy('Download Failed', f'{label} — no torrent found', priority='high', tags=['x'], category='download_failed')
        return

    magnet = best.get('fileUrl', '')
    try:
        from apps.qbt.models import CategoryConfig, CategoryPath
        cfg = CategoryConfig.get()
        category = cfg.tv_category if item.media_type == DownloadItem.MediaType.EPISODE else cfg.movie_category
        cat_path = CategoryPath.objects.filter(category_name=category).first()
        save_path = cat_path.download_path if cat_path else None
        add_magnet(magnet, save_path=save_path or None, category=category or None)

        item.status = DownloadItem.Status.DOWNLOADING
        item.torrent_name = best.get('fileName', '')
        item.magnet_link = magnet
        item.torrent_hash = _hash_from_magnet(magnet)
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
    from apps.media_tracker.models import Episode

    if item.media_type == DownloadItem.MediaType.EPISODE:
        try:
            ep = Episode.objects.select_related('season').get(pk=item.episode_id)
            base = f'{item.title} S{ep.season.season_number:02d}E{ep.episode_number:02d}'
            ep_name = (ep.name or '').strip()
        except Episode.DoesNotExist:
            subtitle = item.subtitle or ''
            base = subtitle.split(' - ')[0].strip() if ' - ' in subtitle else subtitle or item.title
            ep_name = ''

        quality = '1080p'
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

_TV_SIZE_MIN = 1 * 1024 ** 3      # 1 GB — lower bound of preferred range
_TV_SIZE_MAX = 2 * 1024 ** 3      # 2 GB — upper bound of preferred range


def _norm_title(s):
    """Lowercase, replace punctuation/separators with spaces, collapse whitespace."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', (s or '').lower())).strip()


def _pick_best(results, quality=None, media_type=None, episode_code=None, show_name=None):
    """
    Pick the best torrent from search results.

    For TV episodes: filter to results where:
      1. The filename starts with the (normalised) show name
      2. The filename contains the expected SxxExx code
    Both are hard gates — returning the wrong show/episode is worse than failing.
    For movies: pick the best-seeded match regardless of size.
    """
    from apps.downloads.models import DownloadItem

    if not results:
        return None

    # ── Show name prefix filter (TV only) ────────────────────────────────────
    if show_name and media_type == DownloadItem.MediaType.EPISODE:
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

    # ── Seed filter ───────────────────────────────────────────────────────────
    seeded = [r for r in candidates if (r.get('nbSeeders') or 0) >= _MIN_SEEDS]
    pool   = seeded if seeded else candidates

    # ── TV size tiers ─────────────────────────────────────────────────────────
    if media_type == DownloadItem.MediaType.EPISODE:
        def _size(r):
            return r.get('fileSize') or 0

        in_range = [r for r in pool if _TV_SIZE_MIN <= _size(r) <= _TV_SIZE_MAX]
        if in_range:
            best = max(in_range, key=lambda r: r.get('nbSeeders', 0))
        else:
            larger = [r for r in pool if _size(r) > _TV_SIZE_MAX]
            if larger:
                # Smallest file above the range (least unnecessary quality)
                best = min(larger, key=_size)
            else:
                # Nothing in range or above — take the best-seeded fallback
                best = max(pool, key=lambda r: r.get('nbSeeders', 0))
    else:
        best = max(pool, key=lambda r: r.get('nbSeeders', 0))

    sz_gb = (best.get('fileSize') or 0) / 1024 ** 3
    log.info('_pick_best: chose %r (%d seeds, %.2f GB) from %d candidates',
             (best.get('fileName') or '')[:60], best.get('nbSeeders', 0), sz_gb, len(candidates))
    return best


def _hash_from_magnet(magnet: str) -> str:
    """Extract the info-hash from a magnet URI (supports hex and base32)."""
    m = re.search(r'urn:btih:([a-fA-F0-9]{40}|[A-Z2-7]{32})', magnet, re.IGNORECASE)
    return m.group(1).lower() if m else ''


def _mark_downloaded(item):
    from apps.media_tracker.models import Episode, Movie
    from apps.downloads.models import DownloadItem

    if item.media_type == DownloadItem.MediaType.EPISODE and item.episode_id:
        Episode.objects.filter(pk=item.episode_id).update(
            download_status=Episode.DownloadStatus.DOWNLOADED
        )
    elif item.media_type == DownloadItem.MediaType.MOVIE and item.movie_id:
        Movie.objects.filter(pk=item.movie_id).update(
            download_status=Movie.DownloadStatus.DOWNLOADED
        )


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
    from apps.notifications.notify import send as ntfy

    drives = get_disk_usage()
    for drive in drives:
        label_str = ' + '.join(l['label'] for l in drive['labels'])
        cache_key  = f'ntfy_storage_{drive["path"]}'

        if drive['warning']:
            if not cache.get(cache_key):
                ntfy(
                    'Storage Critical',
                    f'{label_str}: {drive["pct"]}% full — {drive["free_display"]} left of {drive["total_display"]}',
                    priority='urgent',
                    tags=['rotating_light', 'cd'],
                    category='storage_warning',
                )
                cache.set(cache_key, 'warning', 4 * 60 * 60)
        elif drive['caution']:
            if not cache.get(cache_key):
                ntfy(
                    'Storage Getting Full',
                    f'{label_str}: {drive["pct"]}% used — {drive["free_display"]} remaining',
                    priority='high',
                    tags=['warning', 'cd'],
                    category='storage_warning',
                )
                cache.set(cache_key, 'caution', 4 * 60 * 60)
        else:
            cache.delete(cache_key)  # clear so next breach re-alerts

    return f'checked {len(drives)} drive(s)'
