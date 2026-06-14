import re
import logging
from celery import shared_task
from django.utils import timezone

log = logging.getLogger('daredevil.tasks')


@shared_task(name='sync_all_shows')
def sync_all_shows():
    """Re-sync every tracked TV show from TMDB to pick up new seasons and episodes."""
    from .models import TVShow
    from .tmdb import tmdb

    for show in TVShow.objects.all():
        try:
            tmdb.sync_show_to_db(show.tmdb_id)
        except Exception:
            pass


@shared_task(name='queue_new_episodes')
def queue_new_episodes():
    """Find aired episodes on monitored shows not yet queued and kick off downloads."""
    from .models import TVShow, Episode
    from apps.downloads.models import DownloadItem

    monitored = TVShow.objects.filter(monitor_new_episodes=True).prefetch_related(
        'seasons__episodes'
    )
    today = timezone.now().date()

    for show in monitored:
        for season in show.seasons.all():
            for ep in season.episodes.filter(download_status=Episode.DownloadStatus.NONE):
                if ep.air_date and ep.air_date <= today:
                    sq = f'{show.name} S{season.season_number:02d}E{ep.episode_number:02d} 1080p'
                    item, created = DownloadItem.objects.get_or_create(
                        media_type=DownloadItem.MediaType.EPISODE,
                        episode_id=ep.id,
                        defaults={
                            'title': show.name,
                            'subtitle': str(ep),
                            'poster_path': show.poster_path,
                            'status': DownloadItem.Status.SEARCHING,
                            'release_date': ep.air_date,
                            'search_query': sq,
                        },
                    )
                    if created:
                        ep.download_status = Episode.DownloadStatus.QUEUED
                        ep.save(update_fields=['download_status'])
                        search_and_download.delay(item.id)


@shared_task(name='check_movie_releases')
def check_movie_releases():
    """Move movies that have become digitally available into the active download queue."""
    from .models import Movie
    from apps.downloads.models import DownloadItem

    waiting = Movie.objects.filter(download_status=Movie.DownloadStatus.WAITING_RELEASE)

    for movie in waiting:
        if movie.is_digitally_available:
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

    query, fallback_query, quality = _build_query(item)
    log.info('search_and_download pk=%s: query=%r fallback=%r quality=%s', download_item_id, query, fallback_query, quality)

    item.status = DownloadItem.Status.SEARCHING
    item.search_query = query
    item.result_count = -1
    item.save(update_fields=['status', 'search_query', 'result_count'])

    try:
        results = search_torrents(query)
        log.info('search_and_download pk=%s: primary search returned %d results', download_item_id, len(results))
    except Exception as e:
        log.error('search_and_download pk=%s: search_torrents raised %s: %s', download_item_id, type(e).__name__, e)
        item.status = DownloadItem.Status.FAILED
        item.error_message = f'Search failed: {e}'
        item.result_count = 0
        item.save(update_fields=['status', 'error_message', 'result_count'])
        return

    # If primary query (with quality) returned nothing, retry with bare query
    if not results and fallback_query != query:
        log.info('search_and_download pk=%s: no results, retrying with fallback %r', download_item_id, fallback_query)
        try:
            results = search_torrents(fallback_query)
            log.info('search_and_download pk=%s: fallback returned %d results', download_item_id, len(results))
            if results:
                item.search_query = f'{fallback_query} (any quality)'
                item.save(update_fields=['search_query'])
        except Exception as e:
            log.error('search_and_download pk=%s: fallback search failed: %s', download_item_id, e)

    item.result_count = len(results) if results else 0
    item.save(update_fields=['result_count'])

    best = _pick_best(results, quality, media_type=item.media_type)
    if not best:
        log.warning('search_and_download pk=%s: no suitable torrent found', download_item_id)
        item.status = DownloadItem.Status.FAILED
        item.error_message = f'No results found — tried "{query}" and "{fallback_query}"'
        item.save(update_fields=['status', 'error_message'])
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
        return

    active = DownloadItem.objects.filter(
        status=DownloadItem.Status.DOWNLOADING,
        torrent_hash__gt='',
    )

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

        item.save(update_fields=fields)


# ── Helpers ──────────────────────────────────────────────────────────────────

_QUALITY_KEYWORDS = {
    '1080p': ['1080p', '1080'],
    '2160p': ['2160p', '4k', 'uhd', '2160'],
}


def _build_query(item):
    """
    Return (primary_query, fallback_query, quality_string).
    Primary includes quality suffix; fallback is the same without it.
    """
    from apps.downloads.models import DownloadItem
    from apps.media_tracker.models import Episode

    if item.media_type == DownloadItem.MediaType.EPISODE:
        try:
            ep = Episode.objects.select_related('season').get(pk=item.episode_id)
            base = f'{item.title} S{ep.season.season_number:02d}E{ep.episode_number:02d}'
        except Episode.DoesNotExist:
            # Strip trailing " - Episode Name" from subtitle if present
            base = item.subtitle.split(' - ')[0] if ' - ' in item.subtitle else item.subtitle
        return f'{base} 1080p', base, '1080p'

    else:
        year = str(item.release_date.year) if item.release_date else ''
        quality = item.quality or '1080p'
        base = ' '.join(filter(None, [item.title, year]))
        return f'{base} {quality}', base, quality


_MIN_SEEDS = 3  # don't pick a torrent with fewer seeds than this

_TV_SIZE_MIN = 1 * 1024 ** 3      # 1 GB — lower bound of preferred range
_TV_SIZE_MAX = 2 * 1024 ** 3      # 2 GB — upper bound of preferred range


def _pick_best(results, quality=None, media_type=None):
    """
    Pick the best torrent from search results.

    For TV episodes: prefer 1–2 GB files (typical 1080p episode).
    If nothing fits, escalate to larger files before falling back to anything.
    For movies: pick the best-seeded match regardless of size.
    """
    from apps.downloads.models import DownloadItem

    if not results:
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
