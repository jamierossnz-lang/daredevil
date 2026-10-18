import logging
import threading

import qbittorrentapi
from django.conf import settings

log = logging.getLogger('daredevil.qbt')

_local = threading.local()


def get_client():
    """
    A logged-in qBittorrent client, reused per thread until the connection
    settings change (qbittorrent-api logs in again by itself if the session
    expires). Logging in on every call was slow, and with a wrong password
    it got Daredevil banned by qBittorrent.
    """
    key = (settings.QBITTORRENT_HOST, str(settings.QBITTORRENT_PORT),
           settings.QBITTORRENT_USERNAME, settings.QBITTORRENT_PASSWORD)
    cached = getattr(_local, 'client', None)
    if cached and cached[0] == key:
        return cached[1]
    client = qbittorrentapi.Client(
        host=settings.QBITTORRENT_HOST,
        port=settings.QBITTORRENT_PORT,
        username=settings.QBITTORRENT_USERNAME,
        password=settings.QBITTORRENT_PASSWORD,
        REQUESTS_ARGS={'timeout': (5, 30)},
        VERIFY_WEBUI_CERTIFICATE=False,
    )
    client.auth_log_in()
    _local.client = (key, client)
    return client


def reset_client():
    """Forget this thread's client (after an error), so the next call logs in fresh."""
    _local.client = None


def is_connected():
    try:
        c = get_client()
        c.app_version()
        return True
    except Exception:
        return False




def add_magnet(magnet_link, save_path=None, category=None):
    c = get_client()
    kwargs = {'urls': magnet_link}
    if save_path:
        kwargs['save_path'] = save_path
    if category:
        kwargs['category'] = category
    try:
        c.torrents_add(**kwargs)
    except Exception as e:
        if isinstance(e, qbittorrentapi.Conflict409Error) or '409' in str(e) or 'conflict' in str(e).lower():
            log.debug('add_magnet: torrent already in qBittorrent (duplicate) — carrying on')
            return
        raise


def stop_torrents(hashes):
    """Pause ("stop" since qBittorrent 5)."""
    get_client().torrents_stop(torrent_hashes=hashes)


def start_torrents(hashes):
    get_client().torrents_start(torrent_hashes=hashes)


def delete_torrents(hashes, delete_files=False):
    get_client().torrents_delete(torrent_hashes=hashes, delete_files=delete_files)


def recheck_torrents(hashes):
    get_client().torrents_recheck(torrent_hashes=hashes)


def reannounce_torrents(hashes):
    get_client().torrents_reannounce(torrent_hashes=hashes)


def get_transfer_info():
    try:
        c = get_client()
        return c.transfer_info()
    except Exception:
        return {}


def get_preferences():
    try:
        c = get_client()
        return dict(c.app_preferences())
    except Exception:
        return {}


def set_preferences(prefs: dict):
    c = get_client()
    c.app_set_preferences(prefs)


def get_categories():
    try:
        c = get_client()
        return dict(c.torrents_categories())
    except Exception:
        return {}


def create_category(name, save_path=''):
    c = get_client()
    c.torrents_create_category(name=name, save_path=save_path)


def edit_category(name, save_path):
    c = get_client()
    c.torrents_edit_category(name=name, save_path=save_path)


def category_folder(name, categories, prefs):
    """
    Where qBittorrent saves a new torrent in this category when it's added
    without a path — which is how Sonarr and Radarr add them.

    In qBittorrent's default (manual) mode category folders are ignored
    unless "use category paths in manual mode" is on; with it on (or in
    automatic mode) an empty category folder means <default>/<category>.
    """
    default = (prefs.get('save_path') or '/').rstrip('/') or '/'
    if not name or not (prefs.get('auto_tmm_enabled') or prefs.get('use_category_paths_in_manual_mode')):
        return default
    cat = categories.get(name) or {}
    path = (cat.get('savePath') or cat.get('save_path') or name).rstrip('/')
    return path if path.startswith('/') else f'{default.rstrip("/")}/{path}'


def set_category_folder(name, save_path):
    """
    Create or update a category's folder, and make sure qBittorrent actually
    uses it for torrents added in manual mode. Returns True if that
    preference had to be switched on.
    """
    c = get_client()
    if name in dict(c.torrents_categories()):
        c.torrents_edit_category(name=name, save_path=save_path)
    else:
        c.torrents_create_category(name=name, save_path=save_path)
    if save_path:
        prefs = c.app_preferences()
        if not (prefs.get('auto_tmm_enabled') or prefs.get('use_category_paths_in_manual_mode')):
            c.app_set_preferences({'use_category_paths_in_manual_mode': True})
            log.info('set_category_folder: switched on "use category paths in manual mode" for %r', name)
            return True
    return False


def delete_category(name):
    c = get_client()
    c.torrents_remove_categories(categories=[name])


def set_torrent_location(torrent_hash, save_path):
    """Tell qBittorrent to move the torrent's data to a new path (qBT does the file move)."""
    c = get_client()
    c.torrents_set_location(location=save_path, hashes=torrent_hash)


def rename_torrent(torrent_hash, new_name):
    """Rename the torrent (renames root folder for multi-file torrents)."""
    c = get_client()
    c.torrents_rename(torrent_hash=torrent_hash, new_torrent_name=new_name)
