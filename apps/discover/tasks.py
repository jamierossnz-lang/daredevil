import logging
from celery import shared_task
from django.conf import settings

from apps.media_tracker.tmdb import tmdb, _parse_date
from .genres import genre_names

log = logging.getLogger('daredevil.discover.tasks')

# Caps keep each nightly run fast and stop the pool growing unbounded —
# the deck is meant to be a curated trickle, not a dump of everything TMDB
# and Trakt know about.
RECS_PER_LIBRARY_ITEM = 6
MAX_NEW_RECS_PER_RUN = 25
MAX_PER_TRAKT_CATEGORY = 8  # ×4 categories ×2 media types ≈ same order of magnitude as before


def _unwrap_trakt_entry(entry, key):
    """
    Trakt list entries are shaped differently per endpoint: trending/
    anticipated/calendar wrap the movie/show object under a named key
    (e.g. {'watchers': 12, 'movie': {...}}); popular returns the object
    bare. Normalize to the object itself either way.
    """
    if isinstance(entry, dict) and isinstance(entry.get(key), dict):
        return entry[key]
    return entry if isinstance(entry, dict) else None


def _existing_and_decided(media_type):
    """tmdb_ids already in the library, already in the pool, or already swiped on."""
    from apps.media_tracker.models import Movie, TVShow
    from .models import DiscoverItem, DiscoverDecision

    if media_type == 'movie':
        library_ids = set(Movie.objects.values_list('tmdb_id', flat=True))
    else:
        library_ids = set(TVShow.objects.values_list('tmdb_id', flat=True))
    pool_ids = set(DiscoverItem.objects.filter(media_type=media_type).values_list('tmdb_id', flat=True))
    decided_ids = set(DiscoverDecision.objects.filter(media_type=media_type).values_list('tmdb_id', flat=True))
    return library_ids | pool_ids | decided_ids


@shared_task(name='generate_recommendations')
def generate_recommendations():
    """
    Nightly: for every TVShow/Movie already in the library, pull TMDB's
    'recommendations' and 'similar' lists and add any title not already in
    the library/pool/swipe-history to the discovery deck. This is the
    'because you like X' half of the For You feed.
    """
    from apps.media_tracker.models import Movie, TVShow
    from .models import DiscoverItem

    seen_movie_ids = _existing_and_decided('movie')
    seen_show_ids = _existing_and_decided('tv')
    added_movies = 0
    added_shows = 0

    for movie in Movie.objects.all().only('id', 'tmdb_id', 'title'):
        if added_movies >= MAX_NEW_RECS_PER_RUN:
            break
        for fetcher in (tmdb.get_movie_recommendations, tmdb.get_movie_similar):
            if added_movies >= MAX_NEW_RECS_PER_RUN:
                break
            try:
                results = fetcher(movie.tmdb_id).get('results', [])
            except Exception as e:
                log.warning('generate_recommendations: movie %s fetch failed — %s', movie.tmdb_id, e)
                continue
            for r in results[:RECS_PER_LIBRARY_ITEM]:
                cand_id = r.get('id')
                if not cand_id or cand_id in seen_movie_ids:
                    continue
                DiscoverItem.objects.get_or_create(
                    tmdb_id=cand_id,
                    media_type=DiscoverItem.MediaType.MOVIE,
                    defaults=dict(
                        title=r.get('title', ''),
                        overview=r.get('overview', ''),
                        poster_path=r.get('poster_path') or '',
                        backdrop_path=r.get('backdrop_path') or '',
                        release_date=_parse_date(r.get('release_date')),
                        vote_average=r.get('vote_average', 0),
                        genres=genre_names(r.get('genre_ids'), 'movie'),
                        source=DiscoverItem.Source.RECOMMENDATION,
                        source_title=movie.title,
                    ),
                )
                seen_movie_ids.add(cand_id)
                added_movies += 1
                if added_movies >= MAX_NEW_RECS_PER_RUN:
                    break

    for show in TVShow.objects.all().only('id', 'tmdb_id', 'name'):
        if added_shows >= MAX_NEW_RECS_PER_RUN:
            break
        for fetcher in (tmdb.get_tv_recommendations, tmdb.get_tv_similar):
            if added_shows >= MAX_NEW_RECS_PER_RUN:
                break
            try:
                results = fetcher(show.tmdb_id).get('results', [])
            except Exception as e:
                log.warning('generate_recommendations: show %s fetch failed — %s', show.tmdb_id, e)
                continue
            for r in results[:RECS_PER_LIBRARY_ITEM]:
                cand_id = r.get('id')
                if not cand_id or cand_id in seen_show_ids:
                    continue
                DiscoverItem.objects.get_or_create(
                    tmdb_id=cand_id,
                    media_type=DiscoverItem.MediaType.TV,
                    defaults=dict(
                        title=r.get('name', ''),
                        overview=r.get('overview', ''),
                        poster_path=r.get('poster_path') or '',
                        backdrop_path=r.get('backdrop_path') or '',
                        release_date=_parse_date(r.get('first_air_date')),
                        vote_average=r.get('vote_average', 0),
                        genres=genre_names(r.get('genre_ids'), 'tv'),
                        source=DiscoverItem.Source.RECOMMENDATION,
                        source_title=show.name,
                    ),
                )
                seen_show_ids.add(cand_id)
                added_shows += 1
                if added_shows >= MAX_NEW_RECS_PER_RUN:
                    break

    return f'added {added_movies} movies, {added_shows} shows (recommendations)'


