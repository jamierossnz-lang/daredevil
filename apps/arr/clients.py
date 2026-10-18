"""
Thin HTTP clients for the *arr apps. Each method is one API call — anything
that combines calls (add-and-search, monitor-then-search, …) lives in
services.py so views stay simple and these stay a 1:1 map of the APIs.

API references: Sonarr/Radarr v3, Prowlarr v1 (https://wiki.servarr.com).
"""
import logging

import requests

log = logging.getLogger('daredevil.arr')


class ArrError(Exception):
    """Any failure talking to an *arr app — message is safe to show in the UI."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class ArrNotConfigured(ArrError):
    pass


class BaseArrClient:
    name = 'arr'
    api_prefix = '/api/v3'
    timeout = 15

    def __init__(self, url, api_key):
        self.url = (url or '').rstrip('/')
        self.api_key = api_key or ''

    # ── Transport ───────────────────────────────────────────────────────────

    def _request(self, method, path, params=None, json=None, timeout=None):
        if not self.url or not self.api_key:
            raise ArrNotConfigured(f'{self.name} is not configured — add its URL and API key in Settings → {self.name}.')
        url = f'{self.url}{self.api_prefix}{path}'
        try:
            resp = requests.request(
                method, url, params=params, json=json,
                headers={'X-Api-Key': self.api_key},
                timeout=timeout or self.timeout,
            )
        except requests.Timeout:
            raise ArrError(f'{self.name} did not respond in time ({url})')
        except requests.RequestException as e:
            raise ArrError(f'Could not reach {self.name} at {self.url} — {type(e).__name__}')
        if resp.status_code >= 400:
            raise ArrError(self._error_message(resp), status=resp.status_code, payload=_safe_json(resp))
        if not resp.content:
            return None
        return _safe_json(resp)

    def _error_message(self, resp):
        if resp.status_code == 401:
            return f'{self.name} rejected the API key'
        body = _safe_json(resp)
        # Validation failures come back as a list of {propertyName, errorMessage}
        if isinstance(body, list):
            msgs = [b.get('errorMessage') for b in body if isinstance(b, dict) and b.get('errorMessage')]
            if msgs:
                return '; '.join(dict.fromkeys(msgs))
        if isinstance(body, dict):
            msg = body.get('message') or body.get('errorMessage') or body.get('description')
            if msg:
                return str(msg)
        return f'{self.name} returned HTTP {resp.status_code}'

    def get(self, path, timeout=None, **params):
        return self._request('GET', path, params=params or None, timeout=timeout)

    def post(self, path, body=None, params=None, timeout=None):
        return self._request('POST', path, params=params, json=body, timeout=timeout)

    def put(self, path, body=None, params=None, timeout=None):
        return self._request('PUT', path, params=params, json=body, timeout=timeout)

    def delete(self, path, params=None, body=None, timeout=None):
        return self._request('DELETE', path, params=params, json=body, timeout=timeout)

    # ── Shared endpoints ────────────────────────────────────────────────────

    def system_status(self):
        return self.get('/system/status', timeout=8)

    def health(self):
        return self.get('/health') or []

    def tags(self):
        return self.get('/tag') or []

    def create_tag(self, label):
        return self.post('/tag', {'label': label})

    def download_clients(self):
        return self.get('/downloadclient') or []

    def download_client_schema(self):
        return self.get('/downloadclient/schema') or []

    def add_download_client(self, payload):
        return self.post('/downloadclient', payload, timeout=30)

    def update_download_client(self, payload):
        return self.put(f'/downloadclient/{payload["id"]}', payload, timeout=30)

    def notifications(self):
        return self.get('/notification') or []

    def notification_schema(self):
        return self.get('/notification/schema') or []

    def add_notification(self, payload):
        return self.post('/notification', payload, timeout=30)

    def update_notification(self, payload):
        return self.put(f'/notification/{payload["id"]}', payload, timeout=30)

    def test_notification(self, payload):
        return self.post('/notification/test', payload, timeout=30)

    def delete_notification(self, notification_id):
        return self.delete(f'/notification/{notification_id}')

    def command(self, name, **body):
        return self.post('/command', {'name': name, **body})


class _MediaArrClient(BaseArrClient):
    """Endpoints Sonarr and Radarr share (same v3 API shape)."""

    def root_folders(self):
        return self.get('/rootfolder') or []

    def add_root_folder(self, path):
        return self.post('/rootfolder', {'path': path})

    def delete_root_folder(self, folder_id):
        return self.delete(f'/rootfolder/{folder_id}')

    def quality_profiles(self):
        return self.get('/qualityprofile') or []

    def quality_profile(self, profile_id):
        return self.get(f'/qualityprofile/{profile_id}')

    def update_quality_profile(self, payload):
        return self.put(f'/qualityprofile/{payload["id"]}', payload)

    def quality_definitions(self):
        return self.get('/qualitydefinition') or []

    def quality_definition_limits(self):
        return self.get('/qualitydefinition/limits') or {}

    def update_quality_definitions(self, definitions):
        return self.put('/qualitydefinition/update', definitions)

    def import_lists(self):
        return self.get('/importlist') or []

    def import_list_schema(self):
        return self.get('/importlist/schema') or []

    def add_import_list(self, payload):
        return self.post('/importlist', payload, timeout=60)

    def update_import_list(self, payload):
        return self.put(f'/importlist/{payload["id"]}', payload, timeout=60)

    def delete_import_list(self, list_id):
        return self.delete(f'/importlist/{list_id}')

    def indexers(self):
        return self.get('/indexer') or []

    def queue(self, page_size=500, **params):
        return self.get('/queue', timeout=20, page=1, pageSize=page_size, **params)

    def queue_remove(self, ids, remove_from_client=True, blocklist=False, skip_redownload=False):
        """Bulk-remove queue records. blocklist=True + skip_redownload=False is
        the 'try a different release' retry — Sonarr/Radarr search again
        straight away and won't pick the same release twice."""
        return self.delete(
            '/queue/bulk',
            params={
                'removeFromClient': str(remove_from_client).lower(),
                'blocklist': str(blocklist).lower(),
                'skipRedownload': str(skip_redownload).lower(),
            },
            body={'ids': list(ids)},
            timeout=30,
        )

    def queue_grab(self, queue_id):
        """Force-grab an item that's sitting in the delay queue."""
        return self.post(f'/queue/grab/{queue_id}')

    def history(self, page=1, page_size=50, **params):
        return self.get('/history', timeout=20, page=page, pageSize=page_size,
                        sortKey='date', sortDirection='descending', **params)

    def history_mark_failed(self, history_id):
        return self.post(f'/history/failed/{history_id}')

    def releases(self, timeout=90, **params):
        """Interactive search — queries every indexer live, so it can be slow."""
        return self.get('/release', timeout=timeout, **params) or []

    def grab_release(self, guid, indexer_id, **extra):
        return self.post('/release', {'guid': guid, 'indexerId': indexer_id, **extra}, timeout=60)

    def push_release(self, payload):
        return self.post('/release/push', payload, timeout=60)

    def parse(self, title):
        return self.get('/parse', timeout=30, title=title) or {}

    def calendar(self, start, end, **params):
        return self.get('/calendar', start=start, end=end, **params) or []

    def wanted_missing(self, page=1, page_size=50, **params):
        return self.get('/wanted/missing', page=page, pageSize=page_size, **params)

    def naming_config(self):
        return self.get('/config/naming')

    def update_naming_config(self, payload):
        return self.put(f'/config/naming/{payload.get("id", 1)}', payload)

    def media_management_config(self):
        return self.get('/config/mediamanagement')

    def update_media_management_config(self, payload):
        return self.put(f'/config/mediamanagement/{payload.get("id", 1)}', payload)


