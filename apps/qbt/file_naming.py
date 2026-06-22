import os
import re


def sanitise(name):
    return re.sub(r'\s+', ' ', re.sub(r'[<>:"/\\|?*]', '', name or '')).strip().rstrip('. ') or 'Unknown'


def _clean(raw):
    return re.sub(r'\s+', ' ', re.sub(r'[._\-]+', ' ', raw)).strip().strip(' -_')


def _strip_ext(name):
    return re.sub(r'\.(mkv|mp4|avi|mov|m4v|wmv|ts|m2ts|mpg|mpeg|webm|flv)$', '', name, flags=re.IGNORECASE)


def _strip_watermark(name):
    return re.sub(r'^(?:www\.)?[\w-]+\.(?:com|org|net|info|to|xyz|me)\s*[-–]\s*', '', name, flags=re.IGNORECASE)


def parse_tv(name):
    """Return (show_name, year_or_None, season_num, ep_num_or_None)."""
    name = _strip_watermark(_strip_ext(name))
    m = re.search(r'[Ss](\d{1,2})[Ee](\d+)', name)
    if m:
        prefix = name[:m.start()]
        season = int(m.group(1))
        ep = int(m.group(2))
        ym = re.search(r'\((\d{4})\)', prefix)
        if ym:
            return sanitise(_clean(prefix[:ym.start()])) or 'Unknown Show', int(ym.group(1)), season, ep
        ym = re.search(r'(?<=[. _])(\d{4})(?=[. _]|$)', prefix)
        if ym:
            return sanitise(_clean(prefix[:ym.start()])) or 'Unknown Show', int(ym.group(1)), season, ep
        return sanitise(_clean(prefix)) or 'Unknown Show', None, season, ep
    m = re.search(r'[Ss]eason\s*(\d+)|[Ss](\d{2})(?:\b|$)', name)
    if m:
        season = int(m.group(1) or m.group(2))
        prefix = name[:m.start()]
        ym = re.search(r'\((\d{4})\)', prefix)
        year = int(ym.group(1)) if ym else None
        raw = prefix[:ym.start()] if ym else prefix
        return sanitise(_clean(raw)) or 'Unknown Show', year, season, None
    return sanitise(_clean(name)) or 'Unknown Show', None, 1, None


def parse_movie(name):
    """Return (title, year_or_None)."""
    name = _strip_watermark(_strip_ext(name))
    m = re.search(r'\((\d{4})\)', name)
    if m:
        return sanitise(_clean(name[:m.start()])), int(m.group(1))
    m = re.search(r'(?<=[. _])(\d{4})(?=[. _]|$)', name)
    if m:
        return sanitise(_clean(name[:m.start()])), int(m.group(1))
    return sanitise(_clean(name)), None


def detect_type(path):
    """Heuristic: TV if any filename contains SxxExx pattern."""
    if os.path.isfile(path):
        return 'tv' if re.search(r'[Ss]\d+[Ee]\d+', os.path.basename(path)) else 'movie'
    for _root, _dirs, files in os.walk(path):
        for f in files:
            if re.search(r'[Ss]\d+[Ee]\d+', f):
                return 'tv'
    return 'movie'


def raw_dest(src, completed_path, detected_type):
    """Compute a destination path using filename parsing only (no network calls)."""
    basename = os.path.basename(src.rstrip('/\\'))
    base = completed_path.rstrip(os.sep)
    if detected_type == 'movie':
        title, year = parse_movie(basename)
        folder = f'{title} ({year})' if year else title
        return os.path.join(base, folder), title, year, None, None
    show, year, season, ep_num = parse_tv(basename)
    show_folder = f'{show} ({year})' if year else show
    season_folder = f'Season {season:02d}'
    return os.path.join(base, show_folder, season_folder), show, year, season, ep_num


def tmdb_enrich_movie(title, year):
    """Search TMDB for the movie, sync to DB. Returns (movie_obj, proper_title, proper_year)."""
    from apps.media_tracker.tmdb import tmdb
    from apps.media_tracker.models import Movie
    results = tmdb.search_movie(f'{title} {year}' if year else title).get('results', [])
    if not results:
        results = tmdb.search_movie(title).get('results', [])
    if not results:
        return None, title, year
    best = results[0]
    movie = Movie.objects.filter(tmdb_id=best['id']).first() or tmdb.sync_movie_to_db(best['id'])
    proper_year = (best.get('release_date') or '')[:4]
    return movie, best.get('title', title), int(proper_year) if proper_year else year


def tmdb_enrich_tv(show, year, season, ep_num):
    """Search TMDB for the show, sync to DB. Returns (episode_obj, proper_show, proper_year, ep_title)."""
    from apps.media_tracker.tmdb import tmdb
    from apps.media_tracker.models import TVShow, Season as SeasonModel, Episode as EpisodeModel
    results = tmdb.search_tv(f'{show} {year}' if year else show).get('results', [])
    if not results:
        results = tmdb.search_tv(show).get('results', [])
    if not results:
        return None, show, year, None
    best = results[0]
    proper_show = best.get('name', show)
    first_air = (best.get('first_air_date') or '')[:4]
    proper_year = int(first_air) if first_air else year
    show_obj = TVShow.objects.filter(tmdb_id=best['id']).first() or tmdb.sync_show_to_db(best['id'])
    ep_obj = ep_title = None
    if ep_num is not None and season is not None:
        season_obj = SeasonModel.objects.filter(show=show_obj, season_number=season).first()
        if not season_obj:
            tmdb.sync_show_to_db(best['id'])
            season_obj = SeasonModel.objects.filter(show=show_obj, season_number=season).first()
        if season_obj:
            ep_obj = EpisodeModel.objects.filter(season=season_obj, episode_number=ep_num).first()
            if ep_obj:
                ep_title = ep_obj.name or None
    return ep_obj, proper_show, proper_year, ep_title
