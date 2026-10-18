import hashlib
import json
import threading
from datetime import date

import requests
from django.conf import settings
from django.core.cache import cache

from config import env_file

_local = threading.local()

# Lists change slowly — browse pages and the dashboard reuse them for a while
LIST_TTL = 1800
REC_TTL = 6 * 3600


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
        resp = self._session().get(f'{self.BASE}{path}', params={**params, 'api_key': env_file.setting('TMDB_API_KEY')}, timeout=10)
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

    def trending(self, media_type, window='week', page=1):
        return self._get(f'/trending/{media_type}/{window}', ttl=LIST_TTL, page=page)

    def popular(self, media_type, page=1):
        return self._get(f'/{media_type}/popular', ttl=LIST_TTL, page=page)

    def movie_upcoming(self, page=1):
        return self._get('/movie/upcoming', ttl=LIST_TTL, page=page)

    # ── TV Shows ────────────────────────────────────────────────────────────

    def get_tv_videos(self, tmdb_id):
        return self._get(f'/tv/{tmdb_id}/videos')

    # ── Movies ──────────────────────────────────────────────────────────────

    def get_movie_videos(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/videos')

    def get_movie_credits(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/credits')

    # Recommendation lists drive For You (one call per library title per run) — cached
    def get_movie_recommendations(self, tmdb_id, page=1):
        return self._get(f'/movie/{tmdb_id}/recommendations', ttl=REC_TTL, page=page)

    def get_movie_similar(self, tmdb_id, page=1):
        return self._get(f'/movie/{tmdb_id}/similar', ttl=REC_TTL, page=page)

    def get_tv_recommendations(self, tmdb_id, page=1):
        return self._get(f'/tv/{tmdb_id}/recommendations', ttl=REC_TTL, page=page)

    def get_tv_similar(self, tmdb_id, page=1):
        return self._get(f'/tv/{tmdb_id}/similar', ttl=REC_TTL, page=page)


def _parse_date(value):
    """'YYYY-MM-DD' from TMDB → date (None if missing or malformed)."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


tmdb = TMDBClient()
