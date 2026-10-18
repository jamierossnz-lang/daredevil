import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .client import get_plex, movie_to_dict, show_to_dict, item_to_dict, _file_size, _fmt_bytes
from .utils import get_disk_usage

log = logging.getLogger('daredevil.plex')



_30_DAYS  = timedelta(days=30)
_90_DAYS  = timedelta(days=90)
_180_DAYS = timedelta(days=180)
_365_DAYS = timedelta(days=365)


def _plex_web_url(request):
    """Plex's web app on the same host Daredevil was opened on. The bare
    :32400 address is Plex's API (raw XML) — the app itself lives at /web."""
    host = request.get_host().split(':')[0]
    return f'{request.scheme}://{host}:32400/web'


SNAPSHOT_KEY = 'plex_library_snapshot'
SNAPSHOT_TTL = 15 * 60  # warm_caches refreshes it every 10 minutes


def library_snapshot(plex, refresh=False):
    """
    Everything the Plex page shows from Plex, Sonarr and Radarr. The seven
    reads are independent so they run in parallel, and the result is kept in
    the shared cache (refreshed in the background by warm_caches, and
    dropped when something is deleted from this page).
    """
    from django.core.cache import cache
    data = None if refresh else cache.get(SNAPSHOT_KEY)
    if data is not None:
        return data

    from apps.arr.services import image_url, in_thread, is_configured, radarr, sonarr

    def movies():
        return [movie_to_dict(m) for m in plex.library.section(settings.PLEX_MOVIE_SECTION).all()]

    def shows():
        return [show_to_dict(s) for s in plex.library.section(settings.PLEX_TV_SECTION).all()]

    def recent_movies():
        return [d for d in (item_to_dict(i) for i in plex.library.section(settings.PLEX_MOVIE_SECTION).recentlyAdded()[:20]) if d]

    def recent_episodes():
        eps = plex.library.section(settings.PLEX_TV_SECTION).search(sort='addedAt:desc', libtype='episode', maxresults=20)
        return [d for d in (item_to_dict(e) for e in eps) if d]

    def watched_episodes():
        eps = plex.library.section(settings.PLEX_TV_SECTION).search(libtype='episode', sort='lastViewedAt:desc', maxresults=100)
        return [d for d in (item_to_dict(e) for e in eps) if d and d.get('last_viewed')]

    reads = {
        'movies': movies, 'shows': shows, 'recent_movies': recent_movies, 'recent_episodes': recent_episodes,
        'watched_episodes': watched_episodes,
        'arr_movies': lambda: radarr().movies() if is_configured('radarr') else [],
        'arr_series': lambda: sonarr().series_list() if is_configured('sonarr') else [],
    }
    results, failed = {}, {}
    with ThreadPoolExecutor(max_workers=len(reads)) as pool:
        futures = {k: pool.submit(in_thread, fn) for k, fn in reads.items()}
        for k, f in futures.items():
            try:
                results[k] = f.result()
            except Exception as e:
                log.warning('Plex page: %s failed — %s', k, e)
                results[k], failed[k] = [], e

    error = None
    if 'movies' in failed:
        error = f'Could not load Movies section "{settings.PLEX_MOVIE_SECTION}": {failed["movies"]}'
    elif 'shows' in failed:
        error = f'Could not load TV section "{settings.PLEX_TV_SECTION}": {failed["shows"]}'

    plex_movies, plex_shows = results['movies'], results['shows']
    arr_movies, arr_series = results['arr_movies'], results['arr_series']
    recent = sorted(results['recent_movies'] + results['recent_episodes'],
                    key=lambda x: x.get('added_at') or 0, reverse=True)[:24]

    # Recently watched: movies + shows by last viewed
    watched = sorted(
        [m for m in plex_movies if m.get('last_viewed')] + [s for s in plex_shows if s.get('view_count', 0) > 0],
        key=lambda x: x.get('last_viewed') or x.get('added_at') or 0, reverse=True,
    )[:24]
    # Individual movies + episodes for the delete tab
    watched_items = sorted(
        [m for m in plex_movies if m.get('last_viewed')] + results['watched_episodes'],
        key=lambda x: x.get('last_viewed') or 0, reverse=True,
    )[:80]

    # ── Cross-reference with Sonarr / Radarr (the library) ──
    movie_tmdb_to_id = {str(m['tmdbId']): m['id'] for m in arr_movies if m.get('tmdbId')}
    show_tmdb_to_id = {str(s['tmdbId']): s['id'] for s in arr_series if s.get('tmdbId')}
    show_name_to_id = {(s.get('title') or '').lower(): s['id'] for s in arr_series}

    def _enrich(items, is_show=False):
        for item in items:
            tmdb = item.get('tmdb_id')
            if is_show or item.get('type') == 'show':
                sid = (tmdb and show_tmdb_to_id.get(tmdb)) or \
                      show_name_to_id.get((item.get('parent_title') or '').lower()) or \
                      show_name_to_id.get((item.get('title') or '').lower())
                if sid:
                    item['detail_url'] = f'/shows/{sid}/'
            else:
                mid = tmdb and movie_tmdb_to_id.get(tmdb)
                if mid:
                    item['detail_url'] = f'/movies/{mid}/'

    _enrich(plex_movies)
    _enrich(plex_shows, is_show=True)
    _enrich(recent)
    _enrich(watched)

    # ── In Sonarr/Radarr with files on disk, but not in Plex ──
    plex_movie_tmdb = {str(m['tmdb_id']) for m in plex_movies if m.get('tmdb_id')}
    plex_show_tmdb = {str(s['tmdb_id']) for s in plex_shows if s.get('tmdb_id')}
    data = {
        'error': error,
        'plex_movies': plex_movies,
        'plex_shows': plex_shows,
        'recent': recent,
        'watched': watched,
        'watched_items': watched_items,
        'missing_movies': [
            {'id': m['id'], 'title': m.get('title'), 'year': m.get('year'),
             'poster': image_url(m.get('images'), 'poster', size='w92')}
            for m in arr_movies if m.get('hasFile') and str(m.get('tmdbId')) not in plex_movie_tmdb
        ],
        'missing_shows': [
            {'id': s['id'], 'title': s.get('title'), 'year': s.get('year'),
             'poster': image_url(s.get('images'), 'poster')}
            for s in arr_series
            if (s.get('statistics') or {}).get('episodeFileCount', 0) > 0 and str(s.get('tmdbId')) not in plex_show_tmdb
        ],
        'fetched_at': timezone.now(),
    }
    # A failed read isn't cached, so the next visit tries again
    if not failed:
        cache.set(SNAPSHOT_KEY, data, SNAPSHOT_TTL)
    return data


