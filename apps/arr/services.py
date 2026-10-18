"""
Everything Daredevil does *through* Sonarr / Radarr / Prowlarr.

Sonarr and Radarr are the source of truth for the library: Daredevil stores
no shows, movies or downloads of its own any more — it reads them live from
the APIs and sends every action (add, monitor, search, grab, retry, delete)
back through them. Views call the helpers here rather than the clients
directly so the multi-step flows (monitor → search, lookup → add, …) exist
in exactly one place.
"""
import html
import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .clients import ArrError, ArrNotConfigured, ProwlarrClient, RadarrClient, SonarrClient
from .models import ArrService

log = logging.getLogger('daredevil.arr')

_CLIENTS = {'sonarr': SonarrClient, 'radarr': RadarrClient, 'prowlarr': ProwlarrClient}


def client(kind):
    svc = ArrService.get(kind)
    return _CLIENTS[kind](svc.url, svc.api_key)


def sonarr():
    return client('sonarr')


def radarr():
    return client('radarr')


def prowlarr():
    return client('prowlarr')


def is_configured(kind):
    return ArrService.get(kind).is_configured


def in_thread(fn, *args, **kwargs):
    """Run fn in a worker thread (ThreadPoolExecutor.submit target) and close
    the DB connection it may have opened — Django only closes connections for
    request threads, and Postgres here allows only 100, shared with Sonarr,
    Radarr and Prowlarr."""
    from django.db import connection
    try:
        return fn(*args, **kwargs)
    finally:
        connection.close()


def public_url(kind, request=None):
    """Browser-facing link to the app's own UI. Falls back to the same host
    the user reached Daredevil on, with the app's default port — right for
    the stock docker-compose setup without any configuration."""
    svc = ArrService.get(kind)
    if svc.public_url:
        return svc.public_url.rstrip('/')
    if request is not None:
        from .models import DEFAULT_PORTS
        host = request.get_host().split(':')[0]
        return f'{request.scheme}://{host}:{DEFAULT_PORTS[kind]}'
    return svc.url


# ── Option lists (shared by the add modals and the Settings tabs) ────────────

SONARR_MONITOR_CHOICES = [
    ('all', 'All Episodes'),
    ('future', 'Future Episodes'),
    ('missing', 'Missing Episodes'),
    ('existing', 'Existing Episodes'),
    ('recent', 'Recent Episodes'),
    ('pilot', 'Pilot Episode'),
    ('firstSeason', 'First Season'),
    ('lastSeason', 'Last Season'),
    ('none', 'None'),
]
# Settings → Sonarr default only: resolved per show when it's added
SONARR_DEFAULT_MONITOR_CHOICES = [('auto', 'Automatic — future episodes if still airing, none if ended')] + SONARR_MONITOR_CHOICES
SERIES_TYPE_CHOICES = [('standard', 'Standard'), ('daily', 'Daily / Date-based'), ('anime', 'Anime / Absolute')]
RADARR_MONITOR_CHOICES = [('movieOnly', 'Movie Only'), ('movieAndCollection', 'Movie and Collection'), ('none', 'None')]
MIN_AVAILABILITY_CHOICES = [('announced', 'Announced'), ('inCinemas', 'In Cinemas'), ('released', 'Released')]


