import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from .tmdb import tmdb
from apps.arr.clients import ArrError
from apps.arr.services import image_url, in_thread, is_configured, library_index, parse_dt, radarr, sonarr

log = logging.getLogger('daredevil.media_tracker')


# ── Dashboard ────────────────────────────────────────────────────────────────

def _episode_tile(ep, queued_ids):
    series = ep.get('series') or {}
    air = parse_dt(ep.get('airDateUtc'))
    if ep.get('hasFile'):
        state = 'downloaded'
    elif ep['id'] in queued_ids:
        state = 'downloading'
    elif not ep.get('monitored'):
        state = 'unmonitored'
    else:
        state = 'missing'
    return {
        'series_id': ep.get('seriesId'),
        'show_title': series.get('title') or '',
        'poster': image_url(series.get('images'), 'poster'),
        'code': f'S{ep.get("seasonNumber", 0):02d}E{ep.get("episodeNumber", 0):02d}',
        'title': ep.get('title') or '',
        'air': timezone.localtime(air) if air else None,
        'state': state,
    }


def dashboard(request):
    """Counts and sliders straight from Sonarr/Radarr (calendar, queue, wanted)."""
    now = timezone.now()
    ctx = {
        'show_count': None, 'movie_count': None, 'downloading_count': None, 'missing_count': None,
        'upcoming_episodes': [], 'new_episodes': [], 'upcoming_movies': [], 'arr_errors': [],
        'sonarr_ready': is_configured('sonarr'), 'radarr_ready': is_configured('radarr'),
    }

    def _sonarr():
        c = sonarr()
        series = c.series_list()
        cal = c.calendar((now - timedelta(days=7)).isoformat(), (now + timedelta(days=21)).isoformat(),
                         includeSeries='true', unmonitored='false')
        queue = c.queue(includeEpisode='false')
        missing = c.wanted_missing(page_size=1, monitored='true')
        return series, cal, queue, missing

    def _radarr():
        c = radarr()
        return c.movies(), c.queue()

    jobs = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if ctx['sonarr_ready']:
            jobs['sonarr'] = pool.submit(in_thread, _sonarr)
        if ctx['radarr_ready']:
            jobs['radarr'] = pool.submit(in_thread, _radarr)
        results = {}
        for name, fut in jobs.items():
            try:
                results[name] = fut.result()
            except ArrError as e:
                ctx['arr_errors'].append(f'{name.title()}: {e}')

    downloading = 0
    missing = 0
    if 'sonarr' in results:
        series, cal, queue, wanted = results['sonarr']
        ctx['show_count'] = len(series)
        queued_ids = {r.get('episodeId') for r in queue.get('records', [])}
        downloading += len({r.get('downloadId') or r.get('id') for r in queue.get('records', [])})
        missing += wanted.get('totalRecords', 0)
        upcoming, recent = [], []
        for ep in cal:
            air = parse_dt(ep.get('airDateUtc'))
            if not air:
                continue
            (upcoming if air > now else recent).append(_episode_tile(ep, queued_ids))
        ctx['upcoming_episodes'] = sorted(upcoming, key=lambda e: e['air'])[:20]
        ctx['new_episodes'] = sorted(recent, key=lambda e: e['air'], reverse=True)[:20]
    if 'radarr' in results:
        movies, queue = results['radarr']
        ctx['movie_count'] = len(movies)
        downloading += len({r.get('downloadId') or r.get('id') for r in queue.get('records', [])})
        missing += sum(1 for m in movies if m.get('monitored') and not m.get('hasFile') and m.get('isAvailable'))
        waiting = []
        for m in movies:
            if m.get('monitored') and not m.get('hasFile') and not m.get('isAvailable'):
                dates = [parse_dt(m.get(k)) for k in ('digitalRelease', 'physicalRelease', 'inCinemas')]
                when = min((d for d in dates if d), default=None)
                waiting.append({
                    'id': m['id'], 'title': m.get('title'), 'year': m.get('year'),
                    'poster': image_url(m.get('images'), 'poster', size='w342'),
                    'when': timezone.localtime(when) if when else None,
                })
        ctx['upcoming_movies'] = sorted(waiting, key=lambda m: (m['when'] is None, m['when'] or now))[:20]
    ctx['downloading_count'] = downloading if jobs else None
    ctx['missing_count'] = missing if jobs else None
    return render(request, 'dashboard.html', ctx)


