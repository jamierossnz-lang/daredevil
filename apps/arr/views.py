"""
JSON endpoints behind the Prowlarr / Sonarr / Radarr tabs on the Settings
page. Every action goes straight to the app's own API — Daredevil keeps only
the connection details and its add-defaults (ArrService).
"""
import copy
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST

from .clients import ArrError
from .http import json_body as _body, json_error as _err
from .models import ArrService
from .services import (
    _CLIENTS, add_options, client, definition_key, ensure_prowlarr_app, ensure_qbittorrent, ensure_webhook,
    find_definition, fmt_bytes, indexer_form_fields, indexer_schema, apply_form_values, is_daredevil_webhook, prowlarr,
    public_url, webhook_url, _find_daredevil_webhook, _field, DEFAULT_QBT_CATEGORY, SONARR_DEFAULT_MONITOR_CHOICES,
    RADARR_MONITOR_CHOICES, MIN_AVAILABILITY_CHOICES, SERIES_TYPE_CHOICES, lookup_item,
    FLARESOLVERR_TAG, ensure_flaresolverr, flaresolverr_status, is_cloudflare_error, set_indexer_flaresolverr, tag_id,
    use_flaresolverr,
)

log = logging.getLogger('daredevil.arr.views')

_KINDS = ('prowlarr', 'sonarr', 'radarr')
_MEDIA_KINDS = ('sonarr', 'radarr')


def _kind(kind, media_only=False):
    if kind not in (_MEDIA_KINDS if media_only else _KINDS):
        raise Http404
    return kind


def _config_dict(svc):
    return {
        'url': svc.url, 'api_key': svc.api_key, 'public_url': svc.public_url,
        'root_folder': svc.root_folder, 'quality_profile_id': svc.quality_profile_id,
        'monitor': svc.monitor, 'minimum_availability': svc.minimum_availability,
        'series_type': svc.series_type, 'season_folder': svc.season_folder,
        'search_on_add': svc.search_on_add, 'path_mappings': svc.path_mappings,
    }


def _choices(kind):
    if kind == 'sonarr':
        return {'monitor': SONARR_DEFAULT_MONITOR_CHOICES, 'series_type': SERIES_TYPE_CHOICES}
    if kind == 'radarr':
        return {'monitor': RADARR_MONITOR_CHOICES, 'minimum_availability': MIN_AVAILABILITY_CHOICES}
    return {}


def _safe(fn):
    try:
        return fn()
    except ArrError as e:
        return {'_error': str(e)}


# ── Overview (everything a tab needs, in one round-trip) ──────────────────────