def add_options(kind):
    """Root folders, quality profiles and saved defaults for an add-show/movie form."""
    svc = ArrService.get(kind)
    c = client(kind)
    folders = c.root_folders()
    profiles = c.quality_profiles()
    data = {
        'root_folders': [{'path': f['path'], 'free': fmt_bytes(f.get('freeSpace'))} for f in folders],
        'quality_profiles': [{'id': p['id'], 'name': p['name']} for p in profiles],
        'defaults': {
            'root_folder': svc.root_folder or (folders[0]['path'] if folders else ''),
            'quality_profile_id': svc.quality_profile_id or (profiles[0]['id'] if profiles else None),
            'monitor': svc.monitor or ('auto' if kind == 'sonarr' else 'movieOnly'),
            'search': svc.search_on_add,
        },
    }
    if kind == 'sonarr':
        data['monitor_choices'] = SONARR_MONITOR_CHOICES
        data['series_type_choices'] = SERIES_TYPE_CHOICES
        data['defaults'].update(series_type=svc.series_type, season_folder=svc.season_folder)
    else:
        data['monitor_choices'] = RADARR_MONITOR_CHOICES
        data['availability_choices'] = MIN_AVAILABILITY_CHOICES
        data['defaults'].update(minimum_availability=svc.minimum_availability)
    return data


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_bytes(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return '—'
    if n <= 0:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit not in ('B', 'KB') else f'{n:.0f} {unit}'
        n /= 1024
    return f'{n:.1f} PB'


def parse_dt(value):
    """ISO-8601 from the APIs → aware datetime (None if missing/invalid)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def image_url(images, cover_type='poster', size='w500'):
    """Remote artwork URL from an *arr `images` list. TMDB originals are
    multi-megabyte, so they're rewritten to a sized variant."""
    for img in images or []:
        if img.get('coverType') == cover_type:
            url = img.get('remoteUrl') or ''
            if url and size and '/t/p/original/' in url:
                url = url.replace('/t/p/original/', f'/t/p/{size}/')
            return url or None
    return None


# ── Library index (tmdb_id → Sonarr/Radarr id) ────────────────────────────────
#
# Used all over the app for "already in library" badges (dashboard sliders,
# streaming browse, For You, Plex, recommendations). Cached briefly so a page
# with dozens of cards costs one API call, and dropped whenever Daredevil
# itself (or a Sonarr/Radarr webhook) adds or removes something.

_INDEX_TTL = 60


def library_index(kind):
    key = f'arr_library_index_{kind}'
    idx = cache.get(key)
    if idx is not None:
        return idx
    try:
        items = sonarr().series_list() if kind == 'sonarr' else radarr().movies()
    except ArrError as e:
        log.info('library_index(%s): %s', kind, e)
        return {}
    idx = {int(i['tmdbId']): i['id'] for i in items if i.get('tmdbId')}
    cache.set(key, idx, _INDEX_TTL)
    return idx


def invalidate_library(kind=None):
    kinds = [kind] if kind else ['sonarr', 'radarr']
    cache.delete_many([f'arr_library_index_{k}' for k in kinds])


# ── Tags (favourites and For You picks live in Sonarr/Radarr as tags) ────────

FAVOURITE_TAG = 'favourite'
FOR_YOU_TAG = 'for-you'


def tag_id(c, label, create=False):
    for t in c.tags():
        if (t.get('label') or '').lower() == label:
            return t['id']
    if create:
        return c.create_tag(label)['id']
    return None


def favourite_tag_id(c, create=False):
    return tag_id(c, FAVOURITE_TAG, create)


def tag_ids(kind, labels):
    """Sonarr/Radarr tag ids for these labels, creating any that are missing."""
    c = client(kind)
    return [tag_id(c, label.lower(), create=True) for label in labels or [] if label]


def toggle_favourite(kind, item_id):
    c = client(kind)
    item = c.series(item_id) if kind == 'sonarr' else c.movie(item_id)
    tag = favourite_tag_id(c, create=True)
    tags = set(item.get('tags') or [])
    is_fav = tag not in tags
    if is_fav:
        tags.add(tag)
    else:
        tags.discard(tag)
    item['tags'] = sorted(tags)
    (c.update_series if kind == 'sonarr' else c.update_movie)(item)
    return is_fav


# ── Adding ────────────────────────────────────────────────────────────────────

def _first(items, key):
    return items[0][key] if items else None


def resolve_sonarr_monitor(monitor, status):
    """'auto' (the default) → future episodes for a show that's still
    airing or not out yet, nothing for one that has ended."""
    if monitor and monitor != 'auto':
        return monitor
    return 'none' if (status or '').lower() == 'ended' else 'future'


# ── Lookup (what the Add dialog shows before adding) ──────────────────────────

_LOOKUP_TTL = 600


def lookup_item(kind, tmdb_id=None, tvdb_id=None):
    """
    One show/movie as Sonarr/Radarr would add it: title, year, poster,
    status and — if it's already there — its library id. Cached for a few
    minutes; the in-library check is always fresh.
    """
    if kind == 'sonarr':
        if not (tvdb_id or tmdb_id):
            raise ArrError('No show id given')
        term = f'tvdb:{tvdb_id}' if tvdb_id else f'tmdb:{tmdb_id}'
        key = f'arr_lookup_sonarr_{term}'
        info = cache.get(key)
        if info is None:
            results = sonarr().lookup(term)
            if not results:
                raise ArrError(f'Sonarr could not find this show ({term}) — it may not be on TheTVDB yet')
            r = results[0]
            seasons = sorted(x['seasonNumber'] for x in r.get('seasons') or [] if x.get('seasonNumber', 0) > 0)
            info = {
                'kind': 'sonarr', 'title': r.get('title'), 'year': r.get('year') or '',
                'tvdb_id': r.get('tvdbId'), 'tmdb_id': r.get('tmdbId'),
                'status': r.get('status') or '', 'network': r.get('network') or '',
                'overview': r.get('overview') or '',
                'poster': r.get('remotePoster') or image_url(r.get('images'), 'poster'),
                'seasons': seasons,
                'suggested_monitor': resolve_sonarr_monitor('auto', r.get('status')),
            }
            cache.set(key, info, _LOOKUP_TTL)
        series_id = library_index('sonarr').get(int(info['tmdb_id'])) if info.get('tmdb_id') else None
        if not series_id and info.get('tvdb_id'):
            series_id = next((x['id'] for x in sonarr().series_list() if x.get('tvdbId') == info['tvdb_id']), None)
        return {**info, 'id': series_id, 'in_library': bool(series_id),
                'url': f'/shows/{series_id}/' if series_id else None}

    if not tmdb_id:
        raise ArrError('No movie id given')
    key = f'arr_lookup_radarr_{tmdb_id}'
    info = cache.get(key)
    if info is None:
        r = radarr().lookup_tmdb(tmdb_id)
        if not r or not r.get('tmdbId'):
            raise ArrError('Radarr could not find this movie on TMDB')
        info = {
            'kind': 'radarr', 'title': r.get('title'), 'year': r.get('year') or '', 'tmdb_id': r.get('tmdbId'),
            'status': r.get('status') or '', 'overview': r.get('overview') or '',
            'poster': r.get('remotePoster') or image_url(r.get('images'), 'poster'),
            'in_cinemas': r.get('inCinemas'), 'digital_release': r.get('digitalRelease'),
            'physical_release': r.get('physicalRelease'),
        }
        cache.set(key, info, _LOOKUP_TTL)
    movie_id = library_index('radarr').get(int(tmdb_id))
    return {**info, 'id': movie_id, 'in_library': bool(movie_id), 'url': f'/movies/{movie_id}/' if movie_id else None}


def wait_for_episodes(series_id, timeout=30):
    """A just-added show has no episodes until Sonarr's first refresh
    finishes — season searches need them, so wait (briefly) for it."""
    import time
    deadline = time.monotonic() + timeout
    while True:
        if sonarr().episodes(series_id):
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(1.5)


def add_series(*, tvdb_id=None, tmdb_id=None, quality_profile_id=None, root_folder=None, monitor=None,
               series_type=None, season_folder=None, search=None, tags=None, path=None):
    """
    Add a show to Sonarr. Idempotent: returns (series, created) and hands
    back the existing series untouched if it's already in the library.
    Accepts a TVDB id (from Sonarr's own search) or a TMDB id (every other
    entry point in the app is TMDB-based — Sonarr resolves it itself).
    """
    if not tvdb_id and not tmdb_id:
        raise ArrError('No show id given')
    svc = ArrService.get('sonarr')
    c = sonarr()
    term = f'tvdb:{tvdb_id}' if tvdb_id else f'tmdb:{tmdb_id}'
    results = c.lookup(term)
    if not results:
        raise ArrError(f'Sonarr could not find this show ({term}) — it may not be on TheTVDB yet')
    series = results[0]
    if series.get('id'):
        return series, False

    profile = quality_profile_id or svc.quality_profile_id or _first(c.quality_profiles(), 'id')
    root = root_folder or svc.root_folder or _first(c.root_folders(), 'path')
    if not root:
        raise ArrError('Sonarr has no root folder yet — add one in Settings → Sonarr')
    monitor = resolve_sonarr_monitor(monitor or svc.monitor, series.get('status'))
    search = svc.search_on_add if search is None else search

    payload = {
        **series,
        'qualityProfileId': int(profile),
        'rootFolderPath': root,
        'seasonFolder': svc.season_folder if season_folder is None else bool(season_folder),
        'seriesType': series_type or svc.series_type or 'standard',
        'monitored': monitor != 'none',
        # "Add to Library" (none) stays library-only — new seasons aren't monitored either
        'monitorNewItems': 'none' if monitor == 'none' else 'all',
        'tags': list(tags or []),
        'addOptions': {
            'monitor': monitor,
            'searchForMissingEpisodes': bool(search) and monitor != 'none',
            'searchForCutoffUnmetEpisodes': False,
        },
    }
    if path:
        payload['path'] = path
    added = c.add_series(payload)
    invalidate_library('sonarr')
    log.info('add_series: added %r (sonarr id=%s, monitor=%s, search=%s)', added.get('title'), added.get('id'), monitor, search)
    return added, True


def add_movie(*, tmdb_id, quality_profile_id=None, root_folder=None, monitor=None, minimum_availability=None,
              search=None, tags=None, path=None):
    """Add a movie to Radarr. Idempotent, returns (movie, created).

    Radarr's minimum availability is what replaces Daredevil's old
    'waiting for digital release' logic — an unreleased movie is added
    monitored and Radarr grabs it itself once it's available."""
    svc = ArrService.get('radarr')
    c = radarr()
    existing = c.movie_by_tmdb(tmdb_id)
    if existing:
        return existing, False
    movie = c.lookup_tmdb(tmdb_id)
    if not movie or not movie.get('tmdbId'):
        raise ArrError('Radarr could not find this movie on TMDB')

    profile = quality_profile_id or svc.quality_profile_id or _first(c.quality_profiles(), 'id')
    root = root_folder or svc.root_folder or _first(c.root_folders(), 'path')
    if not root:
        raise ArrError('Radarr has no root folder yet — add one in Settings → Radarr')
    monitor = monitor or svc.monitor or 'movieOnly'
    search = svc.search_on_add if search is None else search

    payload = {
        **movie,
        'qualityProfileId': int(profile),
        'rootFolderPath': root,
        'minimumAvailability': minimum_availability or svc.minimum_availability or 'released',
        'monitored': monitor != 'none',
        'tags': list(tags or []),
        'addOptions': {'monitor': monitor, 'searchForMovie': bool(search) and monitor != 'none'},
    }
    if path:
        payload['path'] = path
    added = c.add_movie(payload)
    invalidate_library('radarr')
    log.info('add_movie: added %r (radarr id=%s, search=%s)', added.get('title'), added.get('id'), search)
    return added, True


# ── Monitoring (TV) ───────────────────────────────────────────────────────────

def set_series_monitored(series_id, monitored):
    c = sonarr()
    s = c.series(series_id)
    s['monitored'] = bool(monitored)
    c.update_series(s)
    return s


def apply_series_monitoring(series_id, monitor):
    """Re-apply a Sonarr monitoring preset (all/future/missing/…/none)."""
    sonarr().season_pass(series_id, monitor, monitored=(monitor != 'none'))


def set_season_monitored(series_id, season_number, monitored):
    c = sonarr()
    s = c.series(series_id)
    for season in s.get('seasons', []):
        if season['seasonNumber'] == season_number:
            season['monitored'] = bool(monitored)
    if monitored:
        # A monitored season under an unmonitored series is ignored by
        # Sonarr entirely, which would look like the toggle did nothing.
        s['monitored'] = True
    c.update_series(s)
    return s


def set_episodes_monitored(episode_ids, monitored):
    sonarr().set_episodes_monitored(episode_ids, monitored)


# ── Downloading (TV) — "download" = monitor + automatic search ───────────────

def download_series(series_id):
    """Whole show: monitor every episode of every regular season ('all' —
    episodes someone unmonitored one by one included), then search them all."""
    c = sonarr()
    c.season_pass(int(series_id), 'all', monitored=True)
    return c.command('SeriesSearch', seriesId=int(series_id))


def download_season(series_id, season_number):
    """Monitor the season and every episode in it, then search. Sonarr tries
    a season pack first and falls back to single episodes itself — the old
    Daredevil season-fallback logic isn't needed."""
    c = sonarr()
    set_season_monitored(series_id, season_number, True)
    ids = [e['id'] for e in c.episodes(series_id) if e.get('seasonNumber') == int(season_number)]
    if ids:
        # A season that was already monitored can still have individually
        # unmonitored episodes, which SeasonSearch would silently skip.
        c.set_episodes_monitored(ids, True)
    return c.command('SeasonSearch', seriesId=int(series_id), seasonNumber=int(season_number))


def download_episodes(episode_ids):
    c = sonarr()
    ids = [int(i) for i in episode_ids]
    c.set_episodes_monitored(ids, True)
    return c.command('EpisodeSearch', episodeIds=ids)


def search_monitored(series_id):
    """'Search monitored' — like Sonarr's own button, doesn't change monitoring."""
    return sonarr().command('SeriesSearch', seriesId=int(series_id))


def refresh_series(series_id):
    return sonarr().command('RefreshSeries', seriesId=int(series_id))


# ── Movies ────────────────────────────────────────────────────────────────────

def set_movie_monitored(movie_id, monitored):
    c = radarr()
    m = c.movie(movie_id)
    m['monitored'] = bool(monitored)
    c.update_movie(m)
    return m


def download_movie(movie_id):
    c = radarr()
    m = c.movie(movie_id)
    if not m.get('monitored'):
        m['monitored'] = True
        c.update_movie(m)
    return c.command('MoviesSearch', movieIds=[int(movie_id)])


def refresh_movie(movie_id):
    return radarr().command('RefreshMovie', movieIds=[int(movie_id)])


def update_media_fields(kind, item_id, **fields):
    """Change e.g. qualityProfileId / minimumAvailability on an existing item."""
    c = client(kind)
    item = c.series(item_id) if kind == 'sonarr' else c.movie(item_id)
    item.update(fields)
    (c.update_series if kind == 'sonarr' else c.update_movie)(item)
    return item


# ── Interactive search (pick a release) ───────────────────────────────────────

def normalize_release(r):
    quality = ((r.get('quality') or {}).get('quality') or {}).get('name', '')
    return {
        'guid': r.get('guid'),
        'indexer_id': r.get('indexerId'),
        'indexer': (r.get('indexer') or '').replace(' (Prowlarr)', ''),
        'title': r.get('title') or '',
        'size': r.get('size') or 0,
        'size_fmt': fmt_bytes(r.get('size')),
        'seeders': r.get('seeders'),
        'leechers': r.get('leechers'),
        'quality': quality,
        'age': r.get('age'),
        'age_hours': r.get('ageHours'),
        'protocol': r.get('protocol'),
        'rejected': bool(r.get('rejected')),
        'rejections': r.get('rejections') or [],
        'approved': bool(r.get('approved')),
        'full_season': bool(r.get('fullSeason')),
        'episodes': r.get('mappedEpisodeNumbers') or r.get('episodeNumbers') or [],
        'info_url': r.get('infoUrl') or '',
        'languages': [lang.get('name') for lang in r.get('languages') or [] if lang.get('name')],
        'custom_format_score': r.get('customFormatScore') or 0,
    }


def grab_release(kind, guid, indexer_id):
    client(kind).grab_release(guid, int(indexer_id))


# ── Retry / reset ─────────────────────────────────────────────────────────────

def queue_remove(kind, ids, blocklist=False, search_again=False, remove_from_client=True):
    """
    Remove a download from the Sonarr/Radarr queue (and qBittorrent).
    blocklist + search_again is the "retry with a different release" action:
    the current release is blocklisted and a replacement search starts
    immediately.
    """
    client(kind).queue_remove(
        ids, remove_from_client=remove_from_client, blocklist=blocklist,
        skip_redownload=not search_again,
    )


def delete_file(kind, file_id):
    """'Reset' an episode/movie: delete its file so it shows as missing and
    can be downloaded again (Sonarr/Radarr grab it automatically if monitored)."""
    c = client(kind)
    if kind == 'sonarr':
        c.delete_episode_file(file_id)
    else:
        c.delete_movie_file(file_id)


# ── Wiring the apps together (Settings → "Connect" buttons) ───────────────────

def _set_fields(payload, values):
    for f in payload.get('fields', []):
        if f.get('name') in values:
            f['value'] = values[f['name']]


def _field(payload, name, default=None):
    for f in payload.get('fields', []):
        if f.get('name') == name:
            return f.get('value', default)
    return default


def webhook_url(kind):
    base = getattr(settings, 'DAREDEVIL_INTERNAL_URL', 'http://web:8000').rstrip('/')
    secret = ArrService.get(kind).ensure_webhook_secret()
    return f'{base}/arr/webhook/{kind}/{secret}/'


_WEBHOOK_EVENTS = {
    'sonarr': ('onGrab', 'onDownload', 'onUpgrade', 'onManualInteractionRequired', 'onSeriesAdd', 'onSeriesDelete'),
    'radarr': ('onGrab', 'onDownload', 'onUpgrade', 'onManualInteractionRequired', 'onMovieAdded', 'onMovieDelete'),
}


def is_daredevil_webhook(n):
    return n.get('implementation') == 'Webhook' and (n.get('name') or '').lower() == 'daredevil'


def _find_daredevil_webhook(c):
    return next((n for n in c.notifications() if is_daredevil_webhook(n)), None)


def ensure_webhook(kind):
    """Create (or repair) a 'Daredevil' webhook connection in Sonarr/Radarr so
    grabs and imports reach Daredevil the moment they happen — this is what
    replaces the old background polling jobs."""
    c = client(kind)
    hook = _find_daredevil_webhook(c)
    payload = hook or next(s for s in c.notification_schema() if s.get('implementation') == 'Webhook')
    payload['name'] = 'Daredevil'
    _set_fields(payload, {'url': webhook_url(kind), 'method': 1})
    for event in _WEBHOOK_EVENTS[kind]:
        supports = 'supports' + event[0].upper() + event[1:]
        if payload.get(supports, True):
            payload[event] = True
    saved = c.update_notification(payload) if hook else c.add_notification(payload)
    return saved


_QBT_CATEGORY_FIELD = {'sonarr': 'tvCategory', 'radarr': 'movieCategory', 'prowlarr': 'category'}
DEFAULT_QBT_CATEGORY = {'sonarr': 'sonarr', 'radarr': 'radarr', 'prowlarr': 'prowlarr'}


def ensure_qbittorrent(kind, category=None):
    """
    Point Sonarr/Radarr/Prowlarr at the same qBittorrent Daredevil uses.

    A new connection gets Daredevil's qBittorrent login; an existing one only
    has its category updated — its own (working) login is left alone, since
    overwriting it with a wrong one would make qBittorrent ban the app after
    a few failed attempts.
    """
    c = client(kind)
    existing = next((d for d in c.download_clients() if d.get('implementation') == 'QBittorrent'), None)
    payload = existing or next(s for s in c.download_client_schema() if s.get('implementation') == 'QBittorrent')
    payload['name'] = payload.get('name') or 'qBittorrent'
    payload['enable'] = True
    values = {}
    if not existing:
        values.update({
            'host': settings.QBITTORRENT_HOST,
            'port': int(settings.QBITTORRENT_PORT),
            'username': settings.QBITTORRENT_USERNAME,
            'password': settings.QBITTORRENT_PASSWORD,
        })
    cat_field = _QBT_CATEGORY_FIELD[kind]
    values[cat_field] = category or _field(payload, cat_field) or DEFAULT_QBT_CATEGORY[kind]
    _set_fields(payload, values)
    return c.update_download_client(payload) if existing else c.add_download_client(payload)


def ensure_prowlarr_app(kind):
    """Register Sonarr/Radarr as an Application in Prowlarr (full sync), so
    every indexer added to Prowlarr shows up in Sonarr/Radarr automatically."""
    p = prowlarr()
    target = ArrService.get(kind)
    if not target.is_configured:
        raise ArrNotConfigured(f'{kind.title()} needs a URL and API key first')
    impl = 'Sonarr' if kind == 'sonarr' else 'Radarr'
    existing = next((a for a in p.applications() if a.get('implementation') == impl), None)
    payload = existing or next(s for s in p.application_schema() if s.get('implementation') == impl)
    payload['name'] = payload.get('name') or impl
    payload['syncLevel'] = 'fullSync'
    _set_fields(payload, {
        'prowlarrUrl': ArrService.get('prowlarr').url,
        'baseUrl': target.url,
        'apiKey': target.api_key,
    })
    saved = p.update_application(payload) if existing else p.add_application(payload)
    p.command('ApplicationIndexerSync')
    return saved


# ── Prowlarr indexer catalogue ────────────────────────────────────────────────

_SCHEMA_KEY = 'prowlarr_indexer_schema'


def indexer_schema(refresh=False):
    data = None if refresh else cache.get(_SCHEMA_KEY)
    if data is None:
        data = prowlarr().indexer_schema()
        cache.set(_SCHEMA_KEY, data, 6 * 3600)
    return data


def definition_key(d):
    return f'{d.get("implementation")}:{d.get("definitionName") or d.get("name")}'


def find_definition(key):
    for d in indexer_schema():
        if definition_key(d) == key:
            return d
    return None


def indexer_form_fields(definition):
    """Prowlarr field metadata → a flat list the Settings form can render."""
    fields = []
    for f in definition.get('fields', []):
        if f.get('hidden') == 'hidden':
            continue
        options = [{'value': o.get('value'), 'name': o.get('name')} for o in f.get('selectOptions') or []]
        if f.get('name') == 'baseUrl' and not options:
            options = [{'value': u, 'name': u} for u in definition.get('indexerUrls') or []]
        value = f.get('value')
        if f.get('name') == 'baseUrl' and not value and options:
            value = options[0]['value']
        fields.append({
            'name': f.get('name'),
            'label': f.get('label') or f.get('name'),
            'type': f.get('type'),
            'value': value,
            'advanced': bool(f.get('advanced')),
            'help': f.get('helpText') or '',
            'options': options,
            'secret': f.get('privacy') in ('password', 'apiKey'),
        })
    return fields


def apply_form_values(definition, values):
    """Merge {field_name: value} from the form back into a Prowlarr payload."""
    for f in definition.get('fields', []):
        name = f.get('name')
        if name not in values:
            continue
        v = values[name]
        if f.get('type') == 'number':
            v = None if v in ('', None) else (float(v) if '.' in str(v) else int(v))
        elif f.get('type') == 'checkbox':
            v = bool(v)
        elif f.get('type') == 'select' and isinstance(f.get('value'), int) and str(v).lstrip('-').isdigit():
            v = int(v)
        f['value'] = v
    return definition


# ── Prowlarr search ───────────────────────────────────────────────────────────

def normalize_search_result(r):
    cats = r.get('categories') or []
    return {
        'guid': r.get('guid'),
        'indexer_id': r.get('indexerId'),
        'indexer': r.get('indexer') or '',
        # Some indexers send HTML-escaped titles ("Tom &amp; Jerry")
        'title': html.unescape(r.get('title') or ''),
        'size': r.get('size') or 0,
        'size_fmt': fmt_bytes(r.get('size')),
        'seeders': r.get('seeders'),
        'leechers': r.get('leechers'),
        'grabs': r.get('grabs'),
        'age': r.get('age'),
        'age_hours': r.get('ageHours'),
        'publish_date': r.get('publishDate'),
        'protocol': r.get('protocol') or '',
        'categories': [c.get('name') for c in cats if c.get('name')],
        'category_ids': [c.get('id') for c in cats if c.get('id')],
        'info_url': r.get('infoUrl') or '',
        'download_url': r.get('downloadUrl') or '',
        'magnet_url': r.get('magnetUrl') or '',
        'imdb_id': r.get('imdbId') or 0,
        'tmdb_id': r.get('tmdbId') or 0,
        'tvdb_id': r.get('tvdbId') or 0,
        'indexer_flags': r.get('indexerFlags') or [],
    }


def _arr_indexer_id(kind, name):
    """The id of the indexer Prowlarr synced into Sonarr/Radarr as
    "<name> (Prowlarr)", so a pushed release gets that indexer's seeding
    rules. None if there isn't one."""
    key = f'arr_indexer_names_{kind}'
    names = cache.get(key)
    if names is None:
        names = {i.get('name'): i['id'] for i in client(kind).indexers()}
        cache.set(key, names, 300)
    return names.get(f'{name} (Prowlarr)') or names.get(name)


def push_release_to(kind, release):
    """
    Hand a Prowlarr search result to Sonarr/Radarr. If they want it they
    grab it, track the download and import it into the library themselves.

    Returns {'approved', 'rejections', 'unknown' (the show/movie isn't in
    the library), 'can_force' (turned down, but only for reasons like
    quality or monitoring — it can still be downloaded on request)}.
    """
    payload = {
        'title': release.get('title'),
        'downloadUrl': release.get('download_url') or None,
        'magnetUrl': release.get('magnet_url') or None,
        'protocol': release.get('protocol') or 'torrent',
        'publishDate': release.get('publish_date'),
        'indexer': release.get('indexer') or 'Prowlarr',
        'size': release.get('size') or 0,
        'seeders': release.get('seeders'),
        'leechers': release.get('leechers'),
        'infoUrl': release.get('info_url') or None,
        'guid': release.get('guid'),
    }
    if release.get('indexer'):
        indexer_id = _arr_indexer_id(kind, release['indexer'])
        if indexer_id:
            payload['indexerId'] = indexer_id
    if kind == 'sonarr' and release.get('tvdb_id'):
        payload['tvdbId'] = release['tvdb_id']
    if kind == 'radarr' and release.get('tmdb_id'):
        payload['tmdbId'] = release['tmdb_id']
    result = client(kind).push_release(payload) or []
    if isinstance(result, dict):
        result = [result]
    rejections = []
    for r in result:
        rejections.extend(r.get('rejections') or [])
    approved = bool(any(r.get('approved') for r in result) or (result and not rejections))
    unknown = any(x in ('Unknown Series', 'Unknown Movie') for x in rejections)
    return {
        'approved': approved,
        'rejections': rejections,
        'unknown': unknown,
        'can_force': not approved and not unknown and release.get('protocol', 'torrent') == 'torrent'
                     and any(r.get('downloadAllowed') for r in result),
    }


def send_to_downloader(kind, release):
    """
    "Download anyway": add a release Sonarr/Radarr turned down straight to
    qBittorrent under their category. They pick it up from there, show it
    in their queue and import it when it finishes, like one they grabbed.
    """
    from apps.qbt import client as qbt
    if (release.get('protocol') or 'torrent') != 'torrent':
        raise ArrError('Only torrents can be sent to qBittorrent')
    # Magnet first: indexers that only have magnets answer the download
    # link with a redirect qBittorrent can't follow.
    url = release.get('magnet_url') or release.get('download_url')
    if not url:
        raise ArrError('This result has no download link')
    dc = next((d for d in client(kind).download_clients() if d.get('implementation') == 'QBittorrent'), None)
    category = (_field(dc, _QBT_CATEGORY_FIELD[kind]) if dc else None) or DEFAULT_QBT_CATEGORY[kind]
    try:
        qbt.add_magnet(url, category=category)
    except Exception as e:
        raise ArrError(f'qBittorrent: {e}')
    log.info('send_to_downloader: %r → qBittorrent category %r for %s', release.get('title'), category, kind)
    return category


def identify_release(kind, release):
    """
    Which show/movie a search result is, so one that isn't in the library
    yet can be added first: the ids the indexer sent, else Sonarr/Radarr's
    own title parser + lookup. None if it can't be worked out.
    """
    c = client(kind)
    try:
        if kind == 'sonarr':
            if release.get('tvdb_id'):
                return lookup_item('sonarr', tvdb_id=release['tvdb_id'])
            parsed = c.parse(release.get('title') or '')
            if parsed.get('series'):
                return lookup_item('sonarr', tvdb_id=parsed['series'].get('tvdbId'))
            name = (parsed.get('parsedEpisodeInfo') or {}).get('seriesTitle')
            results = c.lookup(name) if name else []
            return lookup_item('sonarr', tvdb_id=results[0]['tvdbId']) if results and results[0].get('tvdbId') else None

        if release.get('tmdb_id'):
            return lookup_item('radarr', tmdb_id=release['tmdb_id'])
        parsed = c.parse(release.get('title') or '')
        if parsed.get('movie'):
            return lookup_item('radarr', tmdb_id=parsed['movie'].get('tmdbId'))
        info = parsed.get('parsedMovieInfo') or {}
        name = (info.get('movieTitles') or [None])[0] or info.get('movieTitle')
        term = f'{name} {info["year"]}' if name and info.get('year') else name
        results = c.lookup(term) if term else []
        return lookup_item('radarr', tmdb_id=results[0]['tmdbId']) if results and results[0].get('tmdbId') else None
    except ArrError as e:
        log.info('identify_release: %s', e)
        return None