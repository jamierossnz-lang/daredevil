"""
Calendar entries from Sonarr (episodes, at their air time) and Radarr
(movies, on their cinema / digital / disc release dates) — shared by the
Calendar page and the subscribable .ics feed.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.utils import timezone

from apps.arr.clients import ArrError
from apps.arr.services import image_url, in_thread, is_configured, parse_dt, radarr, sonarr

log = logging.getLogger('daredevil.calendar')

MOVIE_RELEASES = (
    ('inCinemas', 'In cinemas'),
    ('digitalRelease', 'Digital release'),
    ('physicalRelease', 'Disc release'),
)


def _status(has_file, monitored, available):
    if has_file:
        return 'downloaded'
    if not monitored:
        return 'unmonitored'
    return 'missing' if available else 'upcoming'


def _episodes(start, end, monitored_only):
    now = timezone.now()
    out = []
    for ep in sonarr().calendar(start.isoformat(), end.isoformat(), includeSeries='true',
                                unmonitored='false' if monitored_only else 'true'):
        air = parse_dt(ep.get('airDateUtc'))
        if not air:
            continue
        series = ep.get('series') or {}
        code = f'S{ep.get("seasonNumber", 0):02d}E{ep.get("episodeNumber", 0):02d}'
        runtime = ep.get('runtime') or series.get('runtime') or 30
        out.append({
            'uid': f'sonarr-episode-{ep["id"]}',
            'app': 'sonarr',
            'kind': 'episode',
            'title': series.get('title') or 'Unknown show',
            'subtitle': f'{code} · {ep.get("title") or "TBA"}',
            'start': air,
            'end': air + timedelta(minutes=runtime),
            'all_day': False,
            'status': _status(ep.get('hasFile'), ep.get('monitored'), air <= now),
            'poster': image_url(series.get('images'), 'poster', size='w342'),
            'link': f'/shows/{ep["seriesId"]}/',
            'network': series.get('network') or '',
            'overview': ep.get('overview') or '',
        })
    return out


def _movies(start, end, monitored_only):
    first, last = timezone.localtime(start).date(), timezone.localtime(end).date()
    out = []
    for m in radarr().calendar(start.isoformat(), end.isoformat(),
                               unmonitored='false' if monitored_only else 'true'):
        for key, label in MOVIE_RELEASES:
            when = parse_dt(m.get(key))
            if not when:
                continue
            # Radarr stores release *dates*, as midnight UTC
            day = when.date()
            if not first <= day <= last:
                continue
            year = m.get('year')
            out.append({
                'uid': f'radarr-movie-{m["id"]}-{key}',
                'app': 'radarr',
                'kind': 'movie',
                'title': f'{m.get("title") or "Unknown movie"}{f" ({year})" if year else ""}',
                'subtitle': label,
                'start': day,
                'end': day + timedelta(days=1),
                'all_day': True,
                'status': _status(m.get('hasFile'), m.get('monitored'), m.get('isAvailable')),
                'poster': image_url(m.get('images'), 'poster', size='w342'),
                'link': f'/movies/{m["id"]}/',
                'network': m.get('studio') or '',
                'overview': m.get('overview') or '',
            })
    return out


def local_day(event):
    return event['start'] if event['all_day'] else timezone.localtime(event['start']).date()


def calendar_events(start, end, tv=True, movies=True, monitored_only=False):
    """([events sorted by day, all-day first, then time], [errors]) between
    two aware datetimes."""
    jobs = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if tv and is_configured('sonarr'):
            jobs['Sonarr'] = pool.submit(in_thread, _episodes, start, end, monitored_only)
        if movies and is_configured('radarr'):
            jobs['Radarr'] = pool.submit(in_thread, _movies, start, end, monitored_only)
        events, errors = [], []
        for name, job in jobs.items():
            try:
                events.extend(job.result())
            except ArrError as e:
                log.info('calendar: %s — %s', name, e)
                errors.append(f'{name}: {e}')
    events.sort(key=lambda e: (local_day(e), not e['all_day'], '' if e['all_day'] else e['start'].isoformat(), e['title']))
    return events, errors
