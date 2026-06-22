import json
import logging
import os
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages

from . import client

log = logging.getLogger('daredevil.qbt.views')


def dashboard(request):
    connected = client.is_connected()
    torrents = []
    transfer = {}
    stats = {}
    version = 'Not connected'

    if connected:
        torrents = client.get_torrents()
        transfer = client.get_transfer_info()
        stats = client.get_torrents_stats()
        version = client.get_app_version()

    context = {
        'connected': connected,
        'torrents': torrents,
        'transfer': transfer,
        'stats': stats,
        'version': version,
    }
    return render(request, 'qbt/dashboard.html', context)


def torrents_json(request):
    """HTMX polling — returns fresh torrent list."""
    filter_status = request.GET.get('filter', None)
    try:
        torrents = client.get_torrents(filter_status=filter_status)
        data = [
            {
                'hash': t.hash,
                'name': t.name,
                'state': t.state,
                'progress': round(t.progress * 100, 1),
                'dlspeed': _fmt_speed(t.dlspeed),
                'upspeed': _fmt_speed(t.upspeed),
                'size': _fmt_size(t.size),
                'eta': _fmt_eta(t.eta),
                'category': t.category,
                'seeds': t.num_seeds,
                'peers': t.num_leechs,
            }
            for t in torrents
        ]
        return JsonResponse({'torrents': data})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=503)


@require_POST
def torrent_pause(request, torrent_hash):
    try:
        client.pause_torrent(torrent_hash)
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def torrent_resume(request, torrent_hash):
    try:
        client.resume_torrent(torrent_hash)
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def torrent_delete(request, torrent_hash):
    delete_files = request.POST.get('delete_files', 'false') == 'true'
    try:
        client.delete_torrent(torrent_hash, delete_files=delete_files)
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


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
    from django.conf import settings as django_settings
    connected = client.is_connected()
    prefs = {}
    if connected:
        prefs = client.get_preferences()
    context = {
        'connected': connected,
        'prefs': prefs,
        'conn': {
            'host':     django_settings.QBITTORRENT_HOST,
            'port':     django_settings.QBITTORRENT_PORT,
            'username': django_settings.QBITTORRENT_USERNAME,
            'password': django_settings.QBITTORRENT_PASSWORD,
        },
    }
    return render(request, 'qbt/settings.html', context)


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


def categories_page(request):
    from .models import CategoryConfig, CategoryPath
    connected = client.is_connected()
    raw = client.get_categories() if connected else {}
    # Normalise to plain dicts so Django templates can access fields reliably
    # and merge in our locally stored download/completed paths
    path_rows = {p.category_name: p for p in CategoryPath.objects.all()}
    categories = {}
    for name, cat in raw.items():
        try:
            qbt_save_path = cat.get('savePath') or cat.get('save_path') or ''
        except (AttributeError, TypeError):
            qbt_save_path = getattr(cat, 'savePath', '') or getattr(cat, 'save_path', '') or ''
        row = path_rows.get(name)
        categories[name] = {
            'name': name,
            'qbt_save_path': qbt_save_path,
            'daredevil_qbt_save_path': row.qbt_save_path if row else '',
            'download_path': row.download_path if row else '',
            'completed_path': row.completed_path if row else '',
        }
    config = CategoryConfig.get()
    return render(request, 'qbt/categories.html', {
        'connected': connected,
        'categories': categories,
        'config': config,
    })


@require_POST
def category_paths_save(request, name):
    """Save the download + completed paths for a category in Daredevil's DB."""
    from .models import CategoryPath
    qbt_save_path  = request.POST.get('qbt_save_path', '').strip()
    download_path  = request.POST.get('download_path', '').strip()
    completed_path = request.POST.get('completed_path', '').strip()
    row, _ = CategoryPath.objects.get_or_create(category_name=name)
    row.qbt_save_path  = qbt_save_path
    row.download_path  = download_path
    row.completed_path = completed_path
    row.save()
    log.info('category_paths_save: %r qbt=%r download=%r completed=%r', name, qbt_save_path, download_path, completed_path)
    if qbt_save_path:
        try:
            client.edit_category(name, save_path=qbt_save_path)
        except Exception as e:
            log.warning('category_paths_save: could not update qBT save_path — %s', e)
    return JsonResponse({'ok': True})


