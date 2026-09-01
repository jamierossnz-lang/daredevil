import requests
from django.conf import settings


class TraktClient:
    """
    Minimal read-only client for Trakt's public data endpoints. Only the
    'anticipated' lists are used here — they're ranked by how many users
    have added a title to their watchlist, which is a much better signal
    for 'newly announced and generating buzz' than anything TMDB exposes
    on its own. No OAuth needed; these endpoints only require the app's
    Client ID.
    """

    BASE = 'https://api.trakt.tv'

    def _get(self, path, **params):
        headers = {
            'Content-Type': 'application/json',
            'trakt-api-version': '2',
            'trakt-api-key': settings.TRAKT_CLIENT_ID,
        }
        resp = requests.get(f'{self.BASE}{path}', headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def anticipated_movies(self, limit=50):
        return self._get('/movies/anticipated', limit=limit)

    def anticipated_shows(self, limit=50):
        return self._get('/shows/anticipated', limit=limit)


trakt = TraktClient()
