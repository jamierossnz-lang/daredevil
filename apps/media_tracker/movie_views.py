"""
Movies — Daredevil is the interface, Radarr is the library.

Movies are read live from Radarr and every action goes back through its API.
Radarr's minimum availability (announced / in cinemas / released) is what
watches unreleased movies now: add one monitored and Radarr grabs it on its
own once it's out — no Daredevil release-date jobs involved.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.arr.clients import ArrError
from apps.arr.http import json_body as _body, json_error as _fail
from apps.arr.queue import queue_entry
from apps.arr.services import (
    FAVOURITE_TAG, FOR_YOU_TAG, MIN_AVAILABILITY_CHOICES, add_movie, delete_file, download_movie, favourite_tag_id,
    fmt_bytes, grab_release, image_url, invalidate_library, is_configured, library_index, normalize_release,
    parse_dt, public_url, radarr, refresh_movie, set_movie_monitored, tag_ids, toggle_favourite,
    update_media_fields,
)
from .tmdb import tmdb

log = logging.getLogger('daredevil.movies')

STATUS_FILTERS = [
    ('downloaded', 'Downloaded'),
    ('downloading', 'Downloading'),
    ('missing', 'Missing'),
    ('waiting', 'Waiting for release'),
    ('unmonitored', 'Unmonitored'),
    ('favourite', 'Favourites'),
]


def _release_info(m):
    """The date that matters for 'when will Radarr grab this'."""
    cinemas = parse_dt(m.get('inCinemas'))
    digital = parse_dt(m.get('digitalRelease'))
    physical = parse_dt(m.get('physicalRelease'))
    home = min((d for d in (digital, physical) if d), default=None)
    availability = m.get('minimumAvailability') or 'released'
    expected = cinemas if availability in ('announced', 'inCinemas') else home
    return {
        'in_cinemas': cinemas, 'digital': digital, 'physical': physical,
        'expected': expected, 'availability': availability,
    }


def _movie_card(m, fav_tag=None, queued=None, for_you_tag=None):
    has_file = bool(m.get('hasFile'))
    queue = (queued or {}).get(m['id'])
    if has_file:
        state = 'downloaded'
    elif queue:
        state = 'downloading'
    elif not m.get('monitored'):
        state = 'unmonitored'
    elif m.get('isAvailable'):
        state = 'missing'
    else:
        state = 'waiting'
    f = m.get('movieFile') or {}
    return {
        'id': m['id'],
        'title': m.get('title') or '',
        'sort_title': m.get('sortTitle') or (m.get('title') or '').lower(),
        'year': m.get('year') or '',
        'status': m.get('status') or '',
        'poster': image_url(m.get('images'), 'poster', size='w342'),
        'monitored': bool(m.get('monitored')),
        'favourite': fav_tag is not None and fav_tag in (m.get('tags') or []),
        'for_you': for_you_tag is not None and for_you_tag in (m.get('tags') or []),
        'added': parse_dt(m.get('added')),
        'has_file': has_file,
        'state': state,
        'queue': queue,
        'available': bool(m.get('isAvailable')),
        'quality': ((f.get('quality') or {}).get('quality') or {}).get('name', ''),
        'size': fmt_bytes(m.get('sizeOnDisk')),
        'tmdb_id': m.get('tmdbId'),
        **_release_info(m),
    }


def _queue_by_movie(records):
    return {q['movieId']: queue_entry(q) for q in records or [] if q.get('movieId')}


# ── Pages ─────────────────────────────────────────────────────────────────────

def movies(request):
    if not is_configured('radarr'):
        return render(request, 'media_tracker/arr_not_configured.html', {'app': 'Radarr', 'kind': 'radarr'})
    error = None
    items = []
    try:
        c = radarr()
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_movies = pool.submit(c.movies)
            f_queue = pool.submit(c.queue)
            f_tags = pool.submit(c.tags)
            all_movies = f_movies.result()
            queue = _queue_by_movie((f_queue.result() or {}).get('records'))
            tags = {(t.get('label') or '').lower(): t['id'] for t in f_tags.result()}
        items = sorted((_movie_card(m, tags.get(FAVOURITE_TAG), queue, tags.get(FOR_YOU_TAG)) for m in all_movies),
                       key=lambda m: m['sort_title'])
    except ArrError as e:
        error = str(e)
    return render(request, 'media_tracker/movies.html', {
        'movies': items,
        'error': error,
        'q': request.GET.get('q', '').strip(),
        'status_filter': request.GET.get('status', ''),
        'status_choices': STATUS_FILTERS,
    })


def movie_detail(request, movie_id):
    if not is_configured('radarr'):
        return render(request, 'media_tracker/arr_not_configured.html', {'app': 'Radarr', 'kind': 'radarr'})
    c = radarr()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_movie = pool.submit(c.movie, movie_id)
            f_queue = pool.submit(c.queue_details, movie_id)
            f_profiles = pool.submit(c.quality_profiles)
            f_fav = pool.submit(favourite_tag_id, c)
            m = f_movie.result()
            # The rest is optional — the page still opens without it
            queue = _queue_by_movie([{**q, 'movieId': movie_id} for q in (_optional(f_queue) or [])])
            profiles = _optional(f_profiles) or []
            fav = _optional(f_fav)
    except ArrError as e:
        if e.status == 404:
            raise Http404('Movie not found in Radarr')
        return render(request, 'media_tracker/arr_error.html', {'app': 'Radarr', 'error': str(e)})

    movie = _movie_card(m, fav, queue)
    f = m.get('movieFile') or {}
    ratings = m.get('ratings') or {}
    movie.update({
        'overview': m.get('overview') or '',
        'fanart': image_url(m.get('images'), 'fanart', size='w1280'),
        'fanart_small': image_url(m.get('images'), 'fanart', size='w780'),
        'genres': ', '.join(m.get('genres') or []),
        'runtime': m.get('runtime') or None,
        'certification': m.get('certification') or '',
        'studio': m.get('studio') or '',
        'path': m.get('path') or '',
        'rating': ((ratings.get('tmdb') or {}).get('value')) or ((ratings.get('imdb') or {}).get('value')) or 0,
        'quality_profile_id': m.get('qualityProfileId'),
        'file_id': m.get('movieFileId') or None,
        'file_path': f.get('relativePath') or '',
        'file_size': fmt_bytes(f.get('size')),
        'trailer': m.get('youTubeTrailerId') or '',
        'imdb_id': m.get('imdbId') or '',
        'collection': (m.get('collection') or {}).get('title') or '',
    })

    # Cast / recommendations are TMDB metadata — nothing to do with downloads.
    cast, directors, similar = [], [], []
    tmdb_id = m.get('tmdbId')
    if tmdb_id:
        credits, recs = _tmdb_extras(tmdb_id)
        directors = [p['name'] for p in credits.get('crew', []) if p.get('job') == 'Director']
        for person in credits.get('cast', [])[:10]:
            profile = person.get('profile_path')
            cast.append({
                'name': person.get('name', ''),
                'character': person.get('character', ''),
                'photo': f'https://image.tmdb.org/t/p/w185{profile}' if profile else None,
            })
        in_library = library_index('radarr')
        for r in recs.get('results', [])[:12]:
            poster = r.get('poster_path')
            similar.append({
                'tmdb_id': r.get('id'),
                'title': r.get('title', ''),
                'year': (r.get('release_date') or '')[:4],
                'rating': round(r.get('vote_average', 0), 1),
                'poster': f'https://image.tmdb.org/t/p/w300{poster}' if poster else None,
                'movie_id': in_library.get(r.get('id')),
            })

    return render(request, 'media_tracker/movie_detail.html', {
        'movie': movie,
        'cast': cast,
        'directors': directors,
        'similar': similar,
        'quality_profiles': profiles,
        'availability_choices': MIN_AVAILABILITY_CHOICES,
        'radarr_link': f'{public_url("radarr", request)}/movie/{m.get("titleSlug") or m.get("tmdbId") or movie_id}',
    })


def _optional(future):
    try:
        return future.result()
    except ArrError as e:
        log.info('movie detail: optional call failed — %s', e)
        return None


def _tmdb_extras(tmdb_id):
    """(credits, recommendations) from TMDB — fetched in parallel and cached,
    since they rarely change and TMDB can take several seconds per call."""
    from django.core.cache import cache

    key = f'tmdb_movie_extras_{tmdb_id}'
    cached = cache.get(key)
    if cached is not None:
        return cached

    def _safe(fn):
        try:
            return fn(tmdb_id)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_credits = pool.submit(_safe, tmdb.get_movie_credits)
        f_recs = pool.submit(_safe, tmdb.get_movie_recommendations)
        credits, recs = f_credits.result(), f_recs.result()
    result = (credits or {}, recs or {})
    if credits is not None and recs is not None:
        cache.set(key, result, 6 * 3600)
    return result


def movie_by_tmdb(request, tmdb_id):
    movie_id = library_index('radarr').get(tmdb_id)
    if movie_id:
        return redirect('movie_detail', movie_id=movie_id)
    return redirect(f'/movies/search/?tmdb={tmdb_id}')


def movie_search(request):
    if not is_configured('radarr'):
        return render(request, 'media_tracker/arr_not_configured.html', {'app': 'Radarr', 'kind': 'radarr'})
    query = request.GET.get('q', '').strip()
    tmdb_id = request.GET.get('tmdb', '').strip()
    results, error = [], None
    if tmdb_id.isdigit() and not query:
        query = f'tmdb:{tmdb_id}'
    if query:
        try:
            in_library = library_index('radarr')
            for r in radarr().lookup(query)[:24]:
                results.append({
                    'tmdb_id': r.get('tmdbId'),
                    'title': r.get('title'),
                    'year': r.get('year') or '',
                    'status': r.get('status') or '',
                    'overview': r.get('overview') or '',
                    'poster': r.get('remotePoster') or image_url(r.get('images'), 'poster', size='w342'),
                    'rating': ((r.get('ratings') or {}).get('tmdb') or {}).get('value') or 0,
                    'runtime': r.get('runtime') or None,
                    'movie_id': in_library.get(r.get('tmdbId')),
                })
        except ArrError as e:
            error = str(e)
    context = {'results': results, 'query': '' if query.startswith('tmdb:') else query, 'error': error}
    if request.headers.get('HX-Request'):
        return render(request, 'media_tracker/_movie_search_results.html', context)
    return render(request, 'media_tracker/tmdb_search_movie.html', context)


# ── Adding ────────────────────────────────────────────────────────────────────

@require_POST
def movie_add(request):
    data = _body(request) if request.content_type == 'application/json' else request.POST
    tmdb_id = str(data.get('tmdb_id') or '').strip()
    if not tmdb_id.isdigit():
        return JsonResponse({'status': 'error', 'message': 'Missing TMDB id'}, status=400)

    def _opt(name):
        v = data.get(name)
        return None if v in (None, '') else v

    search = _opt('search')
    labels = data.get('tags') if isinstance(data.get('tags'), list) else []
    try:
        movie, created = add_movie(
            tmdb_id=int(tmdb_id),
            quality_profile_id=_opt('quality_profile_id'),
            root_folder=_opt('root_folder'),
            monitor=_opt('monitor'),
            minimum_availability=_opt('minimum_availability'),
            search=None if search is None else str(search).lower() in ('1', 'true', 'on'),
            tags=tag_ids('radarr', labels) if labels else None,
        )
    except ArrError as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({
        'status': 'added' if created else 'exists',
        'pk': movie.get('id'),
        'id': movie.get('id'),
        'title': movie.get('title'),
        'url': f'/movies/{movie.get("id")}/',
        'available': bool(movie.get('isAvailable')),
    })


# ── Actions ───────────────────────────────────────────────────────────────────

@require_POST
def movie_monitor(request, movie_id):
    data = _body(request)
    try:
        monitored = bool(data['monitored']) if 'monitored' in data else not radarr().movie(movie_id).get('monitored')
        set_movie_monitored(movie_id, monitored)
        return JsonResponse({'ok': True, 'monitored': monitored})
    except ArrError as e:
        return _fail(e)


@require_POST
def movie_download(request, movie_id):
    try:
        download_movie(movie_id)
        return JsonResponse({'ok': True, 'message': 'Searching — Radarr will grab the best release it finds'})
    except ArrError as e:
        return _fail(e)


@require_POST
def movie_refresh(request, movie_id):
    try:
        refresh_movie(movie_id)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


def movie_releases(request, movie_id):
    try:
        releases = [normalize_release(r) for r in radarr().releases(movieId=movie_id)]
        return JsonResponse({'ok': True, 'releases': releases})
    except ArrError as e:
        return _fail(e, status=200)


@require_POST
def release_grab(request):
    data = _body(request)
    if not data.get('guid') or not str(data.get('indexer_id', '')).isdigit():
        return _fail('Missing release')
    try:
        grab_release('radarr', data['guid'], data['indexer_id'])
        return JsonResponse({'ok': True})
    except ArrError as e:
        msg = str(e)
        if e.status == 404:
            msg = 'Radarr no longer has this release cached — search again.'
        return _fail(msg)


@require_POST
def movie_file_delete(request, file_id):
    """'Reset & redownload' — delete the file, then (optionally) search again."""
    data = _body(request)
    try:
        delete_file('radarr', file_id)
        if data.get('search') and data.get('movie_id'):
            download_movie(int(data['movie_id']))
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


@require_POST
def movie_edit(request, movie_id):
    data = _body(request)
    fields = {}
    if str(data.get('quality_profile_id', '')).isdigit():
        fields['qualityProfileId'] = int(data['quality_profile_id'])
    if data.get('minimum_availability') in dict(MIN_AVAILABILITY_CHOICES):
        fields['minimumAvailability'] = data['minimum_availability']
    if not fields:
        return _fail('Nothing to change')
    try:
        update_media_fields('radarr', movie_id, **fields)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


@require_POST
def movie_favourite(request, movie_id):
    try:
        return JsonResponse({'ok': True, 'is_favourite': toggle_favourite('radarr', movie_id)})
    except ArrError as e:
        return _fail(e)


@require_POST
def movie_delete(request, movie_id):
    data = _body(request) if request.content_type == 'application/json' else request.POST
    delete_files = str(data.get('delete_files', '')).lower() in ('1', 'true', 'on')
    try:
        radarr().delete_movie(movie_id, delete_files=delete_files)
        invalidate_library('radarr')
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


def movie_status(request, movie_id):
    try:
        c = radarr()
        queue = _queue_by_movie([{**q, 'movieId': movie_id} for q in c.queue_details(movie_id)])
        m = c.movie(movie_id)
    except ArrError as e:
        return _fail(e, status=200)
    return JsonResponse({'ok': True, 'queue': queue.get(movie_id), 'has_file': bool(m.get('hasFile'))})