@require_POST
def category_create(request):
    name = request.POST.get('name', '').strip()
    save_path = request.POST.get('save_path', '').strip()
    if not name:
        return JsonResponse({'error': 'Name is required'}, status=400)
    try:
        client.create_category(name, save_path=save_path)
        log.info('category_create: created %r (path=%r)', name, save_path)
        return JsonResponse({'ok': True})
    except Exception as e:
        log.error('category_create: failed — %s', e)
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def category_edit(request, name):
    save_path = request.POST.get('save_path', '').strip()
    try:
        client.edit_category(name, save_path=save_path)
        log.info('category_edit: updated %r save_path=%r', name, save_path)
        return JsonResponse({'ok': True})
    except Exception as e:
        log.error('category_edit: failed — %s', e)
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def category_delete(request, name):
    try:
        client.delete_category(name)
        log.info('category_delete: deleted %r', name)
        return JsonResponse({'ok': True})
    except Exception as e:
        log.error('category_delete: failed — %s', e)
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def category_defaults_save(request):
    from .models import CategoryConfig, CategoryPath
    tv = request.POST.get('tv_category', '').strip()
    movie = request.POST.get('movie_category', '').strip()
    config = CategoryConfig.get()
    old_tv    = config.tv_category
    old_movie = config.movie_category
    if tv:
        config.tv_category = tv
    if movie:
        config.movie_category = movie
    config.save()
    log.info('category_defaults_save: tv=%r movie=%r', config.tv_category, config.movie_category)

    # Migrate stored paths to the new category name so the file browser stays clean
    def _migrate(old_name, new_name):
        if not old_name or old_name == new_name:
            return
        old_path = CategoryPath.objects.filter(category_name=old_name).first()
        if not old_path:
            return
        new_path, _ = CategoryPath.objects.get_or_create(category_name=new_name)
        if not new_path.download_path:
            new_path.download_path = old_path.download_path
        if not new_path.completed_path:
            new_path.completed_path = old_path.completed_path
        new_path.save()
        old_path.delete()
        log.info('category_defaults_save: migrated paths %r → %r', old_name, new_name)

    _migrate(old_tv, config.tv_category)
    _migrate(old_movie, config.movie_category)

    return JsonResponse({'ok': True, 'tv_category': config.tv_category, 'movie_category': config.movie_category})


def search_page(request):
    return render(request, 'qbt/search.html', {'connected': client.is_connected()})


@require_POST
def search_run(request):
    """Single synchronous endpoint — blocks until qBT search completes (~5–30s)."""
    query = request.POST.get('q', '').strip()
    if not query:
        return JsonResponse({'error': 'No query provided'}, status=400)
    log.info('search_run: query=%r', query)
    try:
        raw = client.search_torrents(query)
    except Exception as e:
        log.error('search_run: search_torrents raised %s: %s', type(e).__name__, e)
        return JsonResponse({'error': str(e)}, status=503)
    log.info('search_run: %d raw results for %r', len(raw), query)

    data = []
    for r in raw:
        try:
            # qbittorrentapi models support both dict-style and attribute access
            name  = r.get('fileName',  '') or ''
            url   = r.get('fileUrl',   '') or ''
            size  = r.get('fileSize',   0) or 0
            seeds = r.get('nbSeeders', 0) or 0
            peers = r.get('nbLeechers', 0) or 0
            engine = r.get('siteUrl',  '') or ''
            data.append({
                'name':   name,
                'url':    url,
                'size':   _fmt_size(size) if size > 0 else '?',
                'seeds':  seeds,
                'peers':  peers,
                'engine': engine,
            })
        except Exception:
            continue

    data.sort(key=lambda x: x['seeds'], reverse=True)
    return JsonResponse({'results': data, 'total': len(data)})


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

