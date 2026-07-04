import logging
import os
import shutil
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .client import get_plex, movie_to_dict, show_to_dict, item_to_dict, _file_size, _fmt_bytes
from .utils import get_disk_usage

log = logging.getLogger('daredevil.plex')



_30_DAYS  = timedelta(days=30)
_90_DAYS  = timedelta(days=90)
_180_DAYS = timedelta(days=180)
_365_DAYS = timedelta(days=365)


def library(request):
    plex = get_plex()
    if not plex:
        return render(request, 'plex/library.html', {
            'configured': False,
            'error': 'Could not connect to Plex. Check PLEX_URL and PLEX_TOKEN in App Settings.',
        })

    plex_movies, plex_shows, recent, watched = [], [], [], []
    error = None

    try:
        movie_section = plex.library.section(settings.PLEX_MOVIE_SECTION)
        plex_movies = [movie_to_dict(m) for m in movie_section.all()]
    except Exception as e:
        log.warning('Plex movies fetch failed: %s', e)
        error = f'Could not load Movies section "{settings.PLEX_MOVIE_SECTION}": {e}'

    try:
        tv_section = plex.library.section(settings.PLEX_TV_SECTION)
        plex_shows = [show_to_dict(s) for s in tv_section.all()]
    except Exception as e:
        log.warning('Plex TV fetch failed: %s', e)
        if not error:
            error = f'Could not load TV section "{settings.PLEX_TV_SECTION}": {e}'

    try:
        recent_raw = []
        try:
            ms = plex.library.section(settings.PLEX_MOVIE_SECTION)
            recent_raw += list(ms.recentlyAdded()[:20])
        except Exception as e:
            log.warning('Plex recent movies: %s', e)
        try:
            ts = plex.library.section(settings.PLEX_TV_SECTION)
            recent_raw += list(ts.search(sort='addedAt:desc', libtype='episode', maxresults=20))
        except Exception as e:
            log.warning('Plex recent TV episodes: %s', e)
        recent = sorted(
            [d for d in (item_to_dict(i) for i in recent_raw) if d],
            key=lambda x: x.get('added_at') or 0,
            reverse=True,
        )[:24]
    except Exception as e:
        log.warning('Plex recent fetch failed: %s', e)

    # Recently watched: merge movies + shows sorted by lastViewedAt
    try:
        def _sort_key(x):
            return x.get('last_viewed') or x.get('added_at') or 0

        watched = sorted(
            [m for m in plex_movies if m.get('last_viewed')] +
            [s for s in plex_shows  if s.get('view_count', 0) > 0],
            key=_sort_key, reverse=True
        )[:24]
    except Exception as e:
        log.warning('Plex watched merge failed: %s', e)

    # Recently watched individual items (movies + episodes) for the delete tab
    watched_items = []
    try:
        raw_watched = []
        try:
            ms = plex.library.section(settings.PLEX_MOVIE_SECTION)
            raw_watched += [m for m in ms.search(sort='lastViewedAt:desc', maxresults=60)
                            if getattr(m, 'viewCount', 0)]
        except Exception as e:
            log.warning('Plex watched movies fetch: %s', e)
        try:
            ts = plex.library.section(settings.PLEX_TV_SECTION)
            raw_watched += [e for e in ts.search(sort='lastViewedAt:desc', libtype='episode', maxresults=60)
                            if getattr(e, 'viewCount', 0)]
        except Exception as e:
            log.warning('Plex watched episodes fetch: %s', e)
        watched_items = sorted(
            [d for d in (item_to_dict(i) for i in raw_watched) if d],
            key=lambda x: x.get('last_viewed') or 0, reverse=True
        )[:80]
    except Exception as e:
        log.warning('Plex watched_items fetch failed: %s', e)

    # ── Cross-reference with Daredevil ──
    from apps.media_tracker.models import Movie, TVShow, Episode

    movie_tmdb_to_pk = {str(m.tmdb_id): m.pk for m in Movie.objects.all()}
    show_tmdb_to_pk  = {str(s.tmdb_id): s.pk for s in TVShow.objects.all()}
    show_name_to_pk  = {s.name.lower(): s.pk for s in TVShow.objects.all()}

    def _enrich(items, is_show=False):
        for item in items:
            tmdb = item.get('tmdb_id')
            if is_show or item.get('type') == 'show':
                pk = (tmdb and show_tmdb_to_pk.get(tmdb)) or \
                     show_name_to_pk.get(item.get('parent_title', '')) or \
                     show_name_to_pk.get(item.get('title', '').lower())
                if pk:
                    item['detail_url'] = f'/shows/{pk}/'
            else:
                pk = tmdb and movie_tmdb_to_pk.get(tmdb)
                if pk:
                    item['detail_url'] = f'/movies/{pk}/'

    _enrich(plex_movies)
    _enrich(plex_shows, is_show=True)
    _enrich(recent)
    _enrich(watched)

    # ── Not in Plex ──
    plex_movie_tmdb = {m['tmdb_id'] for m in plex_movies if m.get('tmdb_id')}
    plex_show_tmdb  = {s['tmdb_id'] for s in plex_shows  if s.get('tmdb_id')}

    downloaded_movies = list(Movie.objects.filter(download_status=Movie.DownloadStatus.DOWNLOADED))
    missing_movies    = [m for m in downloaded_movies if str(m.tmdb_id) not in plex_movie_tmdb]

    shows_with_downloads = set(
        Episode.objects.filter(download_status=Episode.DownloadStatus.DOWNLOADED)
        .values_list('season__show__tmdb_id', flat=True)
    )
    missing_shows = list(TVShow.objects.filter(
        tmdb_id__in=shows_with_downloads,
    ).exclude(
        tmdb_id__in=[int(x) for x in plex_show_tmdb if x and x.isdigit()]
    ))

    # ── Cleanup candidates ──
    from .models import PlexIgnored
    ignored_keys = set(PlexIgnored.objects.values_list('rating_key', flat=True))
    all_items = [i for i in plex_movies + plex_shows if i.get('rating_key') not in ignored_keys]

    now = timezone.now()

    candidates = _build_candidates(all_items, now.replace(tzinfo=None))
    drives = get_disk_usage()

    return render(request, 'plex/library.html', {
        'configured': True,
        'error': error,
        'plex_movies': plex_movies,
        'plex_shows': plex_shows,
        'recent': recent,
        'watched': watched,
        'missing_movies': missing_movies,
        'missing_shows': missing_shows,
        'candidates': candidates,
        'watched_items': watched_items,
        'drives': drives,
    })


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