def overview(request, kind):
    kind = _kind(kind)
    svc = ArrService.get(kind)
    data = {
        'kind': kind,
        'config': _config_dict(svc),
        'choices': _choices(kind),
        'open_url': public_url(kind, request),
        'status': None,
        'error': None,
    }
    if not svc.is_configured:
        data['error'] = 'Not connected yet — enter the URL and API key below.'
        return JsonResponse(data)

    c = client(kind)
    try:
        st = c.system_status()
        data['status'] = {'version': st.get('version'), 'app': st.get('appName'), 'branch': st.get('branch')}
    except ArrError as e:
        data['error'] = str(e)
        return JsonResponse(data)

    # Independent API calls run in parallel — DB access stays on this thread.
    calls = {'health': c.health, 'download_clients': c.download_clients, 'notifications': c.notifications}
    if kind in _MEDIA_KINDS:
        calls.update(root_folders=c.root_folders, quality_profiles=c.quality_profiles, indexers=c.indexers,
                     naming=c.naming_config, media_management=c.media_management_config, qbt=_qbt_state)
    else:
        calls.update(indexers=c.indexers, indexer_status=c.indexer_status, indexer_stats=c.indexer_stats,
                     applications=c.applications, app_profiles=c.app_profiles, tags=c.tags)
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = {k: pool.submit(_safe, fn) for k, fn in calls.items()}
        results = {k: f.result() for k, f in futures.items()}

    data['health'] = [
        {'type': h.get('type'), 'message': h.get('message'), 'wiki': h.get('wikiUrl')}
        for h in (results['health'] if isinstance(results['health'], list) else [])
    ]
    data['download_clients'] = [
        {'id': d['id'], 'name': d.get('name'), 'implementation': d.get('implementation'), 'enable': d.get('enable'),
         'host': _field(d, 'host'), 'category': _field(d, {'sonarr': 'tvCategory', 'radarr': 'movieCategory'}.get(kind, 'category'))}
        for d in _as_list(results['download_clients'])
    ]
    data['default_qbt_category'] = DEFAULT_QBT_CATEGORY[kind]

    if kind in _MEDIA_KINDS:
        qbt = next((d for d in data['download_clients'] if d['implementation'] == 'QBittorrent'), None)
        data['downloads'] = _downloads_folder((qbt or {}).get('category') or DEFAULT_QBT_CATEGORY[kind], results['qbt'])
        hook = next((n for n in _as_list(results['notifications']) if is_daredevil_webhook(n)), None)
        expected = webhook_url(kind)
        current = _field(hook, 'url') if hook else None
        data['webhook'] = {'connected': bool(hook), 'url': current, 'expected': expected, 'up_to_date': current == expected}
        data['root_folders'] = [
            {'id': f['id'], 'path': f['path'], 'free': fmt_bytes(f.get('freeSpace')), 'accessible': f.get('accessible'),
             'unmapped': len(f.get('unmappedFolders') or []), **_seen_by_daredevil(svc, f['path'])}
            for f in _as_list(results['root_folders'])
        ]
        if data['downloads'].get('effective'):
            data['downloads'].update(_seen_by_daredevil(svc, data['downloads']['effective'], may_not_exist=True))
        data['quality_profiles'] = [{'id': p['id'], 'name': p['name']} for p in _as_list(results['quality_profiles'])]
        data['indexers'] = [
            {'id': i['id'], 'name': i.get('name'), 'protocol': i.get('protocol'),
             'rss': i.get('enableRss'), 'auto': i.get('enableAutomaticSearch'), 'interactive': i.get('enableInteractiveSearch')}
            for i in _as_list(results['indexers'])
        ]
        data['naming'] = results['naming'] if isinstance(results['naming'], dict) else {}
        data['media_management'] = results['media_management'] if isinstance(results['media_management'], dict) else {}
        from .legacy import pending_counts
        data['legacy'] = pending_counts()
    else:
        status = {s.get('indexerId'): s for s in _as_list(results['indexer_status'])}
        stats = {s.get('indexerId'): s for s in _as_list(results['indexer_stats'])}
        fs_tag = next((t['id'] for t in _as_list(results['tags']) if (t.get('label') or '').lower() == FLARESOLVERR_TAG), None)
        data['indexers'] = [_prowlarr_indexer_row(i, status.get(i['id']), stats.get(i['id']), fs_tag) for i in _as_list(results['indexers'])]
        data['applications'] = [
            {'id': a['id'], 'name': a.get('name'), 'implementation': a.get('implementation'), 'sync_level': a.get('syncLevel'),
             'base_url': _field(a, 'baseUrl')}
            for a in _as_list(results['applications'])
        ]
        data['app_profiles'] = [{'id': p['id'], 'name': p['name']} for p in _as_list(results['app_profiles'])]
        data['media_configured'] = {k: ArrService.get(k).is_configured for k in _MEDIA_KINDS}
    return JsonResponse(data)


def _as_list(value):
    return value if isinstance(value, list) else []


def _seen_by_daredevil(svc, remote_path, may_not_exist=False):
    """
    {'local', 'local_ok'}: the path inside Daredevil's container (via the
    path mappings), and whether it's there. A download folder qBittorrent
    hasn't created yet counts if its parent is visible.
    """
    local = svc.to_local(remote_path)
    probe = local
    while probe and probe != '/':
        if os.path.isdir(probe):
            return {'local': local, 'local_ok': True}
        if not may_not_exist:
            break
        probe = os.path.dirname(probe)
    return {'local': local, 'local_ok': False}