def library(request):
    plex = get_plex()
    if not plex:
        return render(request, 'plex/library.html', {
            'configured': False,
            'error': 'Could not connect to Plex. Check PLEX_URL and PLEX_TOKEN in App Settings.',
            'plex_web_url': _plex_web_url(request),
        })

    if request.GET.get('refresh') == '1':
        library_snapshot(plex, refresh=True)
        return redirect(request.path)
    data = library_snapshot(plex)

    # ── Cleanup candidates (ignored items can change between snapshots) ──
    from .models import PlexIgnored
    ignored_keys = set(PlexIgnored.objects.values_list('rating_key', flat=True))
    all_items = [i for i in data['plex_movies'] + data['plex_shows'] if i.get('rating_key') not in ignored_keys]
    candidates = _build_candidates(all_items, timezone.now().replace(tzinfo=None))

    return render(request, 'plex/library.html', {
        'configured': True,
        **data,
        'candidates': candidates,
        'drives': get_disk_usage(),
        'plex_web_url': _plex_web_url(request),
    })


def _rescan_plex(plex, item_type):
    """Tell Plex to scan the affected library section so the entry disappears."""
    try:
        for section in plex.library.sections():
            if (item_type == 'movie' and section.type == 'movie') or \
               (item_type in ('show', 'season', 'episode') and section.type == 'show'):
                section.update()
    except Exception as exc:
        log.warning('plex_delete: library scan failed: %s', exc)


