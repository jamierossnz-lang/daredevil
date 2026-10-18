"""
Folder browsing for the path pickers. Each app sees its own container's
filesystem (Sonarr's /tv isn't Daredevil's /media/tv), so a picker browses
the filesystem of whichever app the path is *for*:

  sonarr / radarr → their /api/v3/filesystem endpoint
  qbittorrent     → app/getDirectoryContent (qBittorrent 5+)
  daredevil       → this container's own disk
"""
import os
import posixpath

from .clients import ArrError

SOURCES = ('sonarr', 'radarr', 'qbittorrent', 'daredevil')
_DEFAULT_START = {'sonarr': '/', 'radarr': '/', 'qbittorrent': '/', 'daredevil': '/media'}


def _norm(path):
    path = (path or '').strip() or '/'
    if not path.startswith('/'):
        path = '/' + path
    path = posixpath.normpath(path)
    return path


def _parent(path):
    return None if path == '/' else (posixpath.dirname(path) or '/')


def browse(source, path=None):
    """{'path', 'parent', 'dirs': [{'name', 'path'}]} — directories only."""
    if source not in SOURCES:
        raise ArrError('Unknown source')
    path = _norm(path or _DEFAULT_START[source])

    if source in ('sonarr', 'radarr'):
        from .services import client
        # `path` is also get()'s own first argument, so the query goes via _request
        data = client(source)._request('GET', '/filesystem', params={
            'path': path if path.endswith('/') else path + '/',
            'includeFiles': 'false', 'allowFoldersWithoutTrailingSlashes': 'true',
        }) or {}
        dirs = [{'name': d.get('name') or posixpath.basename(d['path'].rstrip('/')),
                 'path': _norm(d['path'])} for d in data.get('directories', [])]
    elif source == 'qbittorrent':
        from apps.qbt.client import get_client
        try:
            entries = get_client().app_get_directory_content(directory_path=path, with_metadata=True)
        except Exception as e:
            raise ArrError(f'qBittorrent: {e or type(e).__name__}')
        dirs = [{'name': e['name'], 'path': _norm(posixpath.join(path, e['name']))}
                for e in entries if e.get('type') == 'dir']
    else:
        try:
            with os.scandir(path) as it:
                dirs = [{'name': e.name, 'path': _norm(e.path)}
                        for e in it if e.is_dir(follow_symlinks=True) and not e.name.startswith('.')]
        except FileNotFoundError:
            raise ArrError(f'{path} does not exist inside Daredevil')
        except PermissionError:
            raise ArrError(f'No permission to read {path}')

    dirs.sort(key=lambda d: d['name'].lower())
    return {'source': source, 'path': path, 'parent': _parent(path), 'dirs': dirs}


def visible_to(source, path):
    """
    Whether `path` exists for `source` — or, for a folder that hasn't been
    created yet, whether its nearest existing parent (below /) does. The
    browse endpoints can't tell a missing folder from an empty one, so this
    looks each folder up in its parent's listing.
    """
    path = _norm(path)
    while path != '/':
        parent = _parent(path)
        try:
            listing = browse(source, parent)
        except ArrError:
            return False
        if any(d['path'] == path for d in listing['dirs']):
            return True
        path = parent
    return False
