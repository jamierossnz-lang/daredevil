import json
import logging
import os
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages

from . import client

log = logging.getLogger('daredevil.qbt.views')


def dashboard(request):
    """The torrent list itself loads from torrents.json — this only checks
    the connection, so the page opens instantly."""
    try:
        version = client.get_client().app_version()
        connected = True
    except Exception:
        client.reset_client()
        version, connected = 'Not connected', False
    return render(request, 'qbt/dashboard.html', {'connected': connected, 'version': version})


# qBittorrent states → the Torrent Manager's filter groups (v5 renamed paused* to stopped*)
_STATE_GROUPS = {
    'downloading': 'downloading', 'forcedDL': 'downloading', 'metaDL': 'downloading', 'forcedMetaDL': 'downloading',
    'stalledDL': 'stalled', 'queuedDL': 'queued', 'checkingDL': 'checking', 'allocating': 'checking',
    'uploading': 'seeding', 'forcedUP': 'seeding', 'stalledUP': 'seeding', 'queuedUP': 'queued', 'checkingUP': 'checking',
    'pausedDL': 'paused', 'stoppedDL': 'paused', 'pausedUP': 'paused', 'stoppedUP': 'paused',
    'checkingResumeData': 'checking', 'moving': 'checking',
    'error': 'error', 'missingFiles': 'error', 'unknown': 'error',
}


def torrents_json(request):
    """Polled by the Torrent Manager every few seconds."""
    try:
        torrents = client.get_client().torrents_info()
    except Exception as e:
        client.reset_client()
        return JsonResponse({'error': str(e) or type(e).__name__}, status=503)
    data = [{
        'hash': t.hash,
        'name': t.name,
        'state': t.state,
        'group': _STATE_GROUPS.get(t.state, 'other'),
        'progress': round(t.progress * 100, 1),
        'dl': t.dlspeed, 'up': t.upspeed,
        'dlspeed': _fmt_speed(t.dlspeed),
        'upspeed': _fmt_speed(t.upspeed),
        'size_bytes': t.size,
        'size': _fmt_size(t.size),
        'eta': _fmt_eta(t.eta),
        'category': t.category,
        'seeds': t.num_seeds,
        'peers': t.num_leechs,
        'ratio': round(t.ratio or 0, 2),
        'added_on': t.added_on,
        'save_path': t.save_path,
    } for t in torrents]
    return JsonResponse({'torrents': data})


@require_POST
def torrents_action(request):
    """start / stop / delete / recheck / reannounce — one or many torrents."""
    data = json.loads(request.body or b'{}')
    action = data.get('action')
    hashes = [h for h in data.get('hashes') or [] if isinstance(h, str) and h.isalnum()]
    if not hashes:
        return JsonResponse({'ok': False, 'error': 'Nothing selected'}, status=400)
    actions = {
        'start': client.start_torrents, 'stop': client.stop_torrents,
        'recheck': client.recheck_torrents, 'reannounce': client.reannounce_torrents,
        'delete': lambda h: client.delete_torrents(h, delete_files=bool(data.get('delete_files'))),
    }
    if action not in actions:
        return JsonResponse({'ok': False, 'error': 'Unknown action'}, status=400)
    try:
        actions[action](hashes)
        log.info('torrents_action: %s %d torrent(s)%s', action, len(hashes),
                 ' + files' if action == 'delete' and data.get('delete_files') else '')
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@require_POST
def torrent_add_magnet(request):
    magnet = request.POST.get('magnet', '').strip()
    save_path = request.POST.get('save_path', '')
    category = request.POST.get('category', '')
    if not magnet:
        return JsonResponse({'error': 'No magnet link provided'}, status=400)
    try:
        client.add_magnet(magnet, save_path=save_path or None, category=category or None)
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def settings_view(request):
    """Moved to App Settings → qBittorrent."""
    return redirect('/settings/#qbittorrent')


def _web_url(request):
    """qBittorrent's own Web UI on the host the user reached Daredevil on."""
    from django.conf import settings as django_settings
    host = request.get_host().split(':')[0]
    return f'{request.scheme}://{host}:{django_settings.QBT_WEBUI_PORT}'