def _build_candidates(items, now):
    """
    Categorise Plex items into deletion candidates.
    Returns a list of dicts: {label, description, color, icon, items}.
    Items in an earlier category are excluded from later ones.
    """
    seen = set()

    def _take(pred, iterable):
        out = []
        for item in iterable:
            key = item.get('rating_key')
            if key in seen:
                continue
            if pred(item):
                out.append(item)
                seen.add(key)
        return out

    def _dt(item, field):
        v = item.get(field)
        return v if v else None

    # Cat 1: Watched recently (last 30 days) — you finished it, clear the space
    cat1 = _take(
        lambda i: _dt(i, 'last_viewed') and _dt(i, 'last_viewed') >= now - _30_DAYS,
        items,
    )

    # Cat 2: Watched but not touched in 6+ months — unlikely to rewatch
    cat2 = _take(
        lambda i: _dt(i, 'last_viewed') and _dt(i, 'last_viewed') < now - _180_DAYS,
        items,
    )

    # Cat 3: Completed TV series — all episodes watched, finished 90+ days ago
    cat3 = _take(
        lambda i: (
            i.get('type') == 'show'
            and i.get('total_episodes', 0) > 0
            and i.get('view_count', 0) >= i.get('total_episodes', 1)
            and (_dt(i, 'last_viewed') or now) < now - _90_DAYS
        ),
        items,
    )

    # Cat 4: Partially watched, abandoned 90+ days ago — started but never finished
    cat4 = _take(
        lambda i: (
            i.get('view_count', 0) > 0
            and (_dt(i, 'last_viewed') or now) < now - _90_DAYS
        ),
        items,
    )

    # Cat 5: Long abandoned — never watched, in library 1+ year
    cat5 = _take(
        lambda i: (
            not i.get('view_count', 0)
            and _dt(i, 'added_at')
            and _dt(i, 'added_at') < now - _365_DAYS
        ),
        items,
    )

    # Cat 6: Never watched, 30+ days old — stale additions
    cat6 = _take(
        lambda i: (
            not i.get('view_count', 0)
            and _dt(i, 'added_at')
            and _dt(i, 'added_at') < now - _30_DAYS
        ),
        items,
    )

    categories = []
    from .client import _fmt_bytes

    def _add(label, description, color, icon, cat_items):
        if cat_items:
            total = sum(i.get('size_bytes', 0) for i in cat_items)
            categories.append({
                'label': label,
                'description': description,
                'color': color,
                'icon': icon,
                'items': cat_items,
                'total_bytes': total,
                'total_display': _fmt_bytes(total),
            })

    _add('Recently Watched',         'Watched in the last 30 days — safe to clear space',                 'green',  'fa-circle-check',       cat1)
    _add('Watched Long Ago',         'Last watched 6+ months ago — unlikely to rewatch',                  'yellow', 'fa-clock-rotate-left',  cat2)
    _add('Completed Series',         'All episodes watched and finished 90+ days ago',                    'cyan',   'fa-flag-checkered',     cat3)
    _add('Abandoned (Part-Watched)', 'Started but not touched for 90+ days',                              'orange', 'fa-circle-pause',       cat4)
    _add('Long Abandoned',           'Never watched, sitting in library for over a year',                 'red',    'fa-skull',              cat5)
    _add('Stale Addition',           'Never watched, added 30+ days ago — maybe reconsider keeping it',  'gray',   'fa-hourglass-half',     cat6)

    return categories


# ── Action endpoints ──────────────────────────────────────────────────────────