def _all_allowed_roots():
    """Return a list of realpath-resolved allowed root directories for the file browser."""
    from .models import CategoryPath, ExtraTab
    roots = []
    for cp in CategoryPath.objects.all():
        for p in (cp.download_path, cp.completed_path):
            if p:
                try:
                    roots.append(os.path.realpath(p))
                except Exception:
                    pass
    for et in ExtraTab.objects.all():
        if et.path:
            try:
                roots.append(os.path.realpath(et.path))
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
    log.info('file_tabs_add: created pk=%d label=%r path=%r', tab.pk, label, path)
    return JsonResponse({'ok': True, 'pk': tab.pk})


@require_POST
def file_tabs_delete(request, pk):
    from .models import ExtraTab
    ExtraTab.objects.filter(pk=pk).delete()
    log.info('file_tabs_delete: deleted pk=%d', pk)
    return JsonResponse({'ok': True})


def file_browser_page(request):
    from .models import CategoryPath, ExtraTab
    all_paths = CategoryPath.objects.all()
    if client.is_connected():
        try:
            live_names = set(client.get_categories().keys())
            categories = [cp for cp in all_paths if cp.category_name in live_names]
        except Exception:
            categories = list(all_paths)
    else:
        categories = list(all_paths)

    # Build flat list of named tabs: {label, path, kind}
    tabs = []
    for cp in categories:
        if cp.download_path:
            tabs.append({'label': cp.category_name, 'path': cp.download_path, 'kind': 'download'})
        if cp.completed_path:
            tabs.append({'label': cp.category_name, 'path': cp.completed_path, 'kind': 'completed'})

    extra_tabs = list(ExtraTab.objects.all())
    for et in extra_tabs:
        tabs.append({'label': et.label, 'path': et.path, 'kind': 'custom', 'pk': et.pk})

    return render(request, 'qbt/files.html', {'categories': categories, 'tabs': tabs})


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


@require_POST
def file_move_completed(request):
    """
    Move selected files/folders to their completed_path with Plex-standard naming.
    Auto-detects movie vs TV from filenames; falls back to user-supplied media_type.
    """
    from .models import CategoryPath

    paths = request.POST.getlist('paths')
    media_type = request.POST.get('media_type', 'auto')

    if not paths:
        return JsonResponse({'error': 'No paths provided'}, status=400)

    allowed = _all_allowed_roots()

    def _permitted(p):
        return any(p == a or p.startswith(a + os.sep) for a in allowed)

    from apps.qbt.file_naming import detect_type, raw_dest as _raw_dest

    # Find completed_paths for each category
    movie_completed = None
    tv_completed = None
    try:
        from apps.qbt.models import CategoryConfig
        cfg = CategoryConfig.get()
        movie_cp = CategoryPath.objects.filter(category_name=cfg.movie_category).first()
        tv_cp = CategoryPath.objects.filter(category_name=cfg.tv_category).first()
        movie_completed = movie_cp.completed_path if movie_cp else None
        tv_completed = tv_cp.completed_path if tv_cp else None
    except Exception:
        pass

    results = []
    for raw in paths:
        src = os.path.realpath(raw)
        if not _permitted(src):
            results.append({'path': raw, 'error': 'Not permitted'})
            continue
        if not os.path.exists(src):
            results.append({'path': raw, 'error': 'Path not found'})
            continue

        detected = media_type if media_type in ('movie', 'tv') else detect_type(src)
        completed_path = movie_completed if detected == 'movie' else tv_completed
        if not completed_path:
            results.append({'path': raw, 'error': f'No completed path configured for {detected}'})
            continue

        basename = os.path.basename(src.rstrip('/\\'))
        dest, *_ = _raw_dest(src, completed_path, detected)

        from apps.downloads.models import FileMove
        from apps.downloads.tasks import execute_file_move
        move = FileMove.objects.create(
            title=basename,
            source_path=src,
            dest_path=dest,
            status=FileMove.Status.PENDING,
        )
        execute_file_move.delay(move.id, detected_type=detected, completed_path=completed_path)
        log.info('file_move_completed: queued FileMove id=%d  %r (type=%s)', move.id, src, detected)
        results.append({'path': raw, 'move_id': move.id, 'dest': dest, 'type': detected})

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
