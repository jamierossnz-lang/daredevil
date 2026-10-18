"""
Plex ↔ Sonarr / Radarr, switched on and off from their settings tabs.

* Library refresh — a "Plex Media Server" connection in Sonarr/Radarr, so
  Plex scans the right folder as soon as something is imported, upgraded,
  renamed or deleted, instead of waiting for its own periodic scan.
* Watchlist — Sonarr/Radarr's "Plex Watchlist" import list: whatever you
  add to your Plex Watchlist (from any Plex app) is added and searched for.

Both use the Plex address and token from App Settings → General. Prowlarr
has nothing to connect to Plex — it only deals with indexers.
"""
from urllib.parse import urlparse

from .clients import ArrError
from .models import ArrService
from .services import _field, _set_fields, client

# Where the stock docker-compose mounts TV_DIR / MOVIES_DIR inside Plex
DEFAULT_PLEX_PATHS = {'sonarr': '/data/tv', 'radarr': '/data/movies'}

# Everything that changes files on disk, so Plex rescans for each of them
_REFRESH_EVENTS = {
    'sonarr': ['onDownload', 'onUpgrade', 'onImportComplete', 'onRename', 'onSeriesDelete',
               'onEpisodeFileDelete', 'onEpisodeFileDeleteForUpgrade'],
    'radarr': ['onDownload', 'onUpgrade', 'onRename', 'onMovieDelete', 'onMovieFileDelete', 'onMovieFileDeleteForUpgrade'],
}


def _plex():
    from apps.plex.client import plex_credentials
    return plex_credentials()


def _plex_target():
    url, token = _plex()
    if not url or not token:
        raise ArrError('Add the Plex URL and token in App Settings → General first')
    u = urlparse(url)
    return {'host': u.hostname, 'port': u.port or (443 if u.scheme == 'https' else 32400),
            'useSsl': u.scheme == 'https', 'urlBase': u.path.rstrip('/') or None, 'token': token}


def _find(items, implementation):
    return next((x for x in items if x.get('implementation') == implementation), None)


def _library_folder(kind):
    svc = ArrService.get(kind)
    if svc.root_folder:
        return svc.root_folder
    folders = client(kind).root_folders()
    return folders[0]['path'] if folders else ''


def status(kind):
    c = client(kind)
    note = _find(c.notifications(), 'PlexServer')
    lst = _find(c.import_lists(), 'PlexImport')
    url, token = _plex()
    if kind == 'sonarr':
        watch_on = bool(lst and lst.get('enableAutomaticAdd'))
        monitor, search = (lst or {}).get('shouldMonitor'), (lst or {}).get('searchForMissingEpisodes')
    else:
        watch_on = bool(lst and lst.get('enabled') and lst.get('enableAuto'))
        monitor, search = (lst or {}).get('monitor'), (lst or {}).get('searchOnAdd')
    return {
        'plex_configured': bool(url and token),
        'plex_url': url,
        'refresh': {
            'connected': bool(note),
            'map_from': _field(note, 'mapFrom') if note else _library_folder(kind),
            'map_to': _field(note, 'mapTo') if note else DEFAULT_PLEX_PATHS[kind],
        },
        'watchlist': {
            'connected': watch_on,
            'monitor': monitor or ('all' if kind == 'sonarr' else 'movieOnly'),
            'search': True if search is None else bool(search),
        },
    }


def connect_refresh(kind, map_from, map_to):
    c = client(kind)
    target = _plex_target()
    note = _find(c.notifications(), 'PlexServer')
    payload = note or next(s for s in c.notification_schema() if s.get('implementation') == 'PlexServer')
    payload['name'] = payload.get('name') or 'Plex'
    _set_fields(payload, {
        'host': target['host'], 'port': target['port'], 'useSsl': target['useSsl'], 'urlBase': target['urlBase'],
        'authToken': target['token'], 'updateLibrary': True,
        'mapFrom': (map_from or '').strip() or None, 'mapTo': (map_to or '').strip() or None,
    })
    for event in _REFRESH_EVENTS[kind]:
        if event in payload:
            payload[event] = True
    # Sonarr/Radarr test the connection before saving — an unreachable Plex comes back as an error
    return c.update_notification(payload) if note else c.add_notification(payload)


def test_refresh(kind):
    c = client(kind)
    note = _find(c.notifications(), 'PlexServer')
    if not note:
        raise ArrError('Not connected yet')
    c.test_notification(note)


def connect_watchlist(kind, monitor, search):
    c = client(kind)
    target = _plex_target()
    svc = ArrService.get(kind)
    lst = _find(c.import_lists(), 'PlexImport')
    payload = lst or next(s for s in c.import_list_schema() if s.get('implementation') == 'PlexImport')
    payload['name'] = payload.get('name') or 'Plex Watchlist'
    _set_fields(payload, {'accessToken': target['token']})
    profile = svc.quality_profile_id or next((p['id'] for p in c.quality_profiles()), None)
    root = _library_folder(kind)
    if not root:
        raise ArrError(f'{kind.title()} has no root folder yet — add one under Folders')
    payload.update(qualityProfileId=int(profile), rootFolderPath=root)
    if kind == 'sonarr':
        payload.update(enableAutomaticAdd=True, shouldMonitor=monitor or 'all', searchForMissingEpisodes=bool(search),
                       monitorNewItems='all', seriesType=svc.series_type or 'standard', seasonFolder=svc.season_folder)
    else:
        payload.update(enabled=True, enableAuto=True, monitor=monitor or 'movieOnly', searchOnAdd=bool(search),
                       minimumAvailability=svc.minimum_availability or 'released')
    return c.update_import_list(payload) if lst else c.add_import_list(payload)


def disconnect(kind, what):
    c = client(kind)
    if what == 'refresh':
        note = _find(c.notifications(), 'PlexServer')
        if note:
            c.delete_notification(note['id'])
    else:
        lst = _find(c.import_lists(), 'PlexImport')
        if lst:
            c.delete_import_list(lst['id'])
