import logging
from concurrent.futures import ThreadPoolExecutor

from celery import shared_task

from apps.events.dispatch import emit
from apps.events.models import EventType

log = logging.getLogger('daredevil.tasks')

# The old search / download / file-move / episode-status / release-date jobs
# are gone: Sonarr and Radarr do all of that themselves now (RSS sync,
# searching, grabbing via qBittorrent, importing, renaming), and report back
# through webhooks — see apps.arr.webhooks.


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


@shared_task(name='warm_caches')
def warm_caches():
    """
    Refetch the slow, shared data behind the Dashboard, Streaming and Plex
    pages, so they open straight from the cache instead of waiting on TMDB
    and Plex. Every cached copy outlives the interval this runs at.
    """
    from apps.media_tracker.tmdb import tmdb
    from apps.media_tracker.views import STREAMING_PROVIDERS, STREAMING_SORTS, discover_lists, streaming_params
    from apps.plex.client import get_plex
    from apps.plex.views import library_snapshot

    warmed = []
    discover_lists(refresh=True)
    warmed.append('dashboard')

    def warm_streaming(provider, media_type, sort):
        fetch = tmdb.discover_tv if media_type == 'tv' else tmdb.discover_movie
        try:
            fetch(refresh=True, **streaming_params(provider, sort=sort, media_type=media_type))
        except Exception as e:
            log.info('warm_caches: streaming %s %s %s — %s', provider['key'], media_type, sort, e)

    # First page of every service × Movies/TV × sort
    with ThreadPoolExecutor(max_workers=6) as pool:
        for provider in STREAMING_PROVIDERS:
            for media_type in ('movie', 'tv'):
                for sort, _label in STREAMING_SORTS:
                    pool.submit(warm_streaming, provider, media_type, sort)
    warmed.append('streaming')
    plex = get_plex()
    if plex:
        library_snapshot(plex, refresh=True)
        warmed.append('plex')
    return f'warmed {", ".join(warmed)}'

