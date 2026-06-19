import re
import logging
from django.conf import settings

log = logging.getLogger('daredevil.plex')


def get_plex():
    url = getattr(settings, 'PLEX_URL', '')
    token = getattr(settings, 'PLEX_TOKEN', '')
    if not url or not token:
        return None
    try:
        from plexapi.server import PlexServer
        return PlexServer(url, token, timeout=10)
    except Exception as e:
        log.warning('Plex connection failed: %s', e)
        return None


def extract_tmdb_id(item):
    """Extract TMDB ID string from a plexapi media item's guids."""
    try:
        for g in (item.guids or []):
            gid = g.id if hasattr(g, 'id') else str(g)
            if gid.startswith('tmdb://'):
                return gid.split('://')[-1].split('?')[0]
    except Exception:
        pass
    # Old-style single guid string
    try:
        guid = item.guid or ''
        if 'themoviedb' in guid or 'tmdb' in guid:
            m = re.search(r'//(\d+)', guid)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _file_size(item):
    total = 0
    try:
        for media in (item.media or []):
            for part in (media.parts or []):
                total += part.size or 0
    except Exception:
        pass
    return total


def _fmt_bytes(b):
    if not b:
        return ''
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} TB'


def _base_dict(item, thumb_path=None):
    size = _file_size(item)
    return {
        'title': item.title,
        'year': getattr(item, 'year', '') or '',
        'thumb': thumb_path or item.thumb or '',
        'viewed': bool(getattr(item, 'viewCount', 0)),
        'view_count': getattr(item, 'viewCount', 0) or 0,
        'last_viewed': getattr(item, 'lastViewedAt', None),
        'added_at': getattr(item, 'addedAt', None),
        'tmdb_id': extract_tmdb_id(item),
        'rating_key': str(item.ratingKey),
        'size_bytes': size,
        'size_display': _fmt_bytes(size),
        'detail_url': None,  # filled in by view
    }


def movie_to_dict(m):
    d = _base_dict(m)
    d['type'] = 'movie'
    return d


def show_to_dict(s):
    total = s.leafCount or 0
    watched = s.viewedLeafCount or 0
    d = _base_dict(s)
    d.update({
        'type': 'show',
        'total_episodes': total,
        'view_count': watched,
        'viewed': watched == total and total > 0,
    })
    return d


def season_to_dict(s):
    """Convert a season item (from recentlyAdded) to a show-like dict."""
    return {
        'title': s.parentTitle or s.title,
        'subtitle': s.title,  # e.g. "Season 1"
        'year': getattr(s, 'parentYear', '') or '',
        'thumb': s.parentThumb or s.thumb or '',
        'viewed': False,
        'view_count': 0,
        'last_viewed': None,
        'added_at': getattr(s, 'addedAt', None),
        'tmdb_id': None,  # season TMDB IDs don't match show IDs
        'rating_key': str(s.ratingKey),
        'size_bytes': 0,
        'size_display': '',
        'type': 'show',
        'detail_url': None,
        'parent_title': (s.parentTitle or '').lower(),
    }


def episode_to_dict(e):
    return {
        'title': e.grandparentTitle or e.title,
        'subtitle': f'S{(e.parentIndex or 0):02d}E{(e.index or 0):02d} · {e.title}',
        'year': '',
        'thumb': e.thumb or e.grandparentThumb or '',
        'viewed': bool(getattr(e, 'viewCount', 0)),
        'view_count': getattr(e, 'viewCount', 0) or 0,
        'last_viewed': getattr(e, 'lastViewedAt', None),
        'added_at': getattr(e, 'addedAt', None),
        'tmdb_id': None,
        'rating_key': str(e.ratingKey),
        'size_bytes': _file_size(e),
        'size_display': _fmt_bytes(_file_size(e)),
        'type': 'episode',
        'detail_url': None,
        'parent_title': (e.grandparentTitle or '').lower(),
    }


def item_to_dict(item):
    t = item.type
    if t == 'movie':
        return movie_to_dict(item)
    if t == 'show':
        return show_to_dict(item)
    if t == 'season':
        return season_to_dict(item)
    if t == 'episode':
        return episode_to_dict(item)
    return None
