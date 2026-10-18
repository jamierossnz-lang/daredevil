import logging
from datetime import date, timedelta

from celery import shared_task

from apps.media_tracker.tmdb import tmdb, _parse_date
from .genres import genre_names

log = logging.getLogger('daredevil.discover.tasks')

# Caps keep each nightly run fast and stop the pool growing unbounded —
# the deck is meant to be a curated trickle, not a dump of everything TMDB
# knows about.
RECS_PER_LIBRARY_ITEM = 6
MAX_NEW_RECS_PER_RUN = 25
MAX_PER_LIST = 8  # ×4 lists ×2 media types
# TMDB's TV lists are dominated by daily shows — News, Reality, Soap, Talk
SKIP_TV_GENRES = {10763, 10764, 10766, 10767}
MIN_VOTES = 20  # skip obscure titles (except upcoming ones, which can't have votes yet)


def _card(r, media_type, source, source_title):
    """DiscoverItem fields from a TMDB list result (movies use title /
    release_date, shows name / first_air_date)."""
    is_movie = media_type == 'movie'
    return dict(
        title=r.get('title' if is_movie else 'name', ''),
        overview=r.get('overview', ''),
        poster_path=r.get('poster_path') or '',
        backdrop_path=r.get('backdrop_path') or '',
        release_date=_parse_date(r.get('release_date' if is_movie else 'first_air_date')),
        vote_average=r.get('vote_average', 0),
        genres=genre_names(r.get('genre_ids'), media_type),
        source=source,
        source_title=source_title,
    )


def _library(media_type):
    """[(tmdb_id, title)] for everything in Radarr (movies) / Sonarr (shows)."""
    from apps.arr.clients import ArrError
    from apps.arr.services import is_configured, radarr, sonarr

    kind = 'radarr' if media_type == 'movie' else 'sonarr'
    if not is_configured(kind):
        return []
    try:
        items = radarr().movies() if kind == 'radarr' else sonarr().series_list()
    except ArrError as e:
        log.warning('generate_recommendations: could not read %s library — %s', kind, e)
        return []
    return [(i['tmdbId'], i.get('title') or '') for i in items if i.get('tmdbId')]


def _existing_and_decided(media_type, library=None):
    """tmdb_ids already in the library, already in the pool, or already swiped on."""
    from .models import DiscoverItem, DiscoverDecision

    library_ids = {tmdb_id for tmdb_id, _ in (library if library is not None else _library(media_type))}
    pool_ids = set(DiscoverItem.objects.filter(media_type=media_type).values_list('tmdb_id', flat=True))
    decided_ids = set(DiscoverDecision.objects.filter(media_type=media_type).values_list('tmdb_id', flat=True))
    return library_ids | pool_ids | decided_ids


@shared_task(name='generate_recommendations')
def generate_recommendations():
    """
    Nightly: for every show/movie already in Sonarr/Radarr, pull TMDB's
    'recommendations' and 'similar' lists and add any title not already in
    the library/pool/swipe-history to the discovery deck. This is the
    'because you like X' half of the For You feed.
    """
    from .models import DiscoverItem

    library_movies = _library('movie')
    library_shows = _library('tv')
    seen_movie_ids = _existing_and_decided('movie', library_movies)
    seen_show_ids = _existing_and_decided('tv', library_shows)
    added_movies = 0
    added_shows = 0

    for movie_tmdb_id, movie_title in library_movies:
        if added_movies >= MAX_NEW_RECS_PER_RUN:
            break
        for fetcher in (tmdb.get_movie_recommendations, tmdb.get_movie_similar):
            if added_movies >= MAX_NEW_RECS_PER_RUN:
                break
            try:
                results = fetcher(movie_tmdb_id).get('results', [])
            except Exception as e:
                log.warning('generate_recommendations: movie %s fetch failed — %s', movie_tmdb_id, e)
                continue
            for r in results[:RECS_PER_LIBRARY_ITEM]:
                cand_id = r.get('id')
                if not cand_id or cand_id in seen_movie_ids:
                    continue
                DiscoverItem.objects.get_or_create(
                    tmdb_id=cand_id,
                    media_type=DiscoverItem.MediaType.MOVIE,
                    defaults=_card(r, 'movie', DiscoverItem.Source.RECOMMENDATION, movie_title),
                )
                seen_movie_ids.add(cand_id)
                added_movies += 1
                if added_movies >= MAX_NEW_RECS_PER_RUN:
                    break

    for show_tmdb_id, show_title in library_shows:
        if added_shows >= MAX_NEW_RECS_PER_RUN:
            break
        for fetcher in (tmdb.get_tv_recommendations, tmdb.get_tv_similar):
            if added_shows >= MAX_NEW_RECS_PER_RUN:
                break
            try:
                results = fetcher(show_tmdb_id).get('results', [])
            except Exception as e:
                log.warning('generate_recommendations: show %s fetch failed — %s', show_tmdb_id, e)
                continue
            for r in results[:RECS_PER_LIBRARY_ITEM]:
                cand_id = r.get('id')
                if not cand_id or cand_id in seen_show_ids:
                    continue
                DiscoverItem.objects.get_or_create(
                    tmdb_id=cand_id,
                    media_type=DiscoverItem.MediaType.TV,
                    defaults=_card(r, 'tv', DiscoverItem.Source.RECOMMENDATION, show_title),
                )
                seen_show_ids.add(cand_id)
                added_shows += 1
                if added_shows >= MAX_NEW_RECS_PER_RUN:
                    break

    return f'added {added_movies} movies, {added_shows} shows (recommendations)'