def _qbt_state():
    """(categories, preferences) from qBittorrent, or {'_error': …}."""
    try:
        from apps.qbt.client import get_client
        c = get_client()
        return dict(c.torrents_categories()), dict(c.app_preferences())
    except Exception as e:
        return {'_error': f'qBittorrent: {e or type(e).__name__}'}


def _downloads_folder(category, state):
    """Where qBittorrent puts this app's downloads, for the Folders card."""
    from apps.qbt.client import category_folder
    if isinstance(state, dict):
        return {'category': category, 'error': state.get('_error')}
    cats, prefs = state
    cat = cats.get(category) or {}
    return {
        'category': category,
        'save_path': cat.get('savePath') or cat.get('save_path') or '',
        'effective': category_folder(category, cats, prefs),
        'default': prefs.get('save_path'),
        'error': None,
    }


def _prowlarr_indexer_row(i, status, stats, fs_tag=None):
    return {
        'flaresolverr': fs_tag is not None and fs_tag in (i.get('tags') or []),
        'id': i['id'],
        'name': i.get('name'),
        'enable': i.get('enable'),
        'protocol': i.get('protocol'),
        'privacy': i.get('privacy'),
        'priority': i.get('priority'),
        'definition': i.get('definitionName') or i.get('implementation'),
        'language': i.get('language'),
        'app_profile_id': i.get('appProfileId'),
        'disabled_till': (status or {}).get('disabledTill'),
        'most_recent_failure': (status or {}).get('mostRecentFailure'),
        'queries': (stats or {}).get('numberOfQueries', 0),
        'grabs': (stats or {}).get('numberOfGrabs', 0),
        'failed': (stats or {}).get('numberOfFailedQueries', 0),
        'avg_ms': (stats or {}).get('averageResponseTime', 0),
    }


# ── Connection + defaults ─────────────────────────────────────────────────────

@require_POST
def save_config(request, kind):
    kind = _kind(kind)
    svc = ArrService.get(kind)
    data = _body(request)
    for field in ('url', 'api_key', 'public_url', 'root_folder', 'monitor', 'minimum_availability',
                  'series_type', 'path_mappings'):
        if field in data:
            value = str(data[field] or '').strip()
            if field in ('url', 'public_url'):
                value = value.rstrip('/')
            setattr(svc, field, value)
    for field in ('season_folder', 'search_on_add'):
        if field in data:
            setattr(svc, field, bool(data[field]))
    if 'quality_profile_id' in data:
        qp = str(data['quality_profile_id'] or '')
        svc.quality_profile_id = int(qp) if qp.isdigit() else None
    if not svc.url:
        return _err('URL is required')
    svc.save()
    from .services import invalidate_library
    from django.core.cache import cache
    invalidate_library()
    cache.delete_many(['file_browser_tabs', 'storage_paths'])
    try:
        st = client(kind).system_status()
        return JsonResponse({'ok': True, 'connected': True, 'version': st.get('version')})
    except ArrError as e:
        return JsonResponse({'ok': True, 'connected': False, 'error': str(e)})


@require_POST
def test_connection(request, kind):
    """Test a (possibly unsaved) URL + API key from the form."""
    kind = _kind(kind)
    data = _body(request)
    svc = ArrService.get(kind)
    c = _CLIENTS[kind]((data.get('url') or svc.url).strip(), (data.get('api_key') or svc.api_key).strip())
    try:
        st = c.system_status()
        return JsonResponse({'ok': True, 'version': st.get('version'), 'app': st.get('appName')})
    except ArrError as e:
        return _err(e, status=200)


def options(request, kind):
    """Root folders / quality profiles / defaults for the add-show/movie modals."""
    kind = _kind(kind, media_only=True)
    try:
        return JsonResponse({'ok': True, **add_options(kind)})
    except ArrError as e:
        return _err(e, status=200)