def settings_data(request):
    """Everything the App Settings → qBittorrent tab shows, in one call."""
    from django.conf import settings as django_settings
    data = {
        'conn': {
            'host': django_settings.QBITTORRENT_HOST,
            'port': django_settings.QBITTORRENT_PORT,
            'username': django_settings.QBITTORRENT_USERNAME,
            'password': django_settings.QBITTORRENT_PASSWORD,
        },
        'web_url': _web_url(request),
        'connected': False, 'version': None, 'prefs': {}, 'categories': [], 'error': None,
    }
    try:
        c = client.get_client()
        data['version'] = c.app_version()
        data['connected'] = True
        data['prefs'] = prefs = dict(c.app_preferences())
        used = {}
        for app in _arr_categories():
            if app['category']:
                used.setdefault(app['category'], []).append(app['kind'])
        cats = dict(c.torrents_categories())
        data['categories'] = [
            {'name': name, 'save_path': (cat.get('savePath') or cat.get('save_path') or ''),
             'effective': client.category_folder(name, cats, prefs), 'used_by': used.get(name, [])}
            for name, cat in sorted(cats.items())
        ]
    except Exception as e:
        data['error'] = str(e) or type(e).__name__
    return JsonResponse(data)


@require_POST
def settings_save(request):
    try:
        data = json.loads(request.body)
        client.set_preferences(data)
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def connection_save(request):
    """Update qBittorrent connection settings in-memory and persist to .env."""
    import re
    from django.conf import settings as django_settings

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    host     = str(data.get('host', '')).strip()
    port     = data.get('port', '')
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()

    if not host:
        return JsonResponse({'error': 'Host is required'}, status=400)
    try:
        port = int(port)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Port must be a number'}, status=400)

    # Apply to running process immediately — client reads settings on every call
    django_settings.QBITTORRENT_HOST     = host
    django_settings.QBITTORRENT_PORT     = port
    django_settings.QBITTORRENT_USERNAME = username
    django_settings.QBITTORRENT_PASSWORD = password

    # Persist to .env so values survive a restart
    _update_env({
        'QBITTORRENT_HOST':     host,
        'QBITTORRENT_PORT':     str(port),
        'QBITTORRENT_USERNAME': username,
        'QBITTORRENT_PASSWORD': password,
    })

    # The live download queue backs off after failed logins (see
    # apps.arr.queue._qbt_torrents) — retry straight away with the new details.
    from django.core.cache import cache
    cache.delete('arr_queue_qbt_backoff')

    # Test the new connection
    ok = client.is_connected()
    log.info('connection_save: %s:%s ok=%s', host, port, ok)
    return JsonResponse({'ok': ok, 'connected': ok})


def _update_env(updates: dict):
    """Write key=value pairs into .env, adding lines for missing keys."""
    import re
    from django.conf import settings as django_settings

    env_path = getattr(django_settings, 'BASE_DIR', None)
    if env_path is None:
        return
    env_file = env_path / '.env'
    if not env_file.exists():
        return

    text = env_file.read_text()
    for key, value in updates.items():
        pattern = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
        replacement = f'{key}={value}'
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip('\n') + f'\n{replacement}\n'
    env_file.write_text(text)
    log.info('_update_env: wrote %s', list(updates.keys()))


def _arr_categories():
    """[{kind, name, category|error}] — the qBittorrent category each *arr app adds torrents under."""
    from apps.arr.clients import ArrError
    from apps.arr.services import client, is_configured, _field
    meta = [
        ('sonarr', 'Sonarr', 'fa-tv', 'text-blue-400', 'tvCategory'),
        ('radarr', 'Radarr', 'fa-film', 'text-purple-400', 'movieCategory'),
        ('prowlarr', 'Prowlarr', 'fa-magnifying-glass', 'text-orange-400', 'category'),
    ]
    apps = []
    for kind, name, icon, color, field in meta:
        app = {'kind': kind, 'name': name, 'icon': icon, 'color': color, 'category': None, 'error': None}
        if not is_configured(kind):
            app['error'] = 'Not connected'
        else:
            try:
                qbt = next((d for d in client(kind).download_clients() if d.get('implementation') == 'QBittorrent'), None)
                app['category'] = _field(qbt, field) if qbt else None
            except ArrError as e:
                app['error'] = str(e)
        apps.append(app)
    return apps