class SonarrClient(_MediaArrClient):
    name = 'Sonarr'

    def series_list(self):
        return self.get('/series', timeout=30) or []

    def series(self, series_id):
        return self.get(f'/series/{series_id}')

    def lookup(self, term):
        return self.get('/series/lookup', timeout=30, term=term) or []

    def add_series(self, payload):
        return self.post('/series', payload, timeout=60)

    def update_series(self, series):
        return self.put(f'/series/{series["id"]}', series, params={'moveFiles': 'false'}, timeout=30)

    def delete_series(self, series_id, delete_files=False):
        return self.delete(f'/series/{series_id}', params={
            'deleteFiles': str(delete_files).lower(), 'addImportListExclusion': 'false',
        }, timeout=60)

    def season_pass(self, series_id, monitor, monitored=True):
        """Re-apply a Sonarr monitoring preset (all/future/missing/…) to every
        episode of an existing series — the same thing the 'Monitor' select in
        Sonarr's own series editor does."""
        return self.post('/seasonpass', {
            'series': [{'id': series_id, 'monitored': monitored}],
            'monitoringOptions': {'monitor': monitor},
        })

    def episodes(self, series_id, include_images=False, include_episode_file=False):
        return self.get('/episode', timeout=30, seriesId=series_id,
                        includeImages=str(include_images).lower(),
                        includeEpisodeFile=str(include_episode_file).lower()) or []

    def set_episodes_monitored(self, episode_ids, monitored=True):
        return self.put('/episode/monitor', {'episodeIds': list(episode_ids), 'monitored': monitored})

    def delete_episode_file(self, file_id):
        return self.delete(f'/episodefile/{file_id}')

    def queue_details(self, series_id):
        return self.get('/queue/details', seriesId=series_id, includeEpisode='true') or []


