import os
import shutil
import logging

from .client import _fmt_bytes

log = logging.getLogger('daredevil.plex')


def _storage_paths():
    """
    [{label, color, icon, path}] for the folders worth watching, as Daredevil
    sees them: Sonarr's and Radarr's root folders (the library) plus the
    downloads folder, translated through each app's path mappings. Falls
    back to the completed paths from the old (pre-Sonarr/Radarr) category
    setup if neither app is connected.
    """
    from django.core.cache import cache

    cached = cache.get('storage_paths')
    if cached is not None:
        return cached

    entries = []
    try:
        from apps.arr.clients import ArrError
        from apps.arr.models import ArrService
        from apps.arr.services import client, is_configured

        for kind, label, color, icon in (('sonarr', 'TV Shows', 'purple', 'fa-tv'),
                                         ('radarr', 'Movies', 'cyan', 'fa-film')):
            if not is_configured(kind):
                continue
            svc = ArrService.get(kind)
            try:
                folders = client(kind).root_folders()
            except ArrError as e:
                log.info('storage: %s root folders unavailable — %s', kind, e)
                continue
            for f in folders:
                entries.append({'label': label, 'color': color, 'icon': icon, 'path': svc.to_local(f['path'])})
            for remote, local in svc.mappings():
                if 'download' in remote.lower():
                    entries.append({'label': 'Downloads', 'color': 'brand', 'icon': 'fa-download', 'path': local})
    except Exception as e:
        log.warning('storage: could not read Sonarr/Radarr folders — %s', e)

    if not entries:
        from apps.qbt.models import CategoryPath
        entries = [
            {'label': cp.category_name, 'color': 'brand', 'icon': 'fa-folder', 'path': cp.completed_path}
            for cp in CategoryPath.objects.exclude(completed_path='')
        ]
    cache.set('storage_paths', entries, 300)
    return entries


def get_disk_usage():
    """
    Return one dict per unique filesystem among the watched folders
    (see _storage_paths). Folders on the same device are merged into one entry.
    """
    raw = _storage_paths()

    seen_dev = {}
    drives = []
    for entry in raw:
        path = entry['path']
        if not os.path.exists(path):
            continue
        try:
            dev   = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except Exception as e:
            log.warning('disk_usage failed for %r: %s', path, e)
            continue

        if dev in seen_dev:
            if all(l['label'] != entry['label'] for l in seen_dev[dev]['labels']):
                seen_dev[dev]['labels'].append(
                    {'label': entry['label'], 'color': entry['color'], 'icon': entry['icon']}
                )
        else:
            pct  = round(usage.used / usage.total * 100) if usage.total else 0
            info = {
                'labels':        [{'label': entry['label'], 'color': entry['color'], 'icon': entry['icon']}],
                'path':          path,
                'total':         usage.total,
                'used':          usage.used,
                'free':          usage.free,
                'pct':           pct,
                'total_display': _fmt_bytes(usage.total),
                'used_display':  _fmt_bytes(usage.used),
                'free_display':  _fmt_bytes(usage.free),
                'warning':       pct >= 90,
                'caution':       75 <= pct < 90,
            }
            seen_dev[dev] = info
            drives.append(info)

    return drives