def _discover_spec(today):
    """The eight dashboard sliders: (key, TMDB method, is_tv, discover params)."""
    from datetime import timedelta as td
    this_week_start = today - td(days=today.weekday())
    last_week_start = this_week_start - td(days=7)
    last_week_end = last_week_start + td(days=6)
    return [
        ('tv_new_this_week', tmdb.discover_tv, True, {
            'first_air_date.gte': this_week_start.isoformat(),
            'first_air_date.lte': today.isoformat(),
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        ('tv_new_last_week', tmdb.discover_tv, True, {
            'first_air_date.gte': last_week_start.isoformat(),
            'first_air_date.lte': last_week_end.isoformat(),
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        ('tv_returning_this_week', tmdb.discover_tv, True, {
            'air_date.gte': this_week_start.isoformat(),
            'air_date.lte': today.isoformat(),
            'with_status': 0,
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        ('tv_returning_last_week', tmdb.discover_tv, True, {
            'air_date.gte': last_week_start.isoformat(),
            'air_date.lte': last_week_end.isoformat(),
            'with_status': 0,
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        # Movies new to rent/stream (digital release type 4)
        ('movies_rent_stream_this_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': (this_week_start - td(days=14)).isoformat(),
            'primary_release_date.lte': today.isoformat(),
            'with_release_type': '4',
            'sort_by': 'popularity.desc',
        }),
        ('movies_rent_stream_last_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': (last_week_start - td(days=14)).isoformat(),
            'primary_release_date.lte': last_week_end.isoformat(),
            'with_release_type': '4',
            'sort_by': 'popularity.desc',
        }),
        # Movies released theatrically this/last week
        ('movies_released_this_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': this_week_start.isoformat(),
            'primary_release_date.lte': today.isoformat(),
            'with_release_type': '3',
            'sort_by': 'popularity.desc',
        }),
        ('movies_released_last_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': last_week_start.isoformat(),
            'primary_release_date.lte': last_week_end.isoformat(),
            'with_release_type': '3',
            'sort_by': 'popularity.desc',
        }),
        # Upcoming / announced movies (next 6 months, by popularity)
        ('movies_announced', tmdb.discover_movie, False, {
            'primary_release_date.gte': today.isoformat(),
            'primary_release_date.lte': (today + td(days=180)).isoformat(),
            'sort_by': 'popularity.desc',
        }),
    ]


def discover_lists(refresh=False):
    """
    {slider key: TMDB results} for the dashboard, fetched in parallel. Each
    list sits in the shared cache for 30 minutes and warm_caches refetches
    them every 10, so the dashboard doesn't wait on TMDB.
    """
    from datetime import date

    def _fetch(entry):
        key, method, _is_tv, params = entry
        try:
            return key, method(refresh=refresh, **params).get('results', [])
        except Exception as e:
            log.info('discover %s: %s', key, e)
            return key, []

    spec = _discover_spec(date.today())
    with ThreadPoolExecutor(max_workers=len(spec)) as pool:
        return dict(pool.map(_fetch, spec))


def dashboard_discover(request):
    """AJAX — all 8 discover sliders, marked with what's already in Sonarr/Radarr."""
    from datetime import date

    img_base = 'https://image.tmdb.org/t/p/w300'
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_lists = pool.submit(discover_lists)
        f_shows = pool.submit(in_thread, library_index, 'sonarr')
        f_movies = pool.submit(in_thread, library_index, 'radarr')
        lists, shows_in_library, movies_in_library = f_lists.result(), f_shows.result(), f_movies.result()

    result = {}
    for key, _method, is_tv, _params in _discover_spec(date.today()):
        in_library = shows_in_library if is_tv else movies_in_library
        result[key] = [{
            'tmdb_id': r.get('id'),
            'title': r.get('name') if is_tv else r.get('title'),
            'poster': f'{img_base}{r["poster_path"]}' if r.get('poster_path') else None,
            'year': (r.get('first_air_date') or r.get('release_date') or '')[:4],
            'rating': round(r.get('vote_average', 0), 1),
            'library_id': in_library.get(r.get('id')),
        } for r in lists.get(key, [])[:20]]
    return JsonResponse(result)


# ── App Settings + Restart ───────────────────────────────────────────────────

def app_settings_view(request):
    from django.conf import settings as s
    from dotenv import dotenv_values
    env_file = s.BASE_DIR / '.env'
    env = dotenv_values(env_file) if env_file.exists() else {}

    def _get(key, default=''):
        # File is authoritative (all workers see the same file); fall back to
        # the module-level setting for keys not yet written to the file.
        return env.get(key, str(getattr(s, key, default)))

    return render(request, 'app_settings.html', {
        'settings_tabs': [
            ('general', 'General', 'fa-gear'),
            ('prowlarr', 'Prowlarr', 'fa-magnifying-glass'),
            ('sonarr', 'Sonarr', 'fa-tv'),
            ('radarr', 'Radarr', 'fa-film'),
            ('qbittorrent', 'qBittorrent', 'fa-seedling'),
            ('tasks', 'Background Tasks', 'fa-gears'),
        ],
        'cfg': {
            'TMDB_API_KEY':        _get('TMDB_API_KEY'),
            'REDIS_URL':           _get('REDIS_URL'),
            'TZ':                  _get('TZ', s.TIME_ZONE),
            'DEBUG':               _get('DEBUG', str(s.DEBUG)) == 'True',
            'ALLOWED_HOSTS':       _get('ALLOWED_HOSTS', ','.join(s.ALLOWED_HOSTS)),
            'PLEX_CLAIM':          _get('PLEX_CLAIM'),
            'PLEX_URL':            _get('PLEX_URL'),
            'PLEX_TOKEN':          _get('PLEX_TOKEN'),
            'PLEX_MOVIE_SECTION':  _get('PLEX_MOVIE_SECTION'),
            'PLEX_TV_SECTION':     _get('PLEX_TV_SECTION'),
            'NTFY_URL':            _get('NTFY_URL'),
            'NTFY_TOPIC':          _get('NTFY_TOPIC'),
            'NTFY_TOKEN':          _get('NTFY_TOKEN'),
            'PUID':                _get('PUID'),
            'PGID':                _get('PGID'),
        }
    })


@require_POST
def app_settings_save(request):
    from django.conf import settings as s
    data = json.loads(request.body)

    live = {}
    env_updates = {}
    needs_restart = []

    def _set(key, val, attr=None, restart=False):
        env_updates[key] = str(val)
        if restart:
            needs_restart.append(key)
        elif attr:
            setattr(s, attr, val)
            live[key] = val

    if 'TMDB_API_KEY' in data:
        _set('TMDB_API_KEY', data['TMDB_API_KEY'].strip(), attr='TMDB_API_KEY')
    if 'REDIS_URL' in data:
        _set('REDIS_URL', data['REDIS_URL'].strip(), restart=True)
    if 'TZ' in data:
        _set('TZ', data['TZ'].strip(), restart=True)
    if 'DEBUG' in data:
        _set('DEBUG', 'True' if data['DEBUG'] else 'False', restart=True)
    if 'ALLOWED_HOSTS' in data:
        _set('ALLOWED_HOSTS', data['ALLOWED_HOSTS'].strip(), restart=True)
    if 'PLEX_CLAIM' in data:
        _set('PLEX_CLAIM', data['PLEX_CLAIM'].strip(), attr='PLEX_CLAIM')
    if 'PLEX_URL' in data:
        _set('PLEX_URL', data['PLEX_URL'].strip(), attr='PLEX_URL')
    if 'PLEX_TOKEN' in data:
        _set('PLEX_TOKEN', data['PLEX_TOKEN'].strip(), attr='PLEX_TOKEN')
    if 'PLEX_MOVIE_SECTION' in data:
        _set('PLEX_MOVIE_SECTION', data['PLEX_MOVIE_SECTION'].strip(), attr='PLEX_MOVIE_SECTION')
    if 'PLEX_TV_SECTION' in data:
        _set('PLEX_TV_SECTION', data['PLEX_TV_SECTION'].strip(), attr='PLEX_TV_SECTION')
    if 'NTFY_URL' in data:
        _set('NTFY_URL', data['NTFY_URL'].strip(), attr='NTFY_URL')
    if 'NTFY_TOPIC' in data:
        _set('NTFY_TOPIC', data['NTFY_TOPIC'].strip(), attr='NTFY_TOPIC')
    if 'NTFY_TOKEN' in data:
        _set('NTFY_TOKEN', data['NTFY_TOKEN'].strip(), attr='NTFY_TOKEN')
    if 'PUID' in data:
        _set('PUID', str(data['PUID']).strip(), attr='PUID')
    if 'PGID' in data:
        _set('PGID', str(data['PGID']).strip(), attr='PGID')

    _update_env_file(env_updates)

    return JsonResponse({
        'ok': True,
        'live': list(live.keys()),
        'restart_required': needs_restart,
    })


def _update_env_file(updates: dict):
    import re
    from django.conf import settings as s
    env_file = s.BASE_DIR / '.env'
    if not env_file.exists():
        return
    text = env_file.read_text()
    for key, value in updates.items():
        pattern = re.compile(rf'^{re.escape(key)}\s*=.*', re.MULTILINE)
        replacement = f'{key}={value}'
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip('\n') + f'\n{replacement}\n'
    env_file.write_text(text)


def server_ping(request):
    return JsonResponse({'ok': True})


@require_POST
def server_restart(request):
    import threading
    from django.conf import settings as s

    def _touch_and_restart():
        import time
        time.sleep(0.4)
        manage_py = s.BASE_DIR / 'manage.py'
        manage_py.touch()

    threading.Thread(target=_touch_and_restart, daemon=True).start()
    return JsonResponse({'ok': True, 'msg': 'Django server restarting via auto-reloader…'})


@require_POST
def celery_restart(request):
    try:
        from config.celery import app as celery_app
        celery_app.control.broadcast('pool_restart', arguments={'reload': True})
        return JsonResponse({'ok': True, 'msg': 'Celery pool restart broadcast sent'})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)


@require_POST
def ntfy_test(request):
    """Send a test notification using the ntfy settings from the request body."""
    import requests as http_requests
    data = json.loads(request.body)
    ntfy_url   = (data.get('NTFY_URL') or 'https://ntfy.sh').rstrip('/')
    ntfy_topic = (data.get('NTFY_TOPIC') or '').strip()
    ntfy_token = (data.get('NTFY_TOKEN') or '').strip()

    if not ntfy_topic:
        return JsonResponse({'ok': False, 'error': 'NTFY_TOPIC is required'})

    headers = {'Title': 'Daredevil Test', 'Priority': '3', 'Tags': 'tada,clapper'}
    if ntfy_token:
        headers['Authorization'] = f'Bearer {ntfy_token}'

    try:
        r = http_requests.post(
            f'{ntfy_url}/{ntfy_topic}',
            data='Test notification from Daredevil — notifications are working!'.encode(),
            headers=headers,
            timeout=8,
        )
        r.raise_for_status()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


# ── Streaming browse ─────────────────────────────────────────────────────────

STREAMING_PROVIDERS = [
    {'key': 'netflix',  'id': 8,    'name': 'Netflix',   'abbr': 'N',   'bg': 'linear-gradient(135deg,#b20710,#e50914)', 'ring_color': '#e50914'},
    {'key': 'disney',   'id': 337,  'name': 'Disney+',   'abbr': 'D+',  'bg': 'linear-gradient(135deg,#001f6b,#0063e5)', 'ring_color': '#0063e5'},
    {'key': 'max',      'id': 1899, 'name': 'Max',       'abbr': 'Max', 'bg': 'linear-gradient(135deg,#001a6e,#002be7)', 'ring_color': '#6f7bff'},
    {'key': 'peacock',  'id': 386,  'name': 'Peacock',   'abbr': 'Pea', 'bg': 'linear-gradient(135deg,#000000,#000000)', 'ring_color': '#ffffff'},
    {'key': 'appletv',  'id': 350,  'name': 'Apple TV+', 'abbr': 'TV+', 'bg': 'linear-gradient(135deg,#1c1c1e,#3a3a3c)', 'ring_color': '#f5f5f7'},
    {'key': 'amazon',   'id': 9,    'name': 'Prime',     'abbr': 'P',   'bg': 'linear-gradient(135deg,#00a8e0,#006799)', 'ring_color': '#00a8e0'},
]

MOVIE_GENRES = [
    {'id': '',     'name': 'All'},
    {'id': 28,     'name': 'Action'},
    {'id': 35,     'name': 'Comedy'},
    {'id': 18,     'name': 'Drama'},
    {'id': 27,     'name': 'Horror'},
    {'id': 878,    'name': 'Sci-Fi'},
    {'id': 53,     'name': 'Thriller'},
    {'id': 10749,  'name': 'Romance'},
    {'id': 16,     'name': 'Animation'},
    {'id': 80,     'name': 'Crime'},
    {'id': 99,     'name': 'Documentary'},
    {'id': 10751,  'name': 'Family'},
    {'id': 14,     'name': 'Fantasy'},
]

TV_GENRES = [
    {'id': '',     'name': 'All'},
    {'id': 10759,  'name': 'Action'},
    {'id': 35,     'name': 'Comedy'},
    {'id': 18,     'name': 'Drama'},
    {'id': 10765,  'name': 'Sci-Fi'},
    {'id': 80,     'name': 'Crime'},
    {'id': 9648,   'name': 'Mystery'},
    {'id': 16,     'name': 'Animation'},
    {'id': 99,     'name': 'Documentary'},
    {'id': 10751,  'name': 'Family'},
    {'id': 10764,  'name': 'Reality'},
]


def streaming_params(provider, page=1, genre_id=''):
    """TMDB discover parameters for one provider's page (also used by warm_caches)."""
    params = {'with_watch_providers': provider['id'], 'watch_region': 'US', 'sort_by': 'popularity.desc', 'page': page}
    if genre_id:
        params['with_genres'] = genre_id
    return params


def streaming_browse(request):
    provider_key = request.GET.get('provider', 'netflix')
    media_type = request.GET.get('type', 'movie')
    genre_id = request.GET.get('genre', '')
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    provider = next((p for p in STREAMING_PROVIDERS if p['key'] == provider_key), STREAMING_PROVIDERS[0])
    genres = MOVIE_GENRES if media_type == 'movie' else TV_GENRES
    discover_kwargs = streaming_params(provider, page, genre_id)

    items = []
    total_pages = 1
    error = None
    try:
        # TMDB page (shared-cached, see tmdb.LIST_TTL) and the library index at the same time
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_data = pool.submit(tmdb.discover_tv if media_type == 'tv' else tmdb.discover_movie, **discover_kwargs)
            f_index = pool.submit(in_thread, library_index, 'sonarr' if media_type == 'tv' else 'radarr')
            data, existing_ids = f_data.result(), f_index.result()
        if media_type == 'tv':
            for r in data.get('results', []):
                items.append({
                    'id': r['id'],
                    'title': r.get('name', ''),
                    'year': (r.get('first_air_date') or '')[:4],
                    'poster_path': r.get('poster_path', ''),
                    'vote_average': r.get('vote_average', 0),
                    'overview': r.get('overview', ''),
                    'already_added': r['id'] in existing_ids,
                    'library_id': existing_ids.get(r['id']),
                    'media': 'tv',
                })
        else:
            for r in data.get('results', []):
                items.append({
                    'id': r['id'],
                    'title': r.get('title', ''),
                    'year': (r.get('release_date') or '')[:4],
                    'poster_path': r.get('poster_path', ''),
                    'vote_average': r.get('vote_average', 0),
                    'overview': r.get('overview', ''),
                    'already_added': r['id'] in existing_ids,
                    'library_id': existing_ids.get(r['id']),
                    'media': 'movie',
                })
        total_pages = min(data.get('total_pages', 1), 500)
    except Exception as e:
        error = str(e)

    if request.GET.get('json') == '1':
        return JsonResponse({'items': items, 'has_more': page < total_pages, 'page': page})

    return render(request, 'media_tracker/streaming.html', {
        'items': items,
        'providers': STREAMING_PROVIDERS,
        'provider': provider,
        'provider_key': provider_key,
        'media_type': media_type,
        'genre_id': genre_id,
        'genres': genres,
        'page': page,
        'total_pages': total_pages,
        'error': error,
    })


