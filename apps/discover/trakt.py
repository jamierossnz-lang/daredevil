import requests
from django.conf import settings


class TraktClient:
    """
    Minimal read-only client for Trakt's public data endpoints — no OAuth
    needed, these only require the app's Client ID. Used for four
    discovery angles TMDB doesn't offer on its own: Trending (currently
    most-watched), Releases (new/upcoming via the release calendar),
    Anticipated (most watchlisted — a good 'newly announced and buzzing'
    signal), and Popular (all-time ranking).
    """

    BASE = 'https://api.trakt.tv'

    def _get(self, path, client_id=None, **params):
        headers = {
            'Content-Type': 'application/json',
            'trakt-api-version': '2',
            'trakt-api-key': client_id or settings.TRAKT_CLIENT_ID,
        }
        resp = requests.get(f'{self.BASE}{path}', headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def trending_movies(self, limit=50):
        return self._get('/movies/trending', limit=limit)

    def trending_shows(self, limit=50):
        return self._get('/shows/trending', limit=limit)

    def popular_movies(self, limit=50):
        return self._get('/movies/popular', limit=limit)

    def popular_shows(self, limit=50):
        return self._get('/shows/popular', limit=limit)

    def anticipated_movies(self, limit=50):
        return self._get('/movies/anticipated', limit=limit)

    def anticipated_shows(self, limit=50):
        return self._get('/shows/anticipated', limit=limit)

    def released_movies(self, start_date, days=30):
        """New/upcoming releases (theatrical + digital) in the given window."""
        return self._get(f'/calendars/all/movies/{start_date}/{days}')

    def released_shows(self, start_date, days=30):
        """Shows with new episodes premiering/airing in the given window."""
        return self._get(f'/calendars/all/shows/{start_date}/{days}')

    def test_connection(self, client_id=None):
        """Lightweight authenticated call — raises if the Client ID is invalid or unreachable."""
        self._get('/movies/popular', client_id=client_id, limit=1)


trakt = TraktClient()
