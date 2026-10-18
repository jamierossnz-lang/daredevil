"""
TV shows — Daredevil is the interface, Sonarr is the library.

Every show, season and episode shown here is read live from Sonarr, and
every action (add, monitor on/off, download, pick a release, retry, delete
a file, remove the show) is sent straight back to Sonarr's API. Sonarr does
the monitoring, searching, grabbing (via qBittorrent) and importing.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.arr.clients import ArrError
from apps.arr.services import (
    FAVOURITE_TAG, FOR_YOU_TAG, SONARR_MONITOR_CHOICES, add_series, apply_series_monitoring, delete_file,
    download_episodes, download_season, download_series, favourite_tag_id, fmt_bytes, grab_release, image_url, invalidate_library,
    is_configured, library_index, normalize_release, parse_dt, public_url, refresh_series, search_monitored,
    set_episodes_monitored, set_season_monitored, set_series_monitored, sonarr, tag_ids, toggle_favourite,
    update_media_fields, wait_for_episodes,
)

log = logging.getLogger('daredevil.shows')

STATUS_FILTERS = [
    ('continuing', 'Continuing'),
    ('ended', 'Ended'),
    ('upcoming', 'Upcoming'),
    ('missing', 'Missing episodes'),
    ('unmonitored', 'Unmonitored'),
    ('favourite', 'Favourites'),
]


def _body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


def _fail(e, status=400):
    return JsonResponse({'ok': False, 'error': str(e)}, status=status)


def _not_configured(request, template='media_tracker/arr_not_configured.html'):
    return render(request, template, {'app': 'Sonarr', 'kind': 'sonarr'})


# ── View-model helpers ────────────────────────────────────────────────────────

def _series_card(s, fav_tag=None, for_you_tag=None):
    stats = s.get('statistics') or {}
    files = stats.get('episodeFileCount', 0)
    wanted = stats.get('episodeCount', 0)
    return {
        'id': s['id'],
        'title': s.get('title') or '',
        'sort_title': s.get('sortTitle') or (s.get('title') or '').lower(),
        'year': s.get('year') or '',
        'status': s.get('status') or '',
        'network': s.get('network') or '',
        'poster': image_url(s.get('images'), 'poster'),
        'monitored': bool(s.get('monitored')),
        'favourite': fav_tag is not None and fav_tag in (s.get('tags') or []),
        'for_you': for_you_tag is not None and for_you_tag in (s.get('tags') or []),
        'added': parse_dt(s.get('added')),
        'files': files,
        'wanted': wanted,
        'total': stats.get('totalEpisodeCount', 0),
        'missing': max(0, wanted - files),
        'percent': round(stats.get('percentOfEpisodes') or 0),
        'size': fmt_bytes(stats.get('sizeOnDisk')),
        'season_count': stats.get('seasonCount') or len([x for x in s.get('seasons', []) if x['seasonNumber'] > 0]),
        'next_airing': parse_dt(s.get('nextAiring')),
        'tmdb_id': s.get('tmdbId'),
        'tvdb_id': s.get('tvdbId'),
        'title_slug': s.get('titleSlug') or '',
    }


def _queue_entry(q):
    size = q.get('size') or 0
    sizeleft = q.get('sizeleft') or 0
    state = q.get('trackedDownloadState') or ''
    status = (q.get('status') or '').lower()
    if state in ('importPending', 'importing'):
        label = 'Importing'
    elif q.get('trackedDownloadStatus') in ('warning', 'error') or status in ('warning', 'failed'):
        label = 'Needs attention'
    elif status == 'paused':
        label = 'Paused'
    elif status in ('queued', 'delay'):
        label = 'Queued'
    else:
        label = 'Downloading'
    return {
        'queue_id': q.get('id'),
        'progress': round((size - sizeleft) * 100 / size, 1) if size else 0,
        'label': label,
        'timeleft': q.get('timeleft') or '',
        'release': q.get('title') or '',
        'quality': ((q.get('quality') or {}).get('quality') or {}).get('name', ''),
        'warning': label == 'Needs attention',
    }


def _episode_row(ep, queue_by_ep, now):
    air = parse_dt(ep.get('airDateUtc'))
    has_file = bool(ep.get('hasFile'))
    queued = queue_by_ep.get(ep['id'])
    aired = bool(air and air <= now)
    if has_file:
        state = 'downloaded'
    elif queued:
        state = 'downloading'
    elif not air:
        state = 'tba'
    elif not aired:
        state = 'unaired'
    elif ep.get('monitored'):
        state = 'missing'
    else:
        state = 'unmonitored'
    f = ep.get('episodeFile') or {}
    return {
        'id': ep['id'],
        'season': ep.get('seasonNumber'),
        'number': ep.get('episodeNumber'),
        'title': ep.get('title') or 'TBA',
        'overview': ep.get('overview') or '',
        'air': timezone.localtime(air) if air else None,
        'runtime': ep.get('runtime') or None,
        'monitored': bool(ep.get('monitored')),
        'has_file': has_file,
        'file_id': ep.get('episodeFileId') or None,
        'quality': ((f.get('quality') or {}).get('quality') or {}).get('name', ''),
        'size': fmt_bytes(f.get('size')) if f else '',
        'still': image_url(ep.get('images'), 'screenshot', size=None),
        'state': state,
        'queue': queued,
        'aired': aired,
    }


def _build_detail(series_id):
    c = sonarr()
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_series = pool.submit(c.series, series_id)
        f_eps = pool.submit(c.episodes, series_id, True, True)
        f_queue = pool.submit(c.queue_details, series_id)
        f_profiles = pool.submit(c.quality_profiles)
        f_fav = pool.submit(favourite_tag_id, c)
        series = f_series.result()
        episodes = f_eps.result()
        queue = _quiet(f_queue)
        profiles = _quiet(f_profiles)
        fav_tag = _quiet(f_fav, None)

    queue_by_ep = {}
    for q in queue or []:
        if q.get('episodeId'):
            queue_by_ep[q['episodeId']] = _queue_entry(q)

    now = timezone.now()
    seasons = {}
    for s in series.get('seasons', []):
        stats = s.get('statistics') or {}
        seasons[s['seasonNumber']] = {
            'number': s['seasonNumber'],
            'label': 'Specials' if s['seasonNumber'] == 0 else f'Season {s["seasonNumber"]}',
            'monitored': bool(s.get('monitored')),
            'files': stats.get('episodeFileCount', 0),
            'wanted': stats.get('episodeCount', 0),
            'total': stats.get('totalEpisodeCount', 0),
            'size': fmt_bytes(stats.get('sizeOnDisk')),
            'episodes': [],
        }
    for ep in sorted(episodes, key=lambda e: (e.get('seasonNumber', 0), e.get('episodeNumber', 0))):
        season = seasons.setdefault(ep.get('seasonNumber', 0), {
            'number': ep.get('seasonNumber', 0), 'label': f'Season {ep.get("seasonNumber", 0)}', 'monitored': False,
            'files': 0, 'wanted': 0, 'total': 0, 'size': '—', 'episodes': [],
        })
        season['episodes'].append(_episode_row(ep, queue_by_ep, now))
    for season in seasons.values():
        season['downloading'] = sum(1 for e in season['episodes'] if e['state'] == 'downloading')
        season['missing'] = sum(1 for e in season['episodes'] if e['state'] == 'missing')
        season['complete'] = season['total'] > 0 and season['files'] >= season['total']

    ordered = [seasons[n] for n in sorted(n for n in seasons if n > 0)]
    if 0 in seasons and seasons[0]['episodes']:
        ordered.append(seasons[0])

    card = _series_card(series, fav_tag)
    ratings = series.get('ratings') or {}
    card.update({
        'overview': series.get('overview') or '',
        'fanart': image_url(series.get('images'), 'fanart', size='w1280'),
        'genres': ', '.join(series.get('genres') or []),
        'rating': ratings.get('value') or 0,
        'runtime': series.get('runtime') or None,
        'certification': series.get('certification') or '',
        'path': series.get('path') or '',
        'quality_profile_id': series.get('qualityProfileId'),
        'series_type': series.get('seriesType'),
        'monitor_new_items': series.get('monitorNewItems'),
        'imdb_id': series.get('imdbId') or '',
        'first_aired': parse_dt(series.get('firstAired')),
        'ended': bool(series.get('ended')),
    })
    return card, ordered, profiles or [], queue_by_ep


_UNSET = object()


def _quiet(future, default=_UNSET):
    try:
        return future.result()
    except ArrError as e:
        log.info('show detail: optional call failed — %s', e)
        return [] if default is _UNSET else default


# ── Pages ─────────────────────────────────────────────────────────────────────

def tv_shows(request):
    if not is_configured('sonarr'):
        return _not_configured(request)
    error = None
    shows = []
    try:
        c = sonarr()
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_series, f_tags = pool.submit(c.series_list), pool.submit(c.tags)
            series, tags = f_series.result(), {(t.get('label') or '').lower(): t['id'] for t in f_tags.result()}
        shows = sorted((_series_card(s, tags.get(FAVOURITE_TAG), tags.get(FOR_YOU_TAG)) for s in series),
                       key=lambda s: s['sort_title'])
    except ArrError as e:
        error = str(e)
    return render(request, 'media_tracker/tv_shows.html', {
        'shows': shows,
        'error': error,
        'q': request.GET.get('q', '').strip(),
        'status_filter': request.GET.get('status', ''),
        'status_choices': STATUS_FILTERS,
        'sonarr_url': public_url('sonarr', request),
    })


def shows_watching(request):
    """
    The TV library's "Watching" tab: shows played in Plex in the last 60
    days, newest first, as Sonarr series ids. Loaded only when the tab is
    opened (Plex can be slow to answer) and cached for five minutes.
    """
    from django.core.cache import cache
    items = cache.get('plex_watching_shows')
    if items is None:
        from django.conf import settings
        from apps.plex.client import extract_tmdb_id, get_plex
        plex = get_plex()
        if not plex:
            return JsonResponse({'ok': False, 'error': 'Plex is not connected — set it up in App Settings.'})
        cutoff = timezone.now().replace(tzinfo=None) - timedelta(days=60)
        items = []
        try:
            section = plex.library.section(settings.PLEX_TV_SECTION)
            for show in section.search(libtype='show', sort='lastViewedAt:desc', maxresults=60):
                if not show.lastViewedAt or show.lastViewedAt < cutoff:
                    break
                items.append({'title': show.title, 'tmdb_id': extract_tmdb_id(show),
                              'last_viewed': int(show.lastViewedAt.timestamp()),
                              'watched': show.viewedLeafCount or 0, 'total': show.leafCount or 0})
        except Exception as e:
            return JsonResponse({'ok': False, 'error': f'Plex: {e}'})
        cache.set('plex_watching_shows', items, 300)

    index = library_index('sonarr')
    by_title = None
    out = []
    for item in items:
        series_id = index.get(int(item['tmdb_id'])) if str(item.get('tmdb_id') or '').isdigit() else None
        if not series_id:
            if by_title is None:
                by_title = {(x.get('title') or '').lower(): x['id'] for x in sonarr().series_list()}
            series_id = by_title.get(item['title'].lower())
        if series_id:
            out.append({**item, 'series_id': series_id})
    return JsonResponse({'ok': True, 'items': out})


def show_detail(request, series_id):
    if not is_configured('sonarr'):
        return _not_configured(request)
    try:
        show, seasons, profiles, _ = _build_detail(series_id)
    except ArrError as e:
        if e.status == 404:
            raise Http404('Show not found in Sonarr')
        return render(request, 'media_tracker/arr_error.html', {'app': 'Sonarr', 'error': str(e)})
    return render(request, 'media_tracker/tv_show_detail.html', {
        'show': show,
        'seasons': seasons,
        'quality_profiles': profiles,
        'monitor_choices': SONARR_MONITOR_CHOICES,
        'sonarr_link': f'{public_url("sonarr", request)}/series/{show["title_slug"]}',
    })


def show_by_tmdb(request, tmdb_id):
    """Cross-link helper for TMDB-based pages (Plex, dashboard, For You)."""
    series_id = library_index('sonarr').get(tmdb_id)
    if series_id:
        return redirect('tv_show_detail', series_id=series_id)
    return redirect(f'/shows/search/?tmdb={tmdb_id}')


def show_search(request):
    """Find a show to add — Sonarr's own lookup (TheTVDB), so every result
    is something Sonarr can actually add."""
    if not is_configured('sonarr'):
        return _not_configured(request)
    query = request.GET.get('q', '').strip()
    tmdb_id = request.GET.get('tmdb', '').strip()
    results, error = [], None
    if tmdb_id.isdigit() and not query:
        query = f'tmdb:{tmdb_id}'
    if query:
        try:
            for r in sonarr().lookup(query)[:24]:
                results.append({
                    'tvdb_id': r.get('tvdbId'),
                    'tmdb_id': r.get('tmdbId'),
                    'title': r.get('title'),
                    'year': r.get('year') or '',
                    'network': r.get('network') or '',
                    'status': r.get('status') or '',
                    'overview': r.get('overview') or '',
                    'poster': r.get('remotePoster') or image_url(r.get('images'), 'poster'),
                    'seasons': len([s for s in r.get('seasons', []) if s.get('seasonNumber', 0) > 0]),
                    'rating': (r.get('ratings') or {}).get('value') or 0,
                    'in_library': bool(r.get('id')),
                    'series_id': r.get('id'),
                })
        except ArrError as e:
            error = str(e)
    context = {'results': results, 'query': '' if query.startswith('tmdb:') else query, 'error': error}
    if request.headers.get('HX-Request'):
        return render(request, 'media_tracker/_tv_search_results.html', context)
    return render(request, 'media_tracker/tmdb_search_tv.html', context)


# ── Adding ────────────────────────────────────────────────────────────────────

@require_POST
def show_add(request):
    """Add to Sonarr. Accepts form-encoded or JSON; `tvdb_id` (from the
    search page) or `tmdb_id` (every TMDB-based entry point)."""
    data = _body(request) if request.content_type == 'application/json' else request.POST
    tvdb_id = str(data.get('tvdb_id') or '').strip()
    tmdb_id = str(data.get('tmdb_id') or '').strip()

    def _opt(name):
        v = data.get(name)
        return None if v in (None, '') else v

    search = _opt('search')
    season_folder = _opt('season_folder')
    labels = data.get('tags') if isinstance(data.get('tags'), list) else []
    try:
        series, created = add_series(
            tvdb_id=int(tvdb_id) if tvdb_id.isdigit() else None,
            tmdb_id=int(tmdb_id) if tmdb_id.isdigit() else None,
            quality_profile_id=_opt('quality_profile_id'),
            root_folder=_opt('root_folder'),
            monitor=_opt('monitor'),
            series_type=_opt('series_type'),
            season_folder=None if season_folder is None else str(season_folder).lower() in ('1', 'true', 'on'),
            search=None if search is None else str(search).lower() in ('1', 'true', 'on'),
            tags=tag_ids('sonarr', labels) if labels else None,
        )
    except ArrError as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({
        'status': 'added' if created else 'exists',
        'pk': series.get('id'),
        'id': series.get('id'),
        'name': series.get('title'),
        'title': series.get('title'),
        'url': f'/shows/{series.get("id")}/',
        'seasons': sorted(x['seasonNumber'] for x in series.get('seasons') or [] if x.get('seasonNumber', 0) > 0),
    })


# ── Monitoring ────────────────────────────────────────────────────────────────

@require_POST
def show_monitor(request, series_id):
    data = _body(request)
    try:
        if 'monitored' in data:
            monitored = bool(data['monitored'])
        else:
            monitored = not sonarr().series(series_id).get('monitored')
        set_series_monitored(series_id, monitored)
        return JsonResponse({'ok': True, 'monitored': monitored, 'monitor_new_episodes': monitored})
    except ArrError as e:
        return _fail(e)


@require_POST
def show_monitoring(request, series_id):
    """Apply a Sonarr monitoring preset (e.g. 'future' = only new episodes)."""
    monitor = _body(request).get('monitor', '')
    if monitor not in dict(SONARR_MONITOR_CHOICES):
        return _fail('Unknown monitoring option')
    try:
        apply_series_monitoring(series_id, monitor)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


@require_POST
def season_monitor(request, series_id, season_number):
    data = _body(request)
    try:
        set_season_monitored(series_id, season_number, bool(data.get('monitored', True)))
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


@require_POST
def episodes_monitor(request):
    data = _body(request)
    ids = [int(i) for i in data.get('episode_ids', []) if str(i).isdigit()]
    if not ids:
        return _fail('No episodes given')
    try:
        set_episodes_monitored(ids, bool(data.get('monitored', True)))
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


# ── Downloading / searching ───────────────────────────────────────────────────

@require_POST
def show_download(request, series_id):
    """
    scope = series   → whole show (monitor every season + search)
          = seasons  → `seasons: [1, 2]` (monitor + season search each)
          = episodes → `episode_ids: [...]` (monitor + episode search)
          = monitored → search whatever's already monitored and missing
    Sonarr does the actual search/grab/import; this only issues the commands.
    """
    data = _body(request)
    scope = data.get('scope', 'series')
    try:
        if scope == 'series':
            download_series(series_id)
            msg = 'Searching for every episode'
        elif scope == 'seasons':
            seasons = [int(s) for s in data.get('seasons', []) if str(s).lstrip('-').isdigit()]
            if not seasons:
                return _fail('Pick at least one season')
            for n in seasons:
                download_season(series_id, n)
            msg = f'Searching {len(seasons)} season{"s" if len(seasons) != 1 else ""}'
        elif scope == 'episodes':
            ids = [int(i) for i in data.get('episode_ids', []) if str(i).isdigit()]
            if not ids:
                return _fail('No episodes given')
            download_episodes(ids)
            msg = f'Searching {len(ids)} episode{"s" if len(ids) != 1 else ""}'
        elif scope == 'monitored':
            search_monitored(series_id)
            msg = 'Searching all monitored missing episodes'
        else:
            return _fail('Unknown scope')
        return JsonResponse({'ok': True, 'message': f'{msg} — Sonarr will grab the best release it finds'})
    except ArrError as e:
        return _fail(e)


@require_POST
def show_refresh(request, series_id):
    try:
        refresh_series(series_id)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


def show_releases(request, series_id):
    """Interactive search: ?season=N for season packs, ?episode=ID for one episode."""
    params = {}
    if request.GET.get('episode', '').isdigit():
        params['episodeId'] = int(request.GET['episode'])
    elif request.GET.get('season', '').lstrip('-').isdigit():
        params.update(seriesId=series_id, seasonNumber=int(request.GET['season']))
    else:
        return _fail('Pass ?season= or ?episode=')
    try:
        # Straight after "Add & pick release" Sonarr may still be fetching the episode list
        if request.GET.get('wait') and not wait_for_episodes(series_id):
            return _fail("Sonarr is still loading this show's episodes — try again in a moment.", status=200)
        releases = [normalize_release(r) for r in sonarr().releases(**params)]
        return JsonResponse({'ok': True, 'releases': releases})
    except ArrError as e:
        return _fail(e, status=200)


@require_POST
def release_grab(request):
    data = _body(request)
    if not data.get('guid') or not str(data.get('indexer_id', '')).isdigit():
        return _fail('Missing release')
    try:
        grab_release('sonarr', data['guid'], data['indexer_id'])
        return JsonResponse({'ok': True})
    except ArrError as e:
        msg = str(e)
        if e.status == 404:
            msg = 'Sonarr no longer has this release cached — search again.'
        return _fail(msg)


@require_POST
def episode_file_delete(request, file_id):
    """'Reset' — delete the file so the episode is missing again; Sonarr
    re-downloads it automatically if it's monitored."""
    data = _body(request)
    try:
        delete_file('sonarr', file_id)
        if data.get('search') and data.get('episode_id'):
            download_episodes([data['episode_id']])
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


