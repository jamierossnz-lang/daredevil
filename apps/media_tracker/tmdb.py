import hashlib
import json
import threading

import requests
from django.conf import settings
from django.core.cache import cache

_local = threading.local()

# Lists change slowly — browse pages and the dashboard reuse them for a while
LIST_TTL = 1800


class TMDBClient:
    BASE = settings.TMDB_BASE_URL

    def _session(self):
        # One keep-alive session per thread: skips a TLS handshake on every call
        session = getattr(_local, 'session', None)
        if session is None:
            session = _local.session = requests.Session()
        return session

    def _get(self, path, ttl=None, refresh=False, **params):
        """
        GET from TMDB. With ttl the JSON is kept in the shared cache for that
        many seconds; refresh fetches it again and replaces the cached copy.
        """
        key = None
        if ttl:
            digest = hashlib.md5(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
            key = f'tmdb_{digest}'
            hit = None if refresh else cache.get(key)
            if hit is not None:
                return hit
        resp = self._session().get(f'{self.BASE}{path}', params={**params, 'api_key': settings.TMDB_API_KEY}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if key:
            cache.set(key, data, ttl)
        return data

    # ── Discover ────────────────────────────────────────────────────────────

    def discover_tv(self, ttl=LIST_TTL, refresh=False, **params):
        return self._get('/discover/tv', ttl=ttl, refresh=refresh, **params)

    def discover_movie(self, ttl=LIST_TTL, refresh=False, **params):
        return self._get('/discover/movie', ttl=ttl, refresh=refresh, **params)

    def tv_on_the_air(self, page=1):
        return self._get('/tv/on_the_air', page=page)

    def trending(self, media_type, window='week', page=1):
        return self._get(f'/trending/{media_type}/{window}', ttl=LIST_TTL, page=page)

    def popular(self, media_type, page=1):
        return self._get(f'/{media_type}/popular', ttl=LIST_TTL, page=page)

    def movie_upcoming(self, page=1):
        return self._get('/movie/upcoming', ttl=LIST_TTL, page=page)

    # ── Search ──────────────────────────────────────────────────────────────

    def search_tv(self, query, page=1):
        return self._get('/search/tv', query=query, page=page)

    def search_movie(self, query, page=1):
        return self._get('/search/movie', query=query, page=page)

    # ── TV Shows ────────────────────────────────────────────────────────────

    def get_tv(self, tmdb_id):
        return self._get(f'/tv/{tmdb_id}')

    def get_tv_videos(self, tmdb_id):
        return self._get(f'/tv/{tmdb_id}/videos')

    def get_season(self, tmdb_id, season_number):
        return self._get(f'/tv/{tmdb_id}/season/{season_number}')

    def get_episode(self, tmdb_id, season_number, episode_number):
        return self._get(f'/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}')

    # ── Movies ──────────────────────────────────────────────────────────────

    def get_movie(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}', append_to_response='release_dates')

    def get_movie_videos(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/videos')

    def get_movie_release_dates(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/release_dates')

    def get_movie_watch_providers(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/watch/providers')

    def get_movie_credits(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/credits')

    def get_movie_recommendations(self, tmdb_id, page=1):
        return self._get(f'/movie/{tmdb_id}/recommendations', page=page)

    def get_movie_similar(self, tmdb_id, page=1):
        return self._get(f'/movie/{tmdb_id}/similar', page=page)

    def get_tv_recommendations(self, tmdb_id, page=1):
        return self._get(f'/tv/{tmdb_id}/recommendations', page=page)

    def get_tv_similar(self, tmdb_id, page=1):
        return self._get(f'/tv/{tmdb_id}/similar', page=page)


def _parse_date(value):
    if not value:
        return None
    try:
        from datetime import date
        parts = str(value).split('-')
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None


tmdb = TMDBClient()