def _delete_via_arr(item, item_type, tmdb_id):
    """
    Delete through Sonarr/Radarr when they manage the item, so they don't
    simply re-download it: a movie/show is removed from Radarr/Sonarr along
    with its files; an episode/season has its files deleted and is
    unmonitored. Returns a description of what was done, or None if the item
    isn't managed by Sonarr/Radarr (caller falls back to deleting files).
    """
    from apps.arr.clients import ArrError
    from apps.arr.services import invalidate_library, is_configured, library_index, radarr, sonarr
    from .client import extract_tmdb_id

    try:
        if item_type == 'movie' and tmdb_id and is_configured('radarr'):
            movie = radarr().movie_by_tmdb(int(tmdb_id))
            if movie:
                radarr().delete_movie(movie['id'], delete_files=True)
                invalidate_library('radarr')
                return f'removed {movie.get("title")} from Radarr (files deleted)'
            return None

        if item_type not in ('show', 'season', 'episode') or not is_configured('sonarr'):
            return None
        show = item if item_type == 'show' else item.show()
        show_tmdb = tmdb_id if item_type == 'show' else extract_tmdb_id(show)
        series_id = show_tmdb and library_index('sonarr').get(int(show_tmdb))
        if not series_id:
            return None
        c = sonarr()
        if item_type == 'show':
            c.delete_series(series_id, delete_files=True)
            invalidate_library('sonarr')
            return 'removed the show from Sonarr (files deleted)'

        episodes = c.episodes(series_id)
        if item_type == 'season':
            targets = [e for e in episodes if e.get('seasonNumber') == item.index]
        else:
            targets = [e for e in episodes
                       if e.get('seasonNumber') == item.parentIndex and e.get('episodeNumber') == item.index]
        if not targets:
            return None
        # Unmonitor first so Sonarr doesn't immediately grab them again
        c.set_episodes_monitored([e['id'] for e in targets], False)
        deleted = 0
        for e in targets:
            if e.get('episodeFileId'):
                c.delete_episode_file(e['episodeFileId'])
                deleted += 1
        return f'deleted {deleted} episode file(s) via Sonarr and unmonitored them'
    except ArrError as e:
        log.warning('plex_delete: Sonarr/Radarr delete failed, falling back to filesystem — %s', e)
        return None


@require_POST
def plex_delete(request, rating_key):
    """
    Delete a Plex item. Anything Sonarr/Radarr manage is deleted through
    them (see _delete_via_arr); anything else has its files removed
    directly. Either way Plex then rescans so the entry disappears.
    """
    from .client import extract_tmdb_id

    plex = get_plex()
    if not plex:
        return JsonResponse({'error': 'Plex not connected'}, status=503)
    try:
        item = plex.fetchItem(int(rating_key))
        item_type  = getattr(item, 'type', '')
        item_title = getattr(item, 'title', '')
        tmdb_id    = extract_tmdb_id(item)

        from django.core.cache import cache
        cache.delete(SNAPSHOT_KEY)
        via_arr = _delete_via_arr(item, item_type, tmdb_id)
        if via_arr:
            log.info('plex_delete: ratingKey=%s %r — %s', rating_key, item_title, via_arr)
            _rescan_plex(plex, item_type)
            return JsonResponse({'ok': True, 'deleted': [], 'warnings': [], 'via': via_arr})

        # Plex runs in our docker-compose with /data/movies and /data/tv mounts.
        # The web container has the same dirs at /media/movies and /media/tv.
        path_map = [
            ('/data/movies', '/media/movies'),
            ('/data/tv',     '/media/tv'),
        ]

        def translate(plex_path):
            for plex_prefix, local_prefix in path_map:
                if plex_path.startswith(plex_prefix + '/') or plex_path == plex_prefix:
                    return local_prefix + plex_path[len(plex_prefix):]
            return plex_path

        locations = []
        try:
            locations = list(item.locations)
        except Exception:
            for media in getattr(item, 'media', []) or []:
                for part in getattr(media, 'parts', []) or []:
                    if getattr(part, 'file', None):
                        locations.append(part.file)

        deleted_paths, errors = [], []
        for loc in locations:
            local = translate(loc)
            try:
                if os.path.isdir(local):
                    shutil.rmtree(local)
                    deleted_paths.append(local)
                elif os.path.isfile(local):
                    os.remove(local)
                    # Remove parent dir if now empty
                    parent = os.path.dirname(local)
                    if os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
                    deleted_paths.append(local)
                else:
                    errors.append(f'Not found: {local} (Plex reported: {loc})')
            except Exception as exc:
                errors.append(f'{local}: {exc}')

        log.info('plex_delete: ratingKey=%s %r deleted=%s errors=%s',
                 rating_key, item_title, deleted_paths, errors)

        _rescan_plex(plex, item_type)

        if errors and not deleted_paths:
            return JsonResponse({'error': '; '.join(errors)}, status=500)

        return JsonResponse({'ok': True, 'deleted': deleted_paths, 'warnings': errors})
    except Exception as e:
        log.warning('plex_delete failed for ratingKey=%s: %s', rating_key, e)
        return JsonResponse({'error': str(e)}, status=500)


