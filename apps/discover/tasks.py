"""
The For You deck. Two nightly jobs fill it — both can also be run from
App Settings → For You:

* generate_recommendations: TMDB's "recommendations" and "similar" lists for
  every show and movie in Sonarr/Radarr, scored by how many of *your* titles
  point to each candidate (and how near the top of their lists it is). A pick
  reads "Because you like Breaking Bad, Ozark and Narcos" instead of hanging
  off whichever library title happened to be looked at first.
* generate_anticipated: TMDB's trending / new / coming soon / popular lists,
  a share of the deck.

Each job replaces its part of the deck, so the deck always holds the current
best picks and never more than DiscoverSettings.deck_size cards.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from celery import shared_task

from apps.media_tracker.tmdb import tmdb, _parse_date
from .genres import genre_names

log = logging.getLogger('daredevil.discover.tasks')

# TMDB's TV lists are dominated by daily shows — News, Reality, Soap, Talk
SKIP_TV_GENRES = {10763, 10764, 10766, 10767}
MIN_VOTES = 20          # skip obscure titles (upcoming ones excepted — they can't have votes yet)
TRENDING_SHARE = 0.3    # of the deck, when trending picks are on
LIST_DEPTH = 20         # how far down each TMDB recommendation list to look


def _card(r, media_type, source, source_title, because=(), score=0.0):
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
        because=list(because),
        score=score,
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
        log.warning('For You: could not read the %s library — %s', kind, e)
        return []
    return [(i['tmdbId'], i.get('title') or '') for i in items if i.get('tmdbId')]


def _decided(media_type):
    from .models import DiscoverDecision
    return set(DiscoverDecision.objects.filter(media_type=media_type).values_list('tmdb_id', flat=True))


def _replace(source, cards):
    """Make `cards` [(media_type, tmdb_id, fields)] this source's part of the
    deck: update the cards already there, add new ones, drop the rest.
    Returns how many were dropped."""
    from .models import DiscoverItem
    keep = set()
    for media_type, tmdb_id, fields in cards:
        item, _ = DiscoverItem.objects.update_or_create(tmdb_id=tmdb_id, media_type=media_type, defaults=fields)
        keep.add(item.pk)
    return DiscoverItem.objects.filter(source=source).exclude(pk__in=keep).delete()[0]


def _wanted(r, media_type):
    if media_type == 'tv' and SKIP_TV_GENRES & set(r.get('genre_ids') or []):
        return False
    return (r.get('vote_count') or 0) >= MIN_VOTES


def _recommendations_for(media_type, tmdb_id):
    """[(TMDB result, weight)] for one library title. Higher up a list counts
    for more, and "recommended" counts for more than merely "similar"."""
    if media_type == 'movie':
        fetchers = ((tmdb.get_movie_recommendations, 1.0), (tmdb.get_movie_similar, 0.6))
    else:
        fetchers = ((tmdb.get_tv_recommendations, 1.0), (tmdb.get_tv_similar, 0.6))
    out = []
    for fetch, base in fetchers:
        try:
            results = fetch(tmdb_id).get('results', [])
        except Exception as e:
            log.warning('For You: %s %s — %s', media_type, tmdb_id, e)
            continue
        out.extend((r, base * (1 - rank / (2 * LIST_DEPTH))) for rank, r in enumerate(results[:LIST_DEPTH]))
    return out


def rank_recommendations(media_type, library, excluded, min_matches):
    """[(tmdb_id, result, because, score)], best first: most of your titles
    pointing to it, then how strongly they do."""
    candidates = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        lists = pool.map(lambda item: _recommendations_for(media_type, item[0]), library)
        for (_lib_id, lib_title), recs in zip(library, lists):
            for r, weight in recs:
                cid = r.get('id')
                if not cid or cid in excluded or not _wanted(r, media_type):
                    continue
                entry = candidates.setdefault(cid, {'r': r, 'because': {}})
                # The same title on both of one library item's lists counts once
                entry['because'][lib_title] = max(weight, entry['because'].get(lib_title, 0))
    ranked = []
    for cid, entry in candidates.items():
        if len(entry['because']) < min_matches:
            continue
        because = sorted(entry['because'], key=entry['because'].get, reverse=True)
        score = sum(entry['because'].values()) + (entry['r'].get('vote_average') or 0) / 20
        ranked.append((cid, entry['r'], because, round(score, 3)))
    ranked.sort(key=lambda c: (len(c[2]), c[3]), reverse=True)
    return ranked


@shared_task(name='generate_recommendations')
def generate_recommendations():
    from .models import DiscoverItem, DiscoverSettings

    cfg = DiscoverSettings.get()
    budget = cfg.deck_size - (math.floor(cfg.deck_size * TRENDING_SHARE) if cfg.include_trending else 0)
    picks, library_size = [], 0
    for media_type in cfg.media_types():
        library = _library(media_type)
        library_size += len(library)
        excluded = {tmdb_id for tmdb_id, _ in library} | _decided(media_type)
        picks += [(media_type, *c) for c in rank_recommendations(media_type, library, excluded, cfg.min_matches)]
    picks.sort(key=lambda p: (len(p[3]), p[4]), reverse=True)
    picks = picks[:max(budget, 0)]
    dropped = _replace(DiscoverItem.Source.RECOMMENDATION, [
        (media_type, cid, _card(r, media_type, DiscoverItem.Source.RECOMMENDATION, because[0], because, score))
        for media_type, cid, r, because, score in picks
    ])
    movies = sum(1 for p in picks if p[0] == 'movie')
    return (f'{len(picks)} recommendations ({movies} movies, {len(picks) - movies} shows) from {library_size} '
            f'library titles, each matching at least {cfg.min_matches}; {dropped} older ones dropped')


@shared_task(name='generate_anticipated')
def generate_anticipated():
    """TMDB's trending, new, coming soon and popular lists, taken from in
    turn so no one list crowds out the others."""
    from .models import DiscoverItem, DiscoverSettings

    cfg = DiscoverSettings.get()
    if not cfg.include_trending:
        return f'trending picks are off — {_replace(DiscoverItem.Source.ANTICIPATED, [])} removed'

    recs = DiscoverItem.objects.filter(source=DiscoverItem.Source.RECOMMENDATION)
    budget = max(cfg.deck_size - recs.count(), math.floor(cfg.deck_size * TRENDING_SHARE))
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

    queues = []  # one queue of (media_type, label, result) per list
    for media_type in cfg.media_types():
        seen = ({tmdb_id for tmdb_id, _ in _library(media_type)} | _decided(media_type)
                | set(recs.filter(media_type=media_type).values_list('tmdb_id', flat=True)))
        for label, fetch in lists[media_type]:
            try:
                results = fetch().get('results', [])
            except Exception as e:
                log.warning('For You: %s %s — %s', media_type, label, e)
                continue
            queue = []
            for r in results:
                if not r.get('id') or r['id'] in seen:
                    continue
                if label == 'Coming Soon':
                    # TMDB's upcoming list includes re-releases of old films
                    released = _parse_date(r.get('release_date') or r.get('first_air_date'))
                    if released and released < today:
                        continue
                    if media_type == 'tv' and SKIP_TV_GENRES & set(r.get('genre_ids') or []):
                        continue
                elif not _wanted(r, media_type):
                    continue
                queue.append((media_type, label, r))
            queues.append(queue)

    cards, taken = [], set()
    while len(cards) < budget and any(queues):
        for queue in queues:
            while queue:
                media_type, label, r = queue.pop(0)
                if (media_type, r['id']) not in taken:
                    taken.add((media_type, r['id']))
                    # Earlier picks rank higher in the deck
                    cards.append((media_type, r['id'], _card(r, media_type, DiscoverItem.Source.ANTICIPATED, label,
                                                             score=round(1 - len(cards) / max(budget, 1), 3))))
                    break
            if len(cards) >= budget:
                break
    dropped = _replace(DiscoverItem.Source.ANTICIPATED, cards)
    return f'{len(cards)} trending / new / coming soon / popular picks; {dropped} older ones dropped'


@shared_task(name='rebuild_for_you')
def rebuild_for_you():
    """Both of the above, in the order the deck's budget expects."""
    return f'{generate_recommendations()} · {generate_anticipated()}'