def lookup(request, kind):
    """What the Add dialog needs to know first: ?tmdb_id= / ?tvdb_id=."""
    kind = _kind(kind, media_only=True)
    num = lambda k: int(request.GET[k]) if request.GET.get(k, '').isdigit() else None
    try:
        return JsonResponse({'ok': True, **lookup_item(kind, tmdb_id=num('tmdb_id'), tvdb_id=num('tvdb_id'))})
    except ArrError as e:
        return _err(e, status=200)


# ── Quality profiles & sizes ──────────────────────────────────────────────────

def quality(request, kind):
    from . import quality as q
    kind = _kind(kind, media_only=True)
    try:
        return JsonResponse({'ok': True, **q.overview(kind)})
    except ArrError as e:
        return _err(e, status=200)


@require_POST
def quality_profile_save(request, kind, profile_id):
    from . import quality as q
    kind = _kind(kind, media_only=True)
    data = _body(request)
    try:
        q.save_profile(kind, profile_id, data.get('upgrade_allowed'), data.get('cutoff'), data.get('allowed') or {})
        if data.get('definitions'):
            q.save_definitions(kind, data['definitions'])
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


# ── Plex (library refresh on import, Plex Watchlist) ──────────────────────────

def plex_link(request, kind):
    """GET: what's connected. POST {what: refresh|watchlist, enabled, …}: switch it on/off."""
    from . import plex_link as pl
    kind = _kind(kind, media_only=True)
    try:
        if request.method == 'POST':
            data = _body(request)
            what = data.get('what')
            if what not in ('refresh', 'watchlist'):
                return _err('Unknown connection')
            if not data.get('enabled'):
                pl.disconnect(kind, what)
            elif what == 'refresh':
                pl.connect_refresh(kind, data.get('map_from'), data.get('map_to'))
            else:
                pl.connect_watchlist(kind, data.get('monitor'), data.get('search', True))
        return JsonResponse({'ok': True, **pl.status(kind)})
    except ArrError as e:
        return _err(e, status=200 if request.method == 'GET' else 400)


@require_POST
def plex_link_test(request, kind):
    from . import plex_link as pl
    kind = _kind(kind, media_only=True)
    try:
        pl.test_refresh(kind)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


# ── Folder picker ─────────────────────────────────────────────────────────────

def browse_folders(request):
    """Directories at ?path= as seen by ?source= (sonarr/radarr/qbittorrent/daredevil)."""
    from .filesystem import browse
    try:
        return JsonResponse({'ok': True, **browse(request.GET.get('source', 'daredevil'), request.GET.get('path'))})
    except ArrError as e:
        return _err(e, status=200)


# ── Root folders ──────────────────────────────────────────────────────────────

@require_POST
def root_folder_add(request, kind):
    kind = _kind(kind, media_only=True)
    path = (_body(request).get('path') or '').strip()
    if not path:
        return _err('Path is required')
    try:
        folder = client(kind).add_root_folder(path)
        from django.core.cache import cache
        cache.delete('file_browser_tabs')
        return JsonResponse({'ok': True, 'id': folder.get('id')})
    except ArrError as e:
        return _err(e)


@require_POST
def root_folder_delete(request, kind, folder_id):
    kind = _kind(kind, media_only=True)
    try:
        client(kind).delete_root_folder(folder_id)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


# ── Wiring: qBittorrent, webhooks, Prowlarr apps ──────────────────────────────

@require_POST
def connect_qbittorrent(request, kind):
    kind = _kind(kind)
    category = (_body(request).get('category') or '').strip() or None
    try:
        saved = ensure_qbittorrent(kind, category=category)
        return JsonResponse({'ok': True, 'id': saved.get('id')})
    except ArrError as e:
        return _err(e)


