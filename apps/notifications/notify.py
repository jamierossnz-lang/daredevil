import logging
import requests
from pathlib import Path

log = logging.getLogger('daredevil.notifications')

_PRIORITY_MAP = {'min': 1, 'low': 2, 'default': 3, 'high': 4, 'urgent': 5, 'max': 5}


def _load_ntfy_cfg():
    """Re-read .env every call so worker picks up changes without restart."""
    from django.conf import settings as s
    env_file = Path(s.BASE_DIR) / '.env'
    cfg = {
        'url':   getattr(s, 'NTFY_URL',   'https://ntfy.sh'),
        'topic': getattr(s, 'NTFY_TOPIC', ''),
        'token': getattr(s, 'NTFY_TOKEN', ''),
    }
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key, val = key.strip(), val.strip()
            if key == 'NTFY_URL' and val:
                cfg['url'] = val
            elif key == 'NTFY_TOPIC':
                cfg['topic'] = val
            elif key == 'NTFY_TOKEN':
                cfg['token'] = val
    except Exception:
        pass
    return cfg


def _derive_level(priority, tags):
    tags = tags or []
    if any(t in tags for t in ('x', 'rotating_light')):
        return 'error'
    if 'white_check_mark' in tags:
        return 'success'
    if 'warning' in tags:
        return 'warning'
    if priority in ('urgent', 'max'):
        return 'error'
    if priority == 'high':
        return 'warning'
    if priority == 'low':
        return 'success'
    return 'info'


def _derive_icon(title, level):
    t = title.lower()
    if 'failed' in t or 'error' in t:
        return 'fa-circle-xmark'
    if 'complete' in t or 'ready' in t or 'watch' in t:
        return 'fa-circle-check'
    if 'episode' in t or 'queued' in t:
        return 'fa-tv'
    if 'movie' in t or 'available' in t:
        return 'fa-film'
    if 'storage' in t or 'drive' in t:
        return 'fa-hard-drive'
    if 'download' in t:
        return 'fa-download'
    if 'move' in t or 'library' in t:
        return 'fa-folder-open'
    return {'error': 'fa-circle-xmark', 'warning': 'fa-triangle-exclamation',
            'success': 'fa-circle-check'}.get(level, 'fa-bell')


def _category_enabled(category):
    if not category:
        return True
    try:
        from apps.notifications.models import NotificationPrefs
        return NotificationPrefs.get().is_enabled(category)
    except Exception:
        return True


def _save_to_db(title, message, level, icon, category):
    try:
        from apps.notifications.models import Notification
        Notification.objects.create(
            title=title, message=message, level=level, icon=icon, category=category or '',
        )
        # Keep newest 200
        keep_ids = list(Notification.objects.values_list('pk', flat=True)[:200])
        Notification.objects.exclude(pk__in=keep_ids).delete()
    except Exception as e:
        log.debug('notification DB save failed: %s', e)


def send(title, message, priority='default', tags=None, level=None, category=None):
    """
    Save to the in-app notification centre AND optionally push via ntfy.
    Skipped entirely when the category is disabled in NotificationPrefs.
    ntfy push is skipped when NTFY_TOPIC is blank.
    """
    if not _category_enabled(category):
        return

    derived_level = level or _derive_level(priority, tags)
    icon = _derive_icon(title, derived_level)

    _save_to_db(title, message, derived_level, icon, category)

    # ntfy push
    cfg = _load_ntfy_cfg()
    ntfy_url   = cfg['url'].rstrip('/')
    ntfy_topic = cfg['topic']
    ntfy_token = cfg['token']

    if not ntfy_url or not ntfy_topic:
        return

    headers = {'Title': title, 'Priority': str(_PRIORITY_MAP.get(priority, 3))}
    if tags:
        headers['Tags'] = ','.join(tags)
    if ntfy_token:
        headers['Authorization'] = f'Bearer {ntfy_token}'

    try:
        resp = requests.post(
            f'{ntfy_url}/{ntfy_topic}',
            data=message.encode('utf-8'),
            headers=headers,
            timeout=5,
        )
        resp.raise_for_status()
        log.debug('ntfy sent: %r', title)
    except Exception as e:
        log.warning('ntfy notification failed (%r): %s', title, e)