@require_GET
def plex_episodes(request, rating_key):
    """Return all episodes for a show grouped by season, for the episode picker modal."""
    plex = get_plex()
    if not plex:
        return JsonResponse({'error': 'Plex not connected'}, status=503)
    try:
        show = plex.fetchItem(int(rating_key))
        seasons = []
        for season in show.seasons():
            eps = []
            for ep in season.episodes():
                eps.append({
                    'rating_key': str(ep.ratingKey),
                    'title':      ep.title or '',
                    'season':     ep.parentIndex or 0,
                    'episode':    ep.index or 0,
                    'viewed':     bool(getattr(ep, 'viewCount', 0)),
                    'size':       _fmt_bytes(_file_size(ep)),
                })
            seasons.append({
                'title':    season.title or f'Season {season.index}',
                'index':    season.index or 0,
                'episodes': eps,
            })
        return JsonResponse({'title': show.title, 'seasons': seasons})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_POST
def plex_ignore(request, rating_key):
    """Dismiss an item from cleanup candidates (don't delete, just hide it)."""
    from .models import PlexIgnored
    title = request.POST.get('title', '')
    PlexIgnored.objects.get_or_create(rating_key=rating_key, defaults={'title': title})
    return JsonResponse({'ok': True})


_thumb_local = threading.local()


def thumb(request):
    """Proxy Plex thumbnails — keeps token server-side and handles Docker networking."""
    from .client import _plex_setting

    path = request.GET.get('path', '')
    if not path or not path.startswith('/'):
        return HttpResponse(status=400)

    plex_url = _plex_setting('PLEX_URL', 'PLEX_URL')
    plex_token = _plex_setting('PLEX_TOKEN', 'PLEX_TOKEN')
    if not plex_url or not plex_token:
        return HttpResponse(status=503)

    # A page of posters is dozens of requests — reuse one connection per thread
    session = getattr(_thumb_local, 'session', None)
    if session is None:
        session = _thumb_local.session = requests.Session()
    try:
        # Plex's own resizer — a poster-sized JPEG is about a tenth of the original artwork
        r = session.get(f'{plex_url.rstrip("/")}/photo/:/transcode', timeout=8, params={
            'url': path, 'width': 300, 'height': 450, 'upscale': 0, 'X-Plex-Token': plex_token,
        })
        r.raise_for_status()
        resp = HttpResponse(r.content, content_type=r.headers.get('Content-Type', 'image/jpeg'))
        resp['Cache-Control'] = 'max-age=86400'
        return resp
    except Exception as e:
        log.warning('thumb proxy failed for %r: %s', path, e)
        return HttpResponse(status=502)