@require_POST
def save_downloads(request, kind):
    """
    Where this app's downloads go: the qBittorrent category it uses, and the
    folder qBittorrent stores that category in (empty = qBittorrent's default
    folder). Sonarr/Radarr then move finished files into their root folders.
    """
    kind = _kind(kind, media_only=True)
    data = _body(request)
    category = (data.get('category') or '').strip() or DEFAULT_QBT_CATEGORY[kind]
    save_path = (data.get('save_path') or '').strip()
    try:
        ensure_qbittorrent(kind, category=category)
    except ArrError as e:
        return _err(e)
    from apps.qbt import client as qbt
    try:
        switched_on = qbt.set_category_folder(category, save_path)
        state = _qbt_state()
    except Exception as e:
        return _err(f'{kind.title()} now uses "{category}", but qBittorrent rejected the folder: {e}')
    folder = _downloads_folder(category, state)
    # qBittorrent and Sonarr/Radarr must see the folder at the same path, or
    # finished downloads can't be imported.
    from .filesystem import visible_to
    folder['visible'] = bool(folder.get('effective')) and visible_to(kind, folder['effective'])
    if folder.get('effective'):
        folder.update(_seen_by_daredevil(ArrService.get(kind), folder['effective'], may_not_exist=True))
    from django.core.cache import cache
    cache.delete('file_browser_tabs')
    return JsonResponse({'ok': True, 'downloads': folder, 'category_paths_switched_on': switched_on})


@require_POST
def connect_webhook(request, kind):
    kind = _kind(kind, media_only=True)
    try:
        saved = ensure_webhook(kind)
        return JsonResponse({'ok': True, 'id': saved.get('id'), 'url': webhook_url(kind)})
    except ArrError as e:
        return _err(e)


@require_POST
def test_webhook(request, kind):
    kind = _kind(kind, media_only=True)
    c = client(kind)
    try:
        hook = _find_daredevil_webhook(c)
        if not hook:
            return _err('Webhook not connected yet')
        c.test_notification(hook)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


@require_POST
def prowlarr_connect_apps(request):
    results = {}
    for kind in _MEDIA_KINDS:
        if not ArrService.get(kind).is_configured:
            results[kind] = 'skipped — not configured'
            continue
        try:
            ensure_prowlarr_app(kind)
            results[kind] = 'connected'
        except ArrError as e:
            results[kind] = f'failed — {e}'
    ok = any(v == 'connected' for v in results.values())
    return JsonResponse({'ok': ok, 'results': results})


@require_POST
def prowlarr_sync_apps(request):
    try:
        prowlarr().command('ApplicationIndexerSync')
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


# ── Naming / media management ─────────────────────────────────────────────────

_NAMING_KEYS = {
    'sonarr': {'renameEpisodes', 'standardEpisodeFormat', 'dailyEpisodeFormat', 'animeEpisodeFormat',
               'seriesFolderFormat', 'seasonFolderFormat', 'specialsFolderFormat', 'replaceIllegalCharacters'},
    'radarr': {'renameMovies', 'standardMovieFormat', 'movieFolderFormat', 'replaceIllegalCharacters'},
}
_MM_KEYS = {
    'sonarr': {'copyUsingHardlinks', 'deleteEmptyFolders', 'importExtraFiles', 'extraFileExtensions', 'recycleBin',
               'autoUnmonitorPreviouslyDownloadedEpisodes', 'downloadPropersAndRepacks', 'minimumFreeSpaceWhenImporting'},
    'radarr': {'copyUsingHardlinks', 'deleteEmptyFolders', 'importExtraFiles', 'extraFileExtensions', 'recycleBin',
               'autoUnmonitorPreviouslyDownloadedMovies', 'downloadPropersAndRepacks', 'minimumFreeSpaceWhenImporting'},
}


@require_POST
def save_naming(request, kind):
    kind = _kind(kind, media_only=True)
    data = _body(request)
    c = client(kind)
    try:
        naming = c.naming_config()
        naming.update({k: v for k, v in data.get('naming', {}).items() if k in _NAMING_KEYS[kind]})
        c.update_naming_config(naming)
        mm = c.media_management_config()
        mm.update({k: v for k, v in data.get('media_management', {}).items() if k in _MM_KEYS[kind]})
        c.update_media_management_config(mm)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


# ── Prowlarr indexers ─────────────────────────────────────────────────────────

