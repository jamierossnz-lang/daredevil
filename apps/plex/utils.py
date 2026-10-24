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


# Unraid's live list of drives — docker-compose mounts UNRAID_STATUS_DIR
# (/var/local/emhttp on Unraid) here read-only. Missing everywhere else.
UNRAID_DISKS_INI = os.environ.get('UNRAID_DISKS_INI', '/unraid/disks.ini')


def _usage(labels, path, total, free, used=None, subtitle=''):
    used = total - free if used is None else used
    pct = round(used / total * 100) if total else 0
    return {
        'labels':        labels,
        'path':          path,
        'subtitle':      subtitle or path,
        'total':         total,
        'used':          used,
        'free':          free,
        'pct':           pct,
        'total_display': _fmt_bytes(total),
        'used_display':  _fmt_bytes(used),
        'free_display':  _fmt_bytes(free),
        'warning':       pct >= 90,
        'caution':       75 <= pct < 90,
    }


def _unraid_disks():
    """[{name, type, total, free}] for every mounted array disk and pool, or [] when not on Unraid.

    disks.ini is one ["name"] section per drive, with sizes in KiB. Parity
    has no filesystem (fsSize is empty) and the flash drive isn't storage,
    so both are skipped, as are empty slots and an array that's stopped."""
    try:
        with open(UNRAID_DISKS_INI) as f:
            text = f.read()
    except OSError:
        return []
    sections, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('['):
            cur = {}
            sections.append(cur)
        elif '=' in line and cur is not None:
            key, value = line.split('=', 1)
            cur[key.strip()] = value.strip().strip('"')
    disks = []
    for d in sections:
        if d.get('type') in ('Parity', 'Flash') or d.get('name') in ('flash', 'parity', 'parity2'):
            continue
        try:
            total, free = int(d.get('fsSize') or 0) * 1024, int(d.get('fsFree') or 0) * 1024
        except ValueError:
            continue
        if total > 0:
            disks.append({'name': d.get('name', ''), 'type': d.get('type', ''), 'total': total, 'free': free})
    return disks


def _disk_title(name):
    """disk3 → Disk 3, cache → Cache, nvme_pool → Nvme Pool."""
    if name.startswith('disk') and name[4:].isdigit():
        return f'Disk {name[4:]}'
    return name.replace('_', ' ').title()


def _unraid_usage(disks):
    """The array as one entry (with each data disk under `disks`), then one entry per pool."""
    array = [d for d in disks if d['type'] == 'Data' or (d['name'].startswith('disk') and d['name'][4:].isdigit())]
    pools = [d for d in disks if d not in array]
    out = []
    if array:
        entry = _usage([{'label': 'Array', 'color': 'brand', 'icon': 'fa-server'}], 'unraid:array',
                       sum(d['total'] for d in array), sum(d['free'] for d in array),
                       subtitle=f'{len(array)} data disk{"s" if len(array) != 1 else ""}')
        entry['disks'] = [{'name': _disk_title(d['name']), **_usage([], '', d['total'], d['free'])} for d in array]
        out.append(entry)
    for d in pools:
        out.append(_usage([{'label': _disk_title(d['name']), 'color': 'cyan', 'icon': 'fa-bolt'}],
                          f'unraid:{d["name"]}', d['total'], d['free'], subtitle='Pool'))
    return out


def get_disk_usage():
    """
    On Unraid: the array and each pool, from Unraid's own drive list.
    Elsewhere: one dict per unique filesystem among the watched folders
    (see _storage_paths). Folders on the same device are merged into one entry.
    """
    disks = _unraid_disks()
    if disks:
        return _unraid_usage(disks)

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
            info = _usage([{'label': entry['label'], 'color': entry['color'], 'icon': entry['icon']}],
                          path, usage.total, usage.free, usage.used)
            seen_dev[dev] = info
            drives.append(info)

    return drives