@shared_task(name='generate_anticipated')
def generate_anticipated():
    """
    Nightly: pull four different Trakt discovery angles — Trending,
    Releases (calendar), Anticipated (most watchlisted — a good 'newly
    announced and buzzing' signal TMDB doesn't expose on its own), and
    Popular — and add any not-yet-seen title to the deck, enriched with
    full TMDB metadata. Each category gets its own small cap so no single
    angle crowds out the others.
    """
    from datetime import date
    from .models import DiscoverItem
    from .trakt import trakt

    if not settings.TRAKT_CLIENT_ID:
        log.info('generate_anticipated: TRAKT_CLIENT_ID not configured, skipping')
        return 'skipped — TRAKT_CLIENT_ID not configured'

    seen_movie_ids = _existing_and_decided('movie')
    seen_show_ids = _existing_and_decided('tv')
    added_movies = 0
    added_shows = 0
    today = date.today().isoformat()

    movie_sources = [
        ('Trending on Trakt', lambda: trakt.trending_movies(limit=50)),
        ('New Release',       lambda: trakt.released_movies(today, 30)),
        ('Most Anticipated',  lambda: trakt.anticipated_movies(limit=50)),
        ('Popular on Trakt',  lambda: trakt.popular_movies(limit=50)),
    ]
    show_sources = [
        ('Trending on Trakt', lambda: trakt.trending_shows(limit=50)),
        ('New Release',       lambda: trakt.released_shows(today, 30)),
        ('Most Anticipated',  lambda: trakt.anticipated_shows(limit=50)),
        ('Popular on Trakt',  lambda: trakt.popular_shows(limit=50)),
    ]

    for label, fetch in movie_sources:
        try:
            entries = fetch()
        except Exception as e:
            log.warning('generate_anticipated: trakt movies (%s) fetch failed — %s', label, e)
            continue
        added_this_category = 0
        for entry in entries:
            if added_this_category >= MAX_PER_TRAKT_CATEGORY:
                break
            obj = _unwrap_trakt_entry(entry, 'movie')
            cand_id = ((obj or {}).get('ids') or {}).get('tmdb')
            if not cand_id or cand_id in seen_movie_ids:
                continue
            try:
                details = tmdb.get_movie(cand_id)
            except Exception as e:
                log.warning('generate_anticipated: tmdb detail fetch failed for movie %s — %s', cand_id, e)
                continue
            DiscoverItem.objects.get_or_create(
                tmdb_id=cand_id,
                media_type=DiscoverItem.MediaType.MOVIE,
                defaults=dict(
                    title=details.get('title', ''),
                    overview=details.get('overview', ''),
                    poster_path=details.get('poster_path') or '',
                    backdrop_path=details.get('backdrop_path') or '',
                    release_date=_parse_date(details.get('release_date')),
                    vote_average=details.get('vote_average', 0),
                    genres=', '.join(g['name'] for g in details.get('genres', [])),
                    source=DiscoverItem.Source.ANTICIPATED,
                    source_title=label,
                ),
            )
            seen_movie_ids.add(cand_id)
            added_this_category += 1
            added_movies += 1

    for label, fetch in show_sources:
        try:
            entries = fetch()
        except Exception as e:
            log.warning('generate_anticipated: trakt shows (%s) fetch failed — %s', label, e)
            continue
        added_this_category = 0
        for entry in entries:
            if added_this_category >= MAX_PER_TRAKT_CATEGORY:
                break
            obj = _unwrap_trakt_entry(entry, 'show')
            cand_id = ((obj or {}).get('ids') or {}).get('tmdb')
            if not cand_id or cand_id in seen_show_ids:
                continue
            try:
                details = tmdb.get_tv(cand_id)
            except Exception as e:
                log.warning('generate_anticipated: tmdb detail fetch failed for show %s — %s', cand_id, e)
                continue
            DiscoverItem.objects.get_or_create(
                tmdb_id=cand_id,
                media_type=DiscoverItem.MediaType.TV,
                defaults=dict(
                    title=details.get('name', ''),
                    overview=details.get('overview', ''),
                    poster_path=details.get('poster_path') or '',
                    backdrop_path=details.get('backdrop_path') or '',
                    release_date=_parse_date(details.get('first_air_date')),
                    vote_average=details.get('vote_average', 0),
                    genres=', '.join(g['name'] for g in details.get('genres', [])),
                    source=DiscoverItem.Source.ANTICIPATED,
                    source_title=label,
                ),
            )
            seen_show_ids.add(cand_id)
            added_this_category += 1
            added_shows += 1

    return f'added {added_movies} movies, {added_shows} shows (trending/releases/anticipated/popular)'