def prowlarr_catalogue(request):
    """Every indexer Prowlarr can add, slimmed down for the picker."""
    refresh = request.GET.get('refresh') == '1'
    try:
        schema = indexer_schema(refresh=refresh)
    except ArrError as e:
        return _err(e, status=200)
    items = sorted((
        {
            'key': definition_key(d),
            'name': d.get('name') or '',
            'description': d.get('description') or '',
            'language': d.get('language') or '',
            'privacy': d.get('privacy') or '',
            'protocol': d.get('protocol') or '',
            # Top-level Newznab categories (2000 Movies, 5000 TV, …) for the category filter
            'categories': sorted({c['id'] for c in (d.get('capabilities') or {}).get('categories') or []
                                  if c.get('id', 0) < 10000 and c['id'] % 1000 == 0}),
        }
        for d in schema
    ), key=lambda x: x['name'].lower())
    return JsonResponse({'ok': True, 'items': items})


def _form_payload(definition, app_profiles, fs_tag=None):
    return {
        'flaresolverr': fs_tag is not None and fs_tag in (definition.get('tags') or []),
        'ok': True,
        'id': definition.get('id'),
        'key': definition_key(definition),
        'name': definition.get('name'),
        'description': definition.get('description') or '',
        'privacy': definition.get('privacy'),
        'protocol': definition.get('protocol'),
        'enable': definition.get('enable', True),
        'priority': definition.get('priority', 25),
        'app_profile_id': definition.get('appProfileId') or (app_profiles[0]['id'] if app_profiles else None),
        'app_profiles': app_profiles,
        'needs_captcha': any(f.get('type') == 'cardigannCaptcha' for f in definition.get('fields', [])),
        'fields': [f for f in indexer_form_fields(definition) if f['type'] != 'cardigannCaptcha'],
    }


def prowlarr_definition_form(request):
    key = request.GET.get('key', '')
    try:
        definition = find_definition(key)
        if not definition:
            return _err('Unknown indexer type — refresh the list and try again', status=200)
        c = prowlarr()
        profiles = [{'id': p['id'], 'name': p['name']} for p in c.app_profiles()]
        return JsonResponse(_form_payload(definition, profiles, tag_id(c, FLARESOLVERR_TAG)))
    except ArrError as e:
        return _err(e, status=200)


def prowlarr_indexer_form(request, indexer_id):
    try:
        c = prowlarr()
        definition = c.indexer(indexer_id)
        profiles = [{'id': p['id'], 'name': p['name']} for p in c.app_profiles()]
        return JsonResponse(_form_payload(definition, profiles, tag_id(c, FLARESOLVERR_TAG)))
    except ArrError as e:
        return _err(e, status=200)


def _build_indexer_payload(c, data):
    if data.get('id'):
        definition = c.indexer(int(data['id']))
    else:
        found = find_definition(data.get('key', ''))
        if not found:
            raise ArrError('Unknown indexer type')
        definition = copy.deepcopy(found)
    if data.get('name'):
        definition['name'] = data['name'].strip()
    definition['enable'] = bool(data.get('enable', True))
    if str(data.get('priority', '')).isdigit():
        definition['priority'] = int(data['priority'])
    if data.get('app_profile_id'):
        definition['appProfileId'] = int(data['app_profile_id'])
    elif not definition.get('appProfileId'):
        profiles = c.app_profiles()
        definition['appProfileId'] = profiles[0]['id'] if profiles else 1
    apply_form_values(definition, data.get('values') or {})
    if 'flaresolverr' in data:
        use_flaresolverr(c, definition, bool(data['flaresolverr']))
    return definition


