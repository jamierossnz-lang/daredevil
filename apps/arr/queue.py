"""
The download queue as Sonarr and Radarr see it, merged with qBittorrent's
live transfer stats. Sonarr/Radarr own the downloads now (they pick the
release, send it to qBittorrent, and import it when done) — this module only
reads that state for the Queue page and its live stream.
"""
import logging
import re

from .clients import ArrError
from .services import fmt_bytes, image_url, parse_dt, radarr, sonarr, is_configured

log = logging.getLogger('daredevil.arr.queue')

# trackedDownloadState → (phase used for badges/grouping, human label)
_STATE_LABELS = {
    'downloading': ('downloading', 'Downloading'),
    'importBlocked': ('warning', 'Import blocked'),
    'importPending': ('importing', 'Waiting to import'),
    'importing': ('importing', 'Importing'),
    'imported': ('imported', 'Imported'),
    'failedPending': ('failed', 'Failed — pending'),
    'failed': ('failed', 'Failed'),
    'ignored': ('warning', 'Ignored'),
}


def _phase(record, torrent):
    status = (record.get('status') or '').lower()
    state = record.get('trackedDownloadState') or ''
    tracked = record.get('trackedDownloadStatus') or 'ok'

    if status == 'delay':
        return 'delayed', 'Delayed — waiting for a better release'
    if status == 'downloadclientunavailable':
        return 'warning', 'qBittorrent unavailable'
    if state in _STATE_LABELS and state != 'downloading':
        return _STATE_LABELS[state]
    if tracked == 'error' or status == 'failed':
        return 'failed', 'Failed'
    if tracked == 'warning' or status == 'warning':
        return 'warning', 'Needs attention'
    if status == 'paused' or (torrent and str(torrent.get('state', '')).startswith(('paused', 'stopped'))):
        return 'paused', 'Paused'
    if status == 'queued':
        return 'queued', 'Queued in qBittorrent'
    if torrent and torrent.get('state') in ('stalledDL', 'metaDL', 'forcedMetaDL'):
        return 'stalled', 'Stalled — looking for peers' if torrent.get('state') == 'stalledDL' else 'Fetching metadata'
    if status == 'completed':
        return 'importing', 'Download complete — importing'
    return 'downloading', 'Downloading'


def _messages(record):
    out = []
    for sm in record.get('statusMessages') or []:
        for m in sm.get('messages') or []:
            out.append(m)
        if not sm.get('messages') and sm.get('title'):
            out.append(sm['title'])
    if record.get('errorMessage'):
        out.append(record['errorMessage'])
    return list(dict.fromkeys(out))


def _episode_label(episodes):
    """[(season, episode), …] → 'S01E01–E08' / 'S01E03' / 'S01E01, S02E04'."""
    episodes = sorted(set(episodes))
    if not episodes:
        return ''
    if len(episodes) == 1:
        s, e = episodes[0]
        return f'S{s:02d}E{e:02d}'
    seasons = {s for s, _ in episodes}
    if len(seasons) == 1:
        s = seasons.pop()
        return f'S{s:02d}E{episodes[0][1]:02d}–E{episodes[-1][1]:02d} ({len(episodes)} episodes)'
    return f'{len(episodes)} episodes'


_QBT_BACKOFF_KEY = 'arr_queue_qbt_backoff'
def _qbt_torrents():
    """({hash: torrent-dict}, True) from qBittorrent, or ({}, reason) if it's unreachable."""
    from django.core.cache import cache

    reason = cache.get(_QBT_BACKOFF_KEY)
    if reason:
        return {}, reason
    from apps.qbt.client import get_client, reset_client
    try:
        torrents = get_client().torrents_info()
    except Exception as e:
        reset_client()
        reason = str(e) or type(e).__name__
        # The live queue polls every few seconds. qBittorrent bans an IP after
        # 5 failed logins, so wrong credentials must not be retried each tick:
        # back off for 5 minutes on auth problems, 30 seconds otherwise.
        auth_problem = any(w in reason.lower() for w in ('banned', 'credentials', 'forbidden', 'login', 'unauthorized'))
        cache.set(_QBT_BACKOFF_KEY, f'qBittorrent: {reason}', 300 if auth_problem else 30)
        log.warning('queue: qBittorrent unavailable (%s) — pausing qBittorrent calls for %ss',
                    reason, 300 if auth_problem else 30)
        return {}, f'qBittorrent: {reason}'
    out = {}
    for t in torrents:
        out[t.hash.lower()] = {
            'state': t.state,
            'dlspeed': t.dlspeed,
            'upspeed': t.upspeed,
            'progress': round((t.progress or 0) * 100, 1),
            'seeds': t.num_seeds,
            'peers': t.num_leechs,
            'ratio': round(t.ratio or 0, 2),
            'eta': t.eta,
            'category': t.category,
        }
    return out, True


def fmt_speed(bps):
    return f'{fmt_bytes(bps)}/s' if bps else '—'


def fmt_eta(seconds):
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return '—'
    if seconds <= 0 or seconds >= 8640000:
        return '—'
    if seconds >= 86400:
        return f'{seconds // 86400}d {(seconds % 86400) // 3600}h'
    if seconds >= 3600:
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m'
    if seconds >= 60:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds}s'