@require_POST
def plex_delete(request, rating_key):
    """
    Delete a Plex item by removing its files directly from the filesystem,
    then trigger a Plex library scan to remove the entry from Plex.
    Also removes the matching Movie/TVShow from the Daredevil library.
    """
    from .client import extract_tmdb_id
    from apps.media_tracker.models import Movie, TVShow

    plex = get_plex()
    if not plex:
        return JsonResponse({'error': 'Plex not connected'}, status=503)
    try:
        item = plex.fetchItem(int(rating_key))
        item_type  = getattr(item, 'type', '')
        item_title = getattr(item, 'title', '')
        tmdb_id    = extract_tmdb_id(item)

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

        # Tell Plex to scan the affected library section so the entry disappears
        try:
            for section in plex.library.sections():
                if (item_type == 'movie' and section.type == 'movie') or \
                   (item_type in ('show', 'season', 'episode') and section.type == 'show'):
                    section.update()
        except Exception as exc:
            log.warning('plex_delete: library scan failed: %s', exc)

        # Remove from Daredevil library
        if tmdb_id:
            if item_type == 'movie':
                n, _ = Movie.objects.filter(tmdb_id=int(tmdb_id)).delete()
                if n:
                    log.info('plex_delete: removed Movie tmdb_id=%s', tmdb_id)
            elif item_type == 'show':
                n, _ = TVShow.objects.filter(tmdb_id=int(tmdb_id)).delete()
                if n:
                    log.info('plex_delete: removed TVShow tmdb_id=%s', tmdb_id)

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


def thumb(request):
    """Proxy Plex thumbnails — keeps token server-side and handles Docker networking."""
    path = request.GET.get('path', '')
    if not path or not path.startswith('/'):
        return HttpResponse(status=400)

    plex_url   = getattr(settings, 'PLEX_URL', '')
    plex_token = getattr(settings, 'PLEX_TOKEN', '')
    if not plex_url or not plex_token:
        return HttpResponse(status=503)

    url = f'{plex_url}{path}?X-Plex-Token={plex_token}'
    try:
        r = http_requests.get(url, timeout=8)
        resp = HttpResponse(r.content, content_type=r.headers.get('Content-Type', 'image/jpeg'))
        resp['Cache-Control'] = 'max-age=86400'
        return resp
    except Exception as e:
        log.warning('thumb proxy failed for %r: %s', path, e)
        return HttpResponse(status=502)
