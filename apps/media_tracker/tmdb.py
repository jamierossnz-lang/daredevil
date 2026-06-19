import requests
from django.conf import settings


class TMDBClient:
    BASE = settings.TMDB_BASE_URL

    def _get(self, path, **params):
        params['api_key'] = settings.TMDB_API_KEY
        resp = requests.get(f'{self.BASE}{path}', params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ── Discover ────────────────────────────────────────────────────────────

    def discover_tv(self, **params):
        return self._get('/discover/tv', **params)

    def discover_movie(self, **params):
        return self._get('/discover/movie', **params)

    def tv_on_the_air(self, page=1):
        return self._get('/tv/on_the_air', page=page)

    # ── Search ──────────────────────────────────────────────────────────────

    def search_tv(self, query, page=1):
        return self._get('/search/tv', query=query, page=page)

    def search_movie(self, query, page=1):
        return self._get('/search/movie', query=query, page=page)

    # ── TV Shows ────────────────────────────────────────────────────────────

    def get_tv(self, tmdb_id):
        return self._get(f'/tv/{tmdb_id}')

    def get_season(self, tmdb_id, season_number):
        return self._get(f'/tv/{tmdb_id}/season/{season_number}')

    def get_episode(self, tmdb_id, season_number, episode_number):
        return self._get(f'/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}')

    # ── Movies ──────────────────────────────────────────────────────────────

    def get_movie(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}', append_to_response='release_dates')

    def get_movie_release_dates(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/release_dates')

    def get_movie_watch_providers(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/watch/providers')

    def get_movie_credits(self, tmdb_id):
        return self._get(f'/movie/{tmdb_id}/credits')

    def get_movie_recommendations(self, tmdb_id, page=1):
        return self._get(f'/movie/{tmdb_id}/recommendations', page=page)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def sync_show_to_db(self, tmdb_id):
        """Fetch full show details from TMDB and upsert into the database."""
        from apps.media_tracker.models import TVShow, Season, Episode
        from datetime import date

        data = self.get_tv(tmdb_id)

        status_map = {
            'Returning Series': TVShow.Status.RETURNING,
            'Ended': TVShow.Status.ENDED,
            'Canceled': TVShow.Status.CANCELLED,
            'In Production': TVShow.Status.IN_PRODUCTION,
            'Planned': TVShow.Status.PLANNED,
        }

        show, _ = TVShow.objects.update_or_create(
            tmdb_id=tmdb_id,
            defaults={
                'name': data.get('name', ''),
                'overview': data.get('overview', ''),
                'poster_path': data.get('poster_path', '') or '',
                'backdrop_path': data.get('backdrop_path', '') or '',
                'first_air_date': _parse_date(data.get('first_air_date')),
                'status': status_map.get(data.get('status', ''), TVShow.Status.RETURNING),
                'number_of_seasons': data.get('number_of_seasons', 0),
                'number_of_episodes': data.get('number_of_episodes', 0),
                'networks': ', '.join(n['name'] for n in data.get('networks', [])),
                'genres': ', '.join(g['name'] for g in data.get('genres', [])),
                'vote_average': data.get('vote_average', 0),
            },
        )

        for season_data in data.get('seasons', []):
            if season_data['season_number'] == 0:
                continue
            season, _ = Season.objects.update_or_create(
                show=show,
                season_number=season_data['season_number'],
                defaults={
                    'tmdb_id': season_data.get('id', 0),
                    'name': season_data.get('name', ''),
                    'overview': season_data.get('overview', ''),
                    'poster_path': season_data.get('poster_path', '') or '',
                    'air_date': _parse_date(season_data.get('air_date')),
                    'episode_count': season_data.get('episode_count', 0),
                },
            )
            self._sync_season_episodes(show, season)

        from django.utils import timezone
        show.last_synced = timezone.now()
        show.save(update_fields=['last_synced'])
        return show

    def _sync_season_episodes(self, show, season):
        from apps.media_tracker.models import Episode

        try:
            data = self.get_season(show.tmdb_id, season.season_number)
        except Exception:
            return

        for ep_data in data.get('episodes', []):
            Episode.objects.update_or_create(
                season=season,
                episode_number=ep_data['episode_number'],
                defaults={
                    'tmdb_id': ep_data.get('id', 0),
                    'name': ep_data.get('name', ''),
                    'overview': ep_data.get('overview', ''),
                    'air_date': _parse_date(ep_data.get('air_date')),
                    'still_path': ep_data.get('still_path', '') or '',
                    'runtime': ep_data.get('runtime'),
                    'vote_average': ep_data.get('vote_average', 0),
                },
            )

    def sync_movie_to_db(self, tmdb_id):
        from apps.media_tracker.models import Movie
        from django.utils import timezone
        from datetime import timedelta

        data = self.get_movie(tmdb_id)

        status_map = {
            'Released': Movie.Status.RELEASED,
            'In Production': Movie.Status.IN_PRODUCTION,
            'Post Production': Movie.Status.IN_PRODUCTION,
            'Planned': Movie.Status.UPCOMING,
            'Rumored': Movie.Status.UPCOMING,
        }

        # Type 4 = Digital release. If none found, estimate release_date + 45 days.
        digital_date = _extract_digital_release(data.get('release_dates', {}).get('results', []))
        if not digital_date:
            theatrical = _parse_date(data.get('release_date'))
            if theatrical:
                digital_date = theatrical + timedelta(days=45)

        movie, _ = Movie.objects.update_or_create(
            tmdb_id=tmdb_id,
            defaults={
                'title': data.get('title', ''),
                'overview': data.get('overview', ''),
                'poster_path': data.get('poster_path', '') or '',
                'backdrop_path': data.get('backdrop_path', '') or '',
                'release_date': _parse_date(data.get('release_date')),
                'digital_release_date': digital_date,
                'runtime': data.get('runtime'),
                'genres': ', '.join(g['name'] for g in data.get('genres', [])),
                'vote_average': data.get('vote_average', 0),
                'status': status_map.get(data.get('status', ''), Movie.Status.RELEASED),
                'last_synced': timezone.now(),
            },
        )
        return movie


def _parse_date(value):
    if not value:
        return None
    try:
        from datetime import date
        parts = str(value).split('-')
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None


def _extract_digital_release(results):
    """Pull the earliest type-4 (Digital) release date for the US from release_dates results."""
    candidates = []
    for country in results:
        if country.get('iso_3166_1') == 'US':
            for rd in country.get('release_dates', []):
                if rd.get('type') == 4 and rd.get('release_date'):
                    candidates.append(_parse_date(rd['release_date'][:10]))
    return min(candidates) if candidates else None


def is_available_on_watch_providers(watch_providers_data, region='US'):
    """Return True if the movie has any streaming or rental providers in the given region."""
    region_data = watch_providers_data.get('results', {}).get(region, {})
    return bool(region_data.get('flatrate') or region_data.get('rent'))


tmdb = TMDBClient()