def _timeleft_seconds(value):
    """Sonarr/Radarr 'timeleft' is a .NET TimeSpan string: [d.]hh:mm:ss"""
    if not value:
        return None
    m = re.match(r'^(?:(\d+)\.)?(\d+):(\d+):(\d+)', str(value))
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def build_queue():
    """
    Returns (items, status) where items is one entry per *download* (a
    season pack is many queue records in Sonarr but one torrent) and status
    reports which services answered.
    """
    groups = {}
    status = {'sonarr': None, 'radarr': None, 'qbittorrent': None}

    torrents, qbt_status = _qbt_torrents()
    status['qbittorrent'] = qbt_status

    if is_configured('sonarr'):
        try:
            data = sonarr().queue(includeSeries='true', includeEpisode='true', includeUnknownSeriesItems='true')
            status['sonarr'] = True
            for r in data.get('records', []):
                _add_record(groups, 'sonarr', r, torrents)
        except ArrError as e:
            status['sonarr'] = str(e)
    if is_configured('radarr'):
        try:
            data = radarr().queue(includeMovie='true', includeUnknownMovieItems='true')
            status['radarr'] = True
            for r in data.get('records', []):
                _add_record(groups, 'radarr', r, torrents)
        except ArrError as e:
            status['radarr'] = str(e)

    items = []
    for g in groups.values():
        g['subtitle'] = _episode_label(g.pop('_episodes')) if g['app'] == 'sonarr' else g['subtitle']
        items.append(g)
    order = {'failed': 0, 'warning': 1, 'importing': 2, 'downloading': 3, 'stalled': 4, 'paused': 5, 'queued': 6, 'delayed': 7, 'imported': 8}
    items.sort(key=lambda g: (order.get(g['phase'], 9), -(g['progress'] or 0)))
    return items, status


def _add_record(groups, app, r, torrents):
    download_id = (r.get('downloadId') or '').lower()
    key = f'{app}:{download_id or r.get("id")}'
    torrent = torrents.get(download_id) if download_id else None
    g = groups.get(key)
    if g is None:
        size = r.get('size') or 0
        sizeleft = r.get('sizeleft') or 0
        progress = round((size - sizeleft) * 100 / size, 1) if size else 0
        if torrent:
            progress = torrent['progress']
        phase, phase_label = _phase(r, torrent)
        eta = torrent['eta'] if torrent else _timeleft_seconds(r.get('timeleft'))
        quality = ((r.get('quality') or {}).get('quality') or {}).get('name', '')
        if app == 'sonarr':
            series = r.get('series') or {}
            title = series.get('title') or r.get('title') or 'Unknown'
            poster = image_url(series.get('images'), 'poster')
            link = f'/shows/{r["seriesId"]}/' if r.get('seriesId') else None
            subtitle = ''
        else:
            movie = r.get('movie') or {}
            title = movie.get('title') or r.get('title') or 'Unknown'
            poster = image_url(movie.get('images'), 'poster', size='w342')
            link = f'/movies/{r["movieId"]}/' if r.get('movieId') else None
            subtitle = str(movie.get('year') or '')
        g = {
            'key': key,
            'app': app,
            'ids': [],
            'title': title,
            'subtitle': subtitle,
            'release': r.get('title') or '',
            'poster': poster,
            'link': link,
            'series_id': r.get('seriesId'),
            'movie_id': r.get('movieId'),
            'episode_ids': [],
            'phase': phase,
            'phase_label': phase_label,
            'status': r.get('status'),
            'state': r.get('trackedDownloadState'),
            'messages': _messages(r),
            'size': size,
            'size_fmt': fmt_bytes(size),
            'done_fmt': fmt_bytes(size - sizeleft) if size else '—',
            'progress': progress,
            'eta': fmt_eta(eta),
            'eta_seconds': eta,
            'estimated': r.get('estimatedCompletionTime'),
            'quality': quality,
            'indexer': (r.get('indexer') or '').replace(' (Prowlarr)', ''),
            'protocol': r.get('protocol') or '',
            'download_client': r.get('downloadClient') or '',
            'hash': download_id,
            'added': r.get('added'),
            'speed': fmt_speed(torrent['dlspeed']) if torrent else '—',
            'speed_bps': torrent['dlspeed'] if torrent else 0,
            'seeds': torrent['seeds'] if torrent else None,
            'peers': torrent['peers'] if torrent else None,
            'qbt_state': torrent['state'] if torrent else None,
            'in_qbt': torrent is not None,
            '_episodes': [],
        }
        groups[key] = g
    g['ids'].append(r['id'])
    if app == 'sonarr':
        ep = r.get('episode') or {}
        if ep.get('id'):
            g['episode_ids'].append(ep['id'])
        if ep.get('seasonNumber') is not None and ep.get('episodeNumber') is not None:
            g['_episodes'].append((ep['seasonNumber'], ep['episodeNumber']))
        elif r.get('seasonNumber') is not None:
            g['_episodes'].append((r['seasonNumber'], 0))
    added = parse_dt(r.get('added'))
    if added:
        g['added_dt'] = added.isoformat()


def queue_summary():
    """Cheap counts for the dashboard / nav badges."""
    items, _ = build_queue()
    return {
        'total': len(items),
        'downloading': sum(1 for i in items if i['phase'] in ('downloading', 'stalled', 'queued')),
        'attention': sum(1 for i in items if i['phase'] in ('warning', 'failed')),
    }
