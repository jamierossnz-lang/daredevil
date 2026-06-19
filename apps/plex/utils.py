import os
import shutil
import logging

log = logging.getLogger('daredevil.plex')


def _fmt_bytes(b):
    if not b:
        return ''
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} TB'


def get_disk_usage():
    """
    Return one dict per unique filesystem that has a completed_path configured.
    Paths on the same device are merged into one entry.
    """
    from apps.qbt.models import CategoryPath, CategoryConfig

    try:
        cfg = CategoryConfig.get()
        tv_cat    = (cfg.tv_category    or '').lower()
        movie_cat = (cfg.movie_category or '').lower()
    except Exception:
        return []

    raw = []
    for cp in CategoryPath.objects.exclude(completed_path=''):
        name = cp.category_name.lower()
        if name == tv_cat:
            label, color, icon = 'TV Shows', 'purple', 'fa-tv'
        elif name == movie_cat:
            label, color, icon = 'Movies', 'cyan', 'fa-film'
        else:
            label, color, icon = cp.category_name, 'brand', 'fa-folder'
        raw.append({'label': label, 'color': color, 'icon': icon, 'path': cp.completed_path})

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