@require_POST
def prowlarr_indexer_save(request):
    """
    Prowlarr tests an enabled indexer before saving and refuses one that
    fails (forceSave only skips warnings). `save_disabled` is the way round
    that, same as in Prowlarr's own UI: store it disabled — no test — and
    enable it later once the site is reachable.
    """
    data = _body(request)
    c = prowlarr()
    save_disabled = bool(data.get('save_disabled'))
    try:
        payload = _build_indexer_payload(c, data)
        if save_disabled:
            payload['enable'] = False
        # forceSave skips Prowlarr's test only when we know it'll be stored off
        saved = (c.update_indexer(payload, force=save_disabled) if payload.get('id')
                 else c.add_indexer(payload, force=save_disabled))
        return JsonResponse({'ok': True, 'id': saved.get('id'), 'disabled': not saved.get('enable', True)})
    except ArrError as e:
        return _err(e, status=200, can_save_disabled=not save_disabled and bool(data.get('enable', True)),
                    cloudflare=is_cloudflare_error(e))


@require_POST
def prowlarr_indexer_test(request):
    data = _body(request)
    c = prowlarr()
    try:
        c.test_indexer(_build_indexer_payload(c, data))
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e, status=200, cloudflare=is_cloudflare_error(e))


@require_POST
def prowlarr_indexer_test_existing(request, indexer_id):
    c = prowlarr()
    try:
        c.test_indexer(c.indexer(indexer_id))
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e, status=200, cloudflare=is_cloudflare_error(e))


@require_POST
def prowlarr_indexer_toggle(request, indexer_id):
    """Switching on runs Prowlarr's test (so a broken indexer isn't silently
    re-enabled); switching off never needs one."""
    c = prowlarr()
    try:
        definition = c.indexer(indexer_id)
        definition['enable'] = not definition.get('enable')
        c.update_indexer(definition, force=not definition['enable'])
        return JsonResponse({'ok': True, 'enable': definition['enable']})
    except ArrError as e:
        return _err(e, status=200)


@require_POST
def prowlarr_indexer_delete(request, indexer_id):
    try:
        prowlarr().delete_indexer(indexer_id)
        return JsonResponse({'ok': True})
    except ArrError as e:
        return _err(e)


@require_POST
def prowlarr_indexer_test_all(request):
    try:
        results = prowlarr().test_all_indexers()
        failed = [r for r in results if not r.get('isValid')]
        return JsonResponse({
            'ok': True, 'tested': len(results), 'failed': len(failed),
            'failures': {r.get('id'): '; '.join(v.get('errorMessage', '') for v in r.get('validationFailures') or []) for r in failed},
        })
    except ArrError as e:
        # testall returns 400 with the per-indexer results when any fail
        if isinstance(e.payload, list):
            failed = [r for r in e.payload if not r.get('isValid')]
            return JsonResponse({
                'ok': True, 'tested': len(e.payload), 'failed': len(failed),
                'failures': {r.get('id'): '; '.join(v.get('errorMessage', '') for v in r.get('validationFailures') or []) for r in failed},
            })
        return _err(e)


# ── FlareSolverr ──────────────────────────────────────────────────────────────

def prowlarr_flaresolverr(request):
    """GET: is FlareSolverr up, and which indexers use it. POST {url}: save
    its address and connect it to Prowlarr as an indexer proxy."""
    if request.method == 'POST':
        from config import env_file
        url = (_body(request).get('url') or '').strip().rstrip('/')
        if url:
            env_file.update({'FLARESOLVERR_URL': url})
        try:
            ensure_flaresolverr(url or None)
        except ArrError as e:
            return _err(e, status=200, **flaresolverr_status())
    return JsonResponse({'ok': True, **flaresolverr_status()})


@require_POST
def prowlarr_indexer_flaresolverr(request, indexer_id):
    """Send one indexer through FlareSolverr — or stop: {enabled}."""
    enabled = bool(_body(request).get('enabled', True))
    try:
        set_indexer_flaresolverr(indexer_id, enabled)
        return JsonResponse({'ok': True, 'flaresolverr': enabled})
    except ArrError as e:
        return _err(e, status=200, cloudflare=is_cloudflare_error(e))


# ── Legacy library import ─────────────────────────────────────────────────────

@require_POST
def import_library(request):
    from .tasks import import_library_to_arr
    try:
        result = import_library_to_arr.delay()
        return JsonResponse({'ok': True, 'task_id': result.id})
    except Exception as e:
        return _err(e, status=500)