@shared_task(name='generate_anticipated')
def generate_anticipated():
    """
    Nightly: four TMDB lists per media type — Trending this week, New
    Releases (movies out digitally or on disc / shows that premiered in the
    last month), Coming Soon (upcoming movies / series premieres) and
    Popular — adding any title not already in the library, the deck or the
    swipe history. Each list gets its own small cap so no single one
    crowds out the others. TMDB list results carry everything a card needs,
    so there's no per-title detail fetch.
    """
    from .models import DiscoverItem

    today = date.today()
    month_ago = (today - timedelta(days=30)).isoformat()
    lists = {
        'movie': [
            ('Trending on TMDB', lambda: tmdb.trending('movie')),
            ('New Release', lambda: tmdb.discover_movie(**{
                'sort_by': 'popularity.desc', 'with_release_type': '4|5',  # digital | physical
                'release_date.gte': month_ago, 'release_date.lte': today.isoformat()})),
            ('Coming Soon', lambda: tmdb.movie_upcoming()),
            ('Popular on TMDB', lambda: tmdb.popular('movie')),
        ],
        'tv': [
            ('Trending on TMDB', lambda: tmdb.trending('tv')),
            ('New Release', lambda: tmdb.discover_tv(**{
                'sort_by': 'popularity.desc', 'first_air_date.gte': month_ago, 'first_air_date.lte': today.isoformat()})),
            ('Coming Soon', lambda: tmdb.discover_tv(**{
                'sort_by': 'popularity.desc', 'first_air_date.gte': (today + timedelta(days=1)).isoformat()})),
            ('Popular on TMDB', lambda: tmdb.popular('tv')),
        ],
    }
    added = {'movie': 0, 'tv': 0}
    for media_type, sources in lists.items():
        seen = _existing_and_decided(media_type)
        for label, fetch in sources:
            try:
                results = fetch().get('results', [])
            except Exception as e:
                log.warning('generate_anticipated: %s %s fetch failed — %s', media_type, label, e)
                continue
            count = 0
            for r in results:
                if count >= MAX_PER_LIST:
                    break
                cand_id = r.get('id')
                if not cand_id or cand_id in seen:
                    continue
                if media_type == 'tv' and SKIP_TV_GENRES & set(r.get('genre_ids') or []):
                    continue
                if label == 'Coming Soon':
                    # TMDB's upcoming list includes re-releases of old films
                    released = _parse_date(r.get('release_date') or r.get('first_air_date'))
                    if released and released < today:
                        continue
                elif (r.get('vote_count') or 0) < MIN_VOTES:
                    continue
                DiscoverItem.objects.get_or_create(
                    tmdb_id=cand_id,
                    media_type=media_type,
                    defaults=_card(r, media_type, DiscoverItem.Source.ANTICIPATED, label),
                )
                seen.add(cand_id)
                count += 1
                added[media_type] += 1

    return f"added {added['movie']} movies, {added['tv']} shows (trending/new/coming soon/popular)"
