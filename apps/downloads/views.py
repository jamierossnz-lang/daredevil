"""
Downloads — a window onto Sonarr's and Radarr's queues.

Sonarr/Radarr pick releases, send them to qBittorrent and import finished
downloads into the library; Daredevil shows that state (merged with
qBittorrent's live speed/peers) and sends the user's actions back:
pause/resume (qBittorrent), remove, retry with a different release
(blocklist + search again), force-grab a delayed release, search again.
The Imports page lists what Sonarr/Radarr imported and confirms each file
is really in the library.
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.arr.clients import ArrError
from apps.arr.http import json_body as _body, json_error as _fail
from apps.arr.models import ArrService
from apps.arr.queue import build_queue
from apps.arr.services import (
    client, download_episodes, image_url, in_thread, is_configured, parse_dt, queue_remove, radarr, sonarr,
)

log = logging.getLogger('daredevil.downloads')


def _app(value):
    if value not in ('sonarr', 'radarr'):
        raise ArrError('Unknown app')
    return value


# ── Queue page + live stream ──────────────────────────────────────────────────

def queue(request):
    items, status = build_queue()
    return render(request, 'downloads/queue.html', {
        'items': items,
        'status': status,
        'sonarr_ready': is_configured('sonarr'),
        'radarr_ready': is_configured('radarr'),
    })


def queue_status_json(request):
    items, status = build_queue()
    return JsonResponse({'items': items, 'status': status}, json_dumps_params={'default': str})


def queue_status_stream(request):
    """
    Server-sent events: pushes the merged queue whenever it changes. Each
    connection is capped at ~55s and EventSource reconnects transparently,
    which doubles as a self-heal for a stale connection.
    """
    from django.db import close_old_connections

    def event_stream():
        deadline = time.monotonic() + 55
        last_payload = None
        while time.monotonic() < deadline:
            close_old_connections()
            try:
                items, status = build_queue()
                payload = json.dumps({'items': items, 'status': status}, default=str)
            except Exception as e:
                log.error('queue_status_stream: tick failed — %s', e)
                yield ': error\n\n'
                time.sleep(3)
                continue
            if payload != last_payload:
                yield f'data: {payload}\n\n'
                last_payload = payload
            else:
                yield ': heartbeat\n\n'
            time.sleep(3)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# ── Queue actions ─────────────────────────────────────────────────────────────

@require_POST
def queue_action(request):
    """
    action = remove   → remove from Sonarr/Radarr + qBittorrent (no retry)
           = retry    → blocklist this release and search for another one
           = grab     → force-grab an item waiting in the delay queue
           = pause / resume → qBittorrent, by torrent hash
    """
    data = _body(request)
    action = data.get('action')
    ids = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
    try:
        if action in ('pause', 'resume'):
            from apps.qbt.client import start_torrents, stop_torrents
            torrent_hash = (data.get('hash') or '').strip()
            if not torrent_hash:
                return _fail('No torrent hash for this download')
            (stop_torrents if action == 'pause' else start_torrents)(torrent_hash)
            return JsonResponse({'ok': True})

        app = _app(data.get('app'))
        if not ids:
            return _fail('Nothing selected')
        if action == 'remove':
            queue_remove(app, ids, blocklist=bool(data.get('blocklist')), search_again=False,
                         remove_from_client=data.get('remove_from_client', True) is not False)
            return JsonResponse({'ok': True, 'message': 'Removed'})
        if action == 'retry':
            queue_remove(app, ids, blocklist=True, search_again=True)
            return JsonResponse({'ok': True, 'message': 'Release blocklisted — searching for another one'})
        if action == 'grab':
            client(app).queue_grab(ids[0])
            return JsonResponse({'ok': True, 'message': 'Grabbing now'})
        return _fail('Unknown action')
    except ArrError as e:
        return _fail(e)
    except Exception as e:
        log.warning('queue_action %s failed: %s', action, e)
        return _fail(e)


@require_POST
def search_again(request):
    """'Search again' for wanted / failed items — automatic search in Sonarr/Radarr."""
    data = _body(request)
    try:
        app = _app(data.get('app'))
        if app == 'sonarr':
            ids = [int(i) for i in data.get('episode_ids', []) if str(i).isdigit()]
            if data.get('all_missing'):
                sonarr().command('MissingEpisodeSearch', monitored=True)
            elif ids:
                download_episodes(ids)
            else:
                return _fail('Nothing to search for')
        else:
            ids = [int(i) for i in data.get('movie_ids', []) if str(i).isdigit()]
            if data.get('all_missing'):
                radarr().command('MissingMoviesSearch')
            elif ids:
                radarr().command('MoviesSearch', movieIds=ids)
            else:
                return _fail('Nothing to search for')
        return JsonResponse({'ok': True, 'message': f'{app.title()} is searching'})
    except ArrError as e:
        return _fail(e)


@require_POST
def history_failed(request, app, history_id):
    """'Mark as failed' on a grab — blocklists it and searches for a replacement."""
    try:
        client(_app(app)).history_mark_failed(history_id)
        return JsonResponse({'ok': True, 'message': 'Marked failed — searching for a replacement'})
    except ArrError as e:
        return _fail(e)


# ── Lazy-loaded tabs: waiting / wanted / history / failed ─────────────────────

_EVENT_LABELS = {
    'grabbed': ('Grabbed', 'fa-arrow-down', 'text-brand-400'),
    'downloadFolderImported': ('Imported', 'fa-circle-check', 'text-green-400'),
    'seriesFolderImported': ('Imported', 'fa-circle-check', 'text-green-400'),
    'movieFolderImported': ('Imported', 'fa-circle-check', 'text-green-400'),
    'downloadFailed': ('Download failed', 'fa-circle-xmark', 'text-red-400'),
    'downloadIgnored': ('Ignored', 'fa-eye-slash', 'text-gray-400'),
    'episodeFileDeleted': ('File deleted', 'fa-trash', 'text-gray-400'),
    'movieFileDeleted': ('File deleted', 'fa-trash', 'text-gray-400'),
    'episodeFileRenamed': ('Renamed', 'fa-pen', 'text-gray-400'),
    'movieFileRenamed': ('Renamed', 'fa-pen', 'text-gray-400'),
}


def _history_row(app, r):
    label, icon, color = _EVENT_LABELS.get(r.get('eventType'), (r.get('eventType') or '', 'fa-circle', 'text-gray-400'))
    data = r.get('data') or {}
    when = parse_dt(r.get('date'))
    if app == 'sonarr':
        series = r.get('series') or {}
        ep = r.get('episode') or {}
        code = f'S{ep.get("seasonNumber", 0):02d}E{ep.get("episodeNumber", 0):02d}' if ep else ''
        title = f'{series.get("title", "Unknown")} {code}'.strip()
        poster = image_url(series.get('images'), 'poster')
        link = f'/shows/{r["seriesId"]}/' if r.get('seriesId') else None
        target = {'episode_ids': [r['episodeId']]} if r.get('episodeId') else {}
    else:
        movie = r.get('movie') or {}
        year = movie.get('year')
        title = movie.get('title', 'Unknown') + (f' ({year})' if year else '')
        poster = image_url(movie.get('images'), 'poster', size='w342')
        link = f'/movies/{r["movieId"]}/' if r.get('movieId') else None
        target = {'movie_ids': [r['movieId']]} if r.get('movieId') else {}
    return {
        'app': app,
        'id': r.get('id'),
        'event': r.get('eventType'),
        'label': label, 'icon': icon, 'color': color,
        'when': timezone.localtime(when) if when else None,
        'title': title,
        'release': r.get('sourceTitle') or '',
        'quality': ((r.get('quality') or {}).get('quality') or {}).get('name', ''),
        'indexer': (data.get('indexer') or '').replace(' (Prowlarr)', ''),
        'message': data.get('message') or data.get('reason') or '',
        'dropped': data.get('droppedPath') or '',
        'imported': data.get('importedPath') or '',
        'poster': poster,
        'link': link,
        'target_json': json.dumps({'app': app, **target}),
        'has_file': (r.get('episode') or r.get('movie') or {}).get('hasFile'),
    }


def _history(app, page_size=50, **params):
    if app == 'sonarr':
        data = sonarr().history(page_size=page_size, includeSeries='true', includeEpisode='true', **params)
    else:
        data = radarr().history(page_size=page_size, includeMovie='true', **params)
    return [_history_row(app, r) for r in data.get('records', [])]


def _both(fn):
    """Run fn('sonarr') / fn('radarr') in parallel for whichever are configured."""
    rows, errors = [], []
    apps = [a for a in ('sonarr', 'radarr') if is_configured(a)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {a: pool.submit(in_thread, fn, a) for a in apps}
        for a, f in futures.items():
            try:
                rows.extend(f.result())
            except ArrError as e:
                errors.append(f'{a.title()}: {e}')
    return rows, errors


def _wanted(app):
    now = timezone.now()
    if app == 'sonarr':
        data = sonarr().wanted_missing(page_size=100, monitored='true', includeSeries='true',
                                       sortKey='episodes.airDateUtc', sortDirection='descending')
        rows = []
        for ep in data.get('records', []):
            series = ep.get('series') or {}
            air = parse_dt(ep.get('airDateUtc'))
            rows.append({
                'app': 'sonarr', 'title': series.get('title') or '', 'poster': image_url(series.get('images'), 'poster'),
                'subtitle': f'S{ep.get("seasonNumber", 0):02d}E{ep.get("episodeNumber", 0):02d} · {ep.get("title") or "TBA"}',
                'when': timezone.localtime(air) if air else None, 'link': f'/shows/{ep.get("seriesId")}/',
                'last_search': parse_dt(ep.get('lastSearchTime')),
                'target_json': json.dumps({'app': 'sonarr', 'episode_ids': [ep['id']]}),
            })
        return rows
    rows = []
    for m in radarr().movies():
        if m.get('monitored') and not m.get('hasFile') and m.get('isAvailable'):
            when = parse_dt(m.get('digitalRelease')) or parse_dt(m.get('physicalRelease')) or parse_dt(m.get('inCinemas'))
            rows.append({
                'app': 'radarr', 'title': m.get('title') or '', 'poster': image_url(m.get('images'), 'poster', size='w342'),
                'subtitle': str(m.get('year') or ''), 'when': timezone.localtime(when) if when and when <= now else None,
                'link': f'/movies/{m["id"]}/', 'last_search': parse_dt(m.get('lastSearchTime')),
                'target_json': json.dumps({'app': 'radarr', 'movie_ids': [m['id']]}),
            })
    return rows


def _waiting(app):
    now = timezone.now()
    if app == 'sonarr':
        cal = sonarr().calendar(now.isoformat(), (now + timedelta(days=30)).isoformat(),
                                includeSeries='true', unmonitored='false')
        rows = []
        for ep in cal:
            if ep.get('hasFile'):
                continue
            series = ep.get('series') or {}
            air = parse_dt(ep.get('airDateUtc'))
            rows.append({
                'app': 'sonarr', 'title': series.get('title') or '', 'poster': image_url(series.get('images'), 'poster'),
                'subtitle': f'S{ep.get("seasonNumber", 0):02d}E{ep.get("episodeNumber", 0):02d} · {ep.get("title") or "TBA"}',
                'when': timezone.localtime(air) if air else None, 'link': f'/shows/{ep.get("seriesId")}/',
                'note': 'Sonarr grabs it once it airs',
            })
        return rows
    rows = []
    for m in radarr().movies():
        if m.get('monitored') and not m.get('hasFile') and not m.get('isAvailable'):
            dates = [parse_dt(m.get(k)) for k in ('digitalRelease', 'physicalRelease', 'inCinemas')]
            future = [d for d in dates if d and d > now]
            when = min(future) if future else None
            rows.append({
                'app': 'radarr', 'title': m.get('title') or '', 'poster': image_url(m.get('images'), 'poster', size='w342'),
                'subtitle': str(m.get('year') or ''), 'when': timezone.localtime(when) if when else None,
                'link': f'/movies/{m["id"]}/',
                'note': f'Radarr grabs it once it\'s {_AVAILABILITY.get(m.get("minimumAvailability"), "released")}',
            })
    return rows


_AVAILABILITY = {'announced': 'announced', 'inCinemas': 'in cinemas', 'released': 'released digitally or on disc'}


def queue_tab(request, tab):
    if tab == 'wanted':
        rows, errors = _both(_wanted)
        rows.sort(key=lambda r: r['when'] or timezone.now(), reverse=True)
    elif tab == 'waiting':
        # Split into TV (?app=sonarr) and Movies (?app=radarr) tabs
        app = request.GET.get('app')
        if app in ('sonarr', 'radarr'):
            rows, errors = [], []
            if is_configured(app):
                try:
                    rows = _waiting(app)
                except ArrError as e:
                    errors.append(f'{app.title()}: {e}')
        else:
            rows, errors = _both(_waiting)
        far = timezone.now() + timedelta(days=3650)
        rows.sort(key=lambda r: r['when'] or far)
    elif tab == 'history':
        rows, errors = _both(lambda a: _history(a, page_size=60))
        rows.sort(key=lambda r: r['when'] or timezone.now(), reverse=True)
        rows = rows[:100]
    elif tab == 'failed':
        rows, errors = _both(lambda a: _history(a, page_size=50, eventType=4))
        rows.sort(key=lambda r: r['when'] or timezone.now(), reverse=True)
    else:
        rows, errors = [], ['Unknown tab']
    return render(request, f'downloads/_tab_{tab if tab in ("wanted", "waiting", "history", "failed") else "history"}.html', {
        'rows': rows, 'errors': errors, 'tab': tab, 'app': request.GET.get('app', ''),
        'sonarr_ready': is_configured('sonarr'), 'radarr_ready': is_configured('radarr'),
    })


# ── Imports (was "File Moves") ────────────────────────────────────────────────

def _confirm(svc, imported_path, has_file):
    """Does the imported file really exist in the library?"""
    if not imported_path:
        return 'unknown'
    local = svc.to_local(imported_path)
    mapped = local != imported_path
    if os.path.exists(local):
        return 'confirmed'
    if not mapped:
        return 'in_library' if has_file else 'unknown'
    return 'replaced' if has_file else 'missing'


def imports_page(request):
    def _imports(app):
        svc = ArrService.get(app)
        rows = _history(app, page_size=75, eventType=3)
        for r in rows:
            r['confirm'] = _confirm(svc, r['imported'], r['has_file'])
            r['local'] = svc.to_local(r['imported'])
        return rows

    rows, errors = _both(_imports)
    rows.sort(key=lambda r: r['when'] or timezone.now(), reverse=True)
    status_filter = request.GET.get('status', '')
    counts = {k: sum(1 for r in rows if r['confirm'] == k) for k in ('confirmed', 'in_library', 'replaced', 'missing', 'unknown')}
    if status_filter in counts:
        rows = [r for r in rows if r['confirm'] == status_filter]
    return render(request, 'downloads/imports.html', {
        'rows': rows, 'errors': errors, 'counts': counts, 'status_filter': status_filter,
        'any_configured': is_configured('sonarr') or is_configured('radarr'),
    })