class RadarrClient(_MediaArrClient):
    name = 'Radarr'

    def movies(self):
        return self.get('/movie', timeout=30) or []

    def movie(self, movie_id):
        return self.get(f'/movie/{movie_id}')

    def movie_by_tmdb(self, tmdb_id):
        found = self.get('/movie', tmdbId=tmdb_id) or []
        return found[0] if found else None

    def lookup(self, term):
        return self.get('/movie/lookup', timeout=30, term=term) or []

    def lookup_tmdb(self, tmdb_id):
        return self.get('/movie/lookup/tmdb', timeout=30, tmdbId=tmdb_id)

    def add_movie(self, payload):
        return self.post('/movie', payload, timeout=60)

    def update_movie(self, movie):
        return self.put(f'/movie/{movie["id"]}', movie, params={'moveFiles': 'false'}, timeout=30)

    def delete_movie(self, movie_id, delete_files=False):
        return self.delete(f'/movie/{movie_id}', params={
            'deleteFiles': str(delete_files).lower(), 'addImportExclusion': 'false',
        }, timeout=60)

    def delete_movie_file(self, file_id):
        return self.delete(f'/moviefile/{file_id}')

    def queue_details(self, movie_id):
        return self.get('/queue/details', movieId=movie_id, includeMovie='false') or []


class ProwlarrClient(BaseArrClient):
    name = 'Prowlarr'
    api_prefix = '/api/v1'

    def indexers(self):
        return self.get('/indexer') or []

    def indexer(self, indexer_id):
        return self.get(f'/indexer/{indexer_id}')

    def indexer_schema(self):
        # ~650 definitions / ~4 MB — callers cache this (services.indexer_schema)
        return self.get('/indexer/schema', timeout=60) or []

    # Prowlarr tests an indexer (a live search against the site, allowed up
    # to ~100s) before saving it, so these need a generous timeout.
    def add_indexer(self, payload, force=False):
        return self.post('/indexer', payload, params={'forceSave': str(force).lower()}, timeout=150)

    def update_indexer(self, payload, force=False):
        return self.put(f'/indexer/{payload["id"]}', payload, params={'forceSave': str(force).lower()}, timeout=150)

    def delete_indexer(self, indexer_id):
        return self.delete(f'/indexer/{indexer_id}')

    def test_indexer(self, payload):
        return self.post('/indexer/test', payload, timeout=150)

    def test_all_indexers(self):
        return self.post('/indexer/testall', timeout=300) or []

    def indexer_status(self):
        return self.get('/indexerstatus') or []

    def indexer_stats(self):
        return (self.get('/indexerstats') or {}).get('indexers', [])

    def categories(self):
        return self.get('/indexer/categories') or []

    def app_profiles(self):
        return self.get('/appprofile') or []

    def applications(self):
        return self.get('/applications') or []

    def application_schema(self):
        return self.get('/applications/schema') or []

    def add_application(self, payload):
        return self.post('/applications', payload, timeout=60)

    def update_application(self, payload):
        return self.put(f'/applications/{payload["id"]}', payload, timeout=60)

    def search(self, query='', search_type='search', indexer_ids=None, categories=None, limit=100, offset=0):
        params = {'query': query, 'type': search_type, 'limit': limit, 'offset': offset}
        if indexer_ids:
            params['indexerIds'] = list(indexer_ids)
        if categories:
            params['categories'] = list(categories)
        return self._request('GET', '/search', params=params, timeout=120) or []


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return None
