import requests
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger('daredevil.tvmaze')

TVMAZE_BASE = 'https://api.tvmaze.com'
UTC_TZ = ZoneInfo('UTC')

_NOT_FOUND = (400, 404)


class TVMazeClient:
    def _get(self, path, **params):
        resp = requests.get(f'{TVMAZE_BASE}{path}', params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _lookup(self, **params):
        """
        GET /lookup/shows with arbitrary params. Returns the show dict or None.
        TVMaze returns 404 for unknown IDs and 400 for unrecognised param names,
        so we treat both as "not found".
        """
        try:
            return self._get('/lookup/shows', **params)
        except requests.HTTPError as e:
            if e.response.status_code in _NOT_FOUND:
                return None
            raise

    def lookup_by_thetvdb(self, tvdb_id):
        return self._lookup(thetvdb=tvdb_id)

    def search_by_name(self, name):
        """Single-search: returns the best-match show dict or None."""
        try:
            return self._get('/singlesearch/shows', q=name)
        except requests.HTTPError as e:
            if e.response.status_code in _NOT_FOUND:
                return None
            raise

    def get_episodes(self, tvmaze_id):
        """Return list of all episode dicts for a TVMaze show ID."""
        return self._get(f'/shows/{tvmaze_id}/episodes')

    def _find_tvmaze_show(self, show):
        """
        Resolve a TVShow to a TVMaze show dict. Strategy:
          1. TMDB external IDs → TheTVDB ID → TVMaze lookup (most reliable)
          2. Name search fallback (catches shows not yet linked in TMDB/TVDb)
        Returns the dict or None.
        """
        # 1. Try TheTVDB ID via TMDB external IDs
        try:
            from apps.media_tracker.tmdb import tmdb as tmdb_client
            ext = tmdb_client._get(f'/tv/{show.tmdb_id}/external_ids')
            tvdb_id = ext.get('tvdb_id')
            if tvdb_id:
                result = self.lookup_by_thetvdb(tvdb_id)
                if result:
                    log.info('TVMaze: found %s via TVDb ID %s → TVMaze %s', show.name, tvdb_id, result['id'])
                    return result
        except Exception as e:
            log.debug('TVMaze: TVDb lookup failed for %s: %s', show.name, e)

        # 2. Name search fallback
        result = self.search_by_name(show.name)
        if result:
            log.info('TVMaze: found %s via name search → TVMaze %s', show.name, result['id'])
        else:
            log.warning('TVMaze: no match found for %s (TMDB %s)', show.name, show.tmdb_id)
        return result

    def sync_airdates_for_show(self, show):
        """
        Fetch TVMaze episodes and update Episode.air_datetime for all matching episodes.
        Matches by season + episode number. Datetimes stored as UTC.
        Returns the number of episodes updated.
        """
        if not show.tvmaze_id:
            tvmaze_show = self._find_tvmaze_show(show)
            if not tvmaze_show:
                return 0
            show.tvmaze_id = tvmaze_show['id']
            show.save(update_fields=['tvmaze_id'])

        try:
            episodes = self.get_episodes(show.tvmaze_id)
        except Exception as e:
            log.warning('TVMaze: failed to get episodes for %s: %s', show.name, e)
            return 0

        # (season, episode_number) → airstamp string
        airtime_map = {}
        for ep in episodes:
            key = (ep.get('season'), ep.get('number'))
            airstamp = ep.get('airstamp')
            if airstamp:
                airtime_map[key] = airstamp

        updated = 0
        for season in show.seasons.prefetch_related('episodes').all():
            for ep in season.episodes.all():
                key = (season.season_number, ep.episode_number)
                airstamp = airtime_map.get(key)
                if not airstamp:
                    continue
                try:
                    dt = datetime.fromisoformat(airstamp)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC_TZ)
                    dt_utc = dt.astimezone(UTC_TZ)
                    if ep.air_datetime != dt_utc:
                        ep.air_datetime = dt_utc
                        ep.save(update_fields=['air_datetime'])
                        updated += 1
                except Exception as e:
                    log.warning('TVMaze: bad airstamp %r for %s: %s', airstamp, ep, e)

        log.info('TVMaze: updated %d air datetimes for %s', updated, show.name)
        return updated


tvmaze = TVMazeClient()