# ── Editing / removing ────────────────────────────────────────────────────────

@require_POST
def show_edit(request, series_id):
    data = _body(request)
    fields = {}
    if str(data.get('quality_profile_id', '')).isdigit():
        fields['qualityProfileId'] = int(data['quality_profile_id'])
    if data.get('series_type') in ('standard', 'daily', 'anime'):
        fields['seriesType'] = data['series_type']
    if data.get('monitor_new_items') in ('all', 'none'):
        fields['monitorNewItems'] = data['monitor_new_items']
    if not fields:
        return _fail('Nothing to change')
    try:
        update_media_fields('sonarr', series_id, **fields)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


@require_POST
def show_favourite(request, series_id):
    try:
        return JsonResponse({'ok': True, 'is_favourite': toggle_favourite('sonarr', series_id)})
    except ArrError as e:
        return _fail(e)


@require_POST
def show_delete(request, series_id):
    data = _body(request) if request.content_type == 'application/json' else request.POST
    delete_files = str(data.get('delete_files', '')).lower() in ('1', 'true', 'on')
    try:
        sonarr().delete_series(series_id, delete_files=delete_files)
        invalidate_library('sonarr')
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _fail(e)


def show_status(request, series_id):
    """Live poll for the detail page — what's downloading right now and
    which episodes have files, so badges update without a reload."""
    try:
        c = sonarr()
        queue = c.queue_details(series_id)
        has_file = [e['id'] for e in c.episodes(series_id) if e.get('hasFile')]
    except ArrError as e:
        return _fail(e, status=200)
    return JsonResponse({
        'ok': True,
        'queue': {q['episodeId']: _queue_entry(q) for q in queue if q.get('episodeId')},
        'has_file': has_file,
    })