def categories_page(request):
    """Moved: categories and their folders live on App Settings → qBittorrent
    (and each app's category on its own tab)."""
    return redirect('/settings/#qbittorrent')


@require_POST
def category_save(request):
    """Create a category, or change an existing one's save path."""
    data = json.loads(request.body or b'{}')
    name = (data.get('name') or '').strip()
    save_path = (data.get('save_path') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Name is required'}, status=400)
    try:
        switched_on = client.set_category_folder(name, save_path)
        log.info('category_save: %r save_path=%r', name, save_path)
        return JsonResponse({'ok': True, 'category_paths_switched_on': switched_on})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@require_POST
def category_remove(request):
    data = json.loads(request.body or b'{}')
    name = (data.get('name') or '').strip()
    try:
        client.delete_category(name)
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


def transfer_info_json(request):
    try:
        info = client.get_transfer_info()
        return JsonResponse({
            'dl_speed': _fmt_speed(info.get('dl_info_speed', 0)),
            'up_speed': _fmt_speed(info.get('up_info_speed', 0)),
            'dl_total': _fmt_size(info.get('dl_info_data', 0)),
            'up_total': _fmt_size(info.get('up_info_data', 0)),
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=503)


# ── Formatters ───────────────────────────────────────────────────────────────

def _downloads_local_path():
    """qBittorrent's download folder as Daredevil sees it (via the Sonarr/Radarr
    path mappings — qBittorrent and the apps share the same /downloads mount)."""
    from django.conf import settings as django_settings
    from apps.arr.models import ArrService
    try:
        save_path = (client.get_preferences().get('save_path') or '/downloads').rstrip('/') or '/'
    except Exception:
        save_path = '/downloads'
    for kind in ('sonarr', 'radarr'):
        local = ArrService.get(kind).to_local(save_path)
        if local != save_path:
            return local
    local = getattr(django_settings, 'DOWNLOAD_PATH', '')
    return local or None


def _browser_tabs():
    """
    Tabs for the File Browser, built automatically: the download folder,
    every Sonarr/Radarr root folder (translated to Daredevil's own paths),
    plus any folders added by hand. Cached briefly — every directory listing
    checks its path against these.
    """
    from django.core.cache import cache
    tabs = cache.get('file_browser_tabs')
    if tabs is not None:
        return tabs

    from apps.arr.clients import ArrError
    from apps.arr.models import ArrService
    from apps.arr.services import client as arr_client, is_configured
    from .models import ExtraTab

    tabs = []
    downloads = _downloads_local_path()
    if downloads:
        tabs.append({'label': 'Downloads', 'path': downloads, 'kind': 'download', 'icon': 'fa-arrow-down text-blue-300'})
    for kind, label, icon in (('sonarr', 'TV Shows', 'fa-tv text-purple-300'), ('radarr', 'Movies', 'fa-film text-cyan-300')):
        if not is_configured(kind):
            continue
        svc = ArrService.get(kind)
        try:
            folders = arr_client(kind).root_folders()
        except ArrError:
            continue
        for f in folders:
            remote = f['path'].rstrip('/') or '/'
            local = svc.to_local(remote)
            # Only folders a path mapping translates — an unmapped path is the
            # app's own container path and may mean something else entirely here.
            if local == remote:
                continue
            tabs.append({'label': label if len(folders) == 1 else f'{label} ({remote})', 'path': local,
                         'kind': 'library', 'icon': icon})
    for et in ExtraTab.objects.all():
        tabs.append({'label': et.label, 'path': et.path, 'kind': 'custom', 'pk': et.pk, 'icon': 'fa-folder text-yellow-300'})

    seen, unique = set(), []
    for t in tabs:
        if t['path'] not in seen:
            seen.add(t['path'])
            unique.append(t)
    cache.set('file_browser_tabs', unique, 60)
    return unique


def _all_allowed_roots():
    """Realpath-resolved roots the File Browser may list, delete in, or import from."""
    roots = []
    for t in _browser_tabs():
        try:
            roots.append(os.path.realpath(t['path']))
        except Exception:
            pass
    return roots


@require_POST
def file_tabs_add(request):
    from .models import ExtraTab
    label = request.POST.get('label', '').strip()
    path  = request.POST.get('path', '').strip()
    if not label:
        return JsonResponse({'error': 'Label is required'}, status=400)
    if not path:
        return JsonResponse({'error': 'Path is required'}, status=400)
    if not os.path.isdir(path):
        return JsonResponse({'error': f'Directory not found: {path}'}, status=400)
    tab = ExtraTab.objects.create(label=label, path=path)
    from django.core.cache import cache
    cache.delete('file_browser_tabs')
    log.info('file_tabs_add: created pk=%d label=%r path=%r', tab.pk, label, path)
    return JsonResponse({'ok': True, 'pk': tab.pk})


@require_POST
def file_tabs_delete(request, pk):
    from .models import ExtraTab
    ExtraTab.objects.filter(pk=pk).delete()
    from django.core.cache import cache
    cache.delete('file_browser_tabs')
    log.info('file_tabs_delete: deleted pk=%d', pk)
    return JsonResponse({'ok': True})


def file_browser_page(request):
    return render(request, 'qbt/files.html', {'tabs': _browser_tabs()})


def file_browser_list(request):
    """Return a directory listing as JSON. Path must be inside a configured category path."""
    import os

    raw = request.GET.get('path', '').strip()
    if not raw:
        return JsonResponse({'error': 'No path specified'}, status=400)

    # Detect Windows-style paths used on a non-Windows host
    is_windows_path = (
        (len(raw) >= 2 and raw[1] == ':') or   # drive letter: C:\...
        raw.startswith('\\\\')                  # UNC: \\server\share
    )
    if is_windows_path and os.sep != '\\':
        return JsonResponse({
            'error': f'This path looks like a Windows path ({raw!r}). '
                     'Update the category to use the path as this machine sees it '
                     '(e.g. a network mount like /Volumes/… or /mnt/…).'
        }, status=400)

    path = os.path.realpath(raw)

    allowed = _all_allowed_roots()

    def _permitted(p):
        return any(p == a or p.startswith(a + os.sep) for a in allowed)

    if not _permitted(path):
        return JsonResponse({'error': 'Path not permitted'}, status=403)

    if not os.path.exists(path):
        return JsonResponse({
            'error': f'Path not found on this machine: {path}\n'
                     'The folder may not be mounted or the path may need updating in Categories.'
        }, status=404)

    if not os.path.isdir(path):
        return JsonResponse({'error': f'{path} is a file, not a folder'}, status=400)

    # Directory listing
    entries = []
    try:
        for name in sorted(os.listdir(path), key=lambda n: n.lower()):
            full   = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            entry  = {'name': name, 'type': 'dir' if is_dir else 'file', 'path': full}
            if not is_dir:
                try:
                    entry['size'] = os.path.getsize(full)
                except Exception:
                    entry['size'] = 0
                entry['ext'] = os.path.splitext(name)[1].lower().lstrip('.')
            entries.append(entry)
    except PermissionError as e:
        return JsonResponse({'error': str(e)}, status=403)

    dirs  = [e for e in entries if e['type'] == 'dir']
    files = [e for e in entries if e['type'] == 'file']

    # Parent: only expose if it's still within an allowed root
    parent_path = os.path.dirname(path)
    parent = parent_path if (parent_path != path and _permitted(parent_path)) else None

    # Breadcrumb: walk from the deepest allowed root up to current path
    root = max(
        (a for a in allowed if path == a or path.startswith(a + os.sep)),
        key=len, default=None,
    )
    breadcrumb = []
    if root:
        rel  = os.path.relpath(path, root)
        node = root
        breadcrumb.append({'name': os.path.basename(root) or root, 'path': root})
        if rel != '.':
            for part in rel.split(os.sep):
                node = os.path.join(node, part)
                breadcrumb.append({'name': part, 'path': node})

    all_entries = dirs + files
    total = len(all_entries)

    try:
        page_size = max(1, min(500, int(request.GET.get('page_size', 50))))
    except (ValueError, TypeError):
        page_size = 50

    total_pages = max(1, (total + page_size - 1) // page_size)

    try:
        page = max(1, min(total_pages, int(request.GET.get('page', 1))))
    except (ValueError, TypeError):
        page = 1

    start = (page - 1) * page_size
    entries = all_entries[start:start + page_size]

    return JsonResponse({
        'path':        path,
        'parent':      parent,
        'breadcrumb':  breadcrumb,
        'entries':     entries,
        'total':       total,
        'page':        page,
        'page_size':   page_size,
        'total_pages': total_pages,
    })


@require_POST
def file_delete(request):
    """Delete a file or folder within an allowed category path."""
    import shutil

    raw = request.POST.get('path', '').strip()
    if not raw:
        return JsonResponse({'error': 'No path specified'}, status=400)

    path = os.path.realpath(raw)
    allowed = _all_allowed_roots()

    def _permitted(p):
        return any(p == a or p.startswith(a + os.sep) for a in allowed)

    if not _permitted(path):
        return JsonResponse({'error': 'Path not permitted'}, status=403)

    if not os.path.exists(path):
        return JsonResponse({'error': 'Path not found'}, status=404)

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        log.info('file_delete: deleted %r', path)
        return JsonResponse({'ok': True})
    except Exception as e:
        log.error('file_delete: failed for %r — %s', path, e)
        return JsonResponse({'error': str(e)}, status=500)


def _detect_type(path):
    """TV if any file name in it carries an SxxExx code, otherwise movie."""
    import re
    pattern = re.compile(r'[Ss]\d+[Ee]\d+')
    if os.path.isfile(path):
        return 'tv' if pattern.search(os.path.basename(path)) else 'movie'
    for _root, _dirs, files in os.walk(path):
        if any(pattern.search(f) for f in files):
            return 'tv'
    return 'movie'


@require_POST
def file_import(request):
    """
    Hand selected files/folders to Sonarr (TV) or Radarr (movies) to import —
    they match them to the library, move/rename them and record the import.
    Daredevil doesn't move files itself any more. Paths are translated from
    Daredevil's mounts to the app's own via its path mappings (Settings).
    """
    from apps.arr.clients import ArrError
    from apps.arr.models import ArrService
    from apps.arr.services import client as arr_client, is_configured

    paths = request.POST.getlist('paths')
    media_type = request.POST.get('media_type', 'auto')
    import_mode = request.POST.get('import_mode', 'Auto')
    if import_mode not in ('Auto', 'Move', 'Copy'):
        import_mode = 'Auto'
    if not paths:
        return JsonResponse({'error': 'No paths provided'}, status=400)

    allowed = _all_allowed_roots()

    def _permitted(p):
        return any(p == a or p.startswith(a + os.sep) for a in allowed)

    results = []
    for raw in paths:
        src = os.path.realpath(raw)
        if not _permitted(src):
            results.append({'path': raw, 'error': 'Not permitted'})
            continue
        if not os.path.exists(src):
            results.append({'path': raw, 'error': 'Path not found'})
            continue
        detected = media_type if media_type in ('movie', 'tv') else _detect_type(src)
        kind = 'sonarr' if detected == 'tv' else 'radarr'
        if not is_configured(kind):
            results.append({'path': raw, 'error': f'{kind.title()} is not connected (Settings → {kind.title()})'})
            continue
        remote = ArrService.get(kind).to_remote(src)
        command = 'DownloadedEpisodesScan' if kind == 'sonarr' else 'DownloadedMoviesScan'
        try:
            arr_client(kind).command(command, path=remote, importMode=import_mode)
            log.info('file_import: %s %r (as %r) mode=%s', command, src, remote, import_mode)
            results.append({'path': raw, 'app': kind, 'remote_path': remote})
        except ArrError as e:
            results.append({'path': raw, 'error': str(e)})

    return JsonResponse({'results': results})


def _fmt_size(b):
    if not b:
        return '0 B'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


def _fmt_speed(bps):
    return _fmt_size(bps) + '/s'


def _fmt_eta(seconds):
    if not seconds or seconds < 0 or seconds > 8640000:
        return '∞'
    if seconds >= 3600:
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m'
    if seconds >= 60:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds}s'
