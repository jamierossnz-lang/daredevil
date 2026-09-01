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
MAX_NEW_ANTICIPATED_PER_RUN = 20


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
                DiscoverItem.objects.create(
                    tmdb_id=cand_id,
                    media_type=DiscoverItem.MediaType.MOVIE,
                    title=r.get('title', ''),
                    overview=r.get('overview', ''),
                    poster_path=r.get('poster_path') or '',
                    backdrop_path=r.get('backdrop_path') or '',
                    release_date=_parse_date(r.get('release_date')),
                    vote_average=r.get('vote_average', 0),
                    genres=genre_names(r.get('genre_ids'), 'movie'),
                    source=DiscoverItem.Source.RECOMMENDATION,
                    source_title=movie.title,
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
                DiscoverItem.objects.create(
                    tmdb_id=cand_id,
                    media_type=DiscoverItem.MediaType.TV,
                    title=r.get('name', ''),
                    overview=r.get('overview', ''),
                    poster_path=r.get('poster_path') or '',
                    backdrop_path=r.get('backdrop_path') or '',
                    release_date=_parse_date(r.get('first_air_date')),
                    vote_average=r.get('vote_average', 0),
                    genres=genre_names(r.get('genre_ids'), 'tv'),
                    source=DiscoverItem.Source.RECOMMENDATION,
                    source_title=show.name,
                )
                seen_show_ids.add(cand_id)
                added_shows += 1
                if added_shows >= MAX_NEW_RECS_PER_RUN:
                    break

    return f'added {added_movies} movies, {added_shows} shows (recommendations)'


@shared_task(name='generate_anticipated')
def generate_anticipated():
    """
    Nightly: pull Trakt's most-anticipated movies/shows — ranked by real
    watchlist-add activity, a far better 'newly announced and buzzing'
    signal than TMDB exposes on its own — and add any not-yet-seen title
    to the deck, enriched with full TMDB metadata for the card.
    """
    from .models import DiscoverItem
    from .trakt import trakt

    if not settings.TRAKT_CLIENT_ID:
        log.info('generate_anticipated: TRAKT_CLIENT_ID not configured, skipping')
        return 'skipped — TRAKT_CLIENT_ID not configured'

    seen_movie_ids = _existing_and_decided('movie')
    seen_show_ids = _existing_and_decided('tv')
    added_movies = 0
    added_shows = 0

    try:
        anticipated_movies = trakt.anticipated_movies(limit=50)
    except Exception as e:
        log.warning('generate_anticipated: trakt movies fetch failed — %s', e)
        anticipated_movies = []

    for entry in anticipated_movies:
        if added_movies >= MAX_NEW_ANTICIPATED_PER_RUN:
            break
        cand_id = ((entry.get('movie') or {}).get('ids') or {}).get('tmdb')
        if not cand_id or cand_id in seen_movie_ids:
            continue
        try:
            details = tmdb.get_movie(cand_id)
        except Exception as e:
            log.warning('generate_anticipated: tmdb detail fetch failed for movie %s — %s', cand_id, e)
            continue
        DiscoverItem.objects.create(
            tmdb_id=cand_id,
            media_type=DiscoverItem.MediaType.MOVIE,
            title=details.get('title', ''),
            overview=details.get('overview', ''),
            poster_path=details.get('poster_path') or '',
            backdrop_path=details.get('backdrop_path') or '',
            release_date=_parse_date(details.get('release_date')),
            vote_average=details.get('vote_average', 0),
            genres=', '.join(g['name'] for g in details.get('genres', [])),
            source=DiscoverItem.Source.ANTICIPATED,
        )
        seen_movie_ids.add(cand_id)
        added_movies += 1

    try:
        anticipated_shows = trakt.anticipated_shows(limit=50)
    except Exception as e:
        log.warning('generate_anticipated: trakt shows fetch failed — %s', e)
        anticipated_shows = []

    for entry in anticipated_shows:
        if added_shows >= MAX_NEW_ANTICIPATED_PER_RUN:
            break
        cand_id = ((entry.get('show') or {}).get('ids') or {}).get('tmdb')
        if not cand_id or cand_id in seen_show_ids:
            continue
        try:
            details = tmdb.get_tv(cand_id)
        except Exception as e:
            log.warning('generate_anticipated: tmdb detail fetch failed for show %s — %s', cand_id, e)
            continue
        DiscoverItem.objects.create(
            tmdb_id=cand_id,
            media_type=DiscoverItem.MediaType.TV,
            title=details.get('name', ''),
            overview=details.get('overview', ''),
            poster_path=details.get('poster_path') or '',
            backdrop_path=details.get('backdrop_path') or '',
            release_date=_parse_date(details.get('first_air_date')),
            vote_average=details.get('vote_average', 0),
            genres=', '.join(g['name'] for g in details.get('genres', [])),
            source=DiscoverItem.Source.ANTICIPATED,
        )
        seen_show_ids.add(cand_id)
        added_shows += 1

    return f'added {added_movies} movies, {added_shows} shows (anticipated)'
