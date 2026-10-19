"""
Sonarr/Radarr → Daredevil webhooks (registered automatically from Settings).

This is what replaces the old polling jobs: Sonarr/Radarr call in the moment
they grab or import something. Daredevil doesn't move any files itself any
more — on an import it only *confirms* the file actually landed in the
library (on disk, via the path mappings), then notifies.
"""
import json
import logging
import os

from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.events.dispatch import emit
from apps.events.models import EventType

from .models import ArrService
from .services import invalidate_history, invalidate_library

log = logging.getLogger('daredevil.arr.webhooks')


@csrf_exempt
@require_POST
def receive(request, kind, secret):
    if kind not in ('sonarr', 'radarr'):
        raise Http404
    svc = ArrService.get(kind)
    if not svc.webhook_secret or not constant_time_compare(secret, svc.webhook_secret):
        return HttpResponseForbidden('bad secret')
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    event = payload.get('eventType') or ''
    log.info('webhook %s: %s', kind, event)
    # Something happened — the history pages shouldn't show a cached copy
    invalidate_history(kind)
    try:
        handler = _HANDLERS.get(event)
        if handler:
            handler(kind, svc, payload)
    except Exception:
        # Never make Sonarr/Radarr retry or mark the connection unhealthy
        # because of a Daredevil-side bug — log it and acknowledge.
        log.exception('webhook %s %s: handler failed', kind, event)
    return JsonResponse({'ok': True})


# ── Labels ────────────────────────────────────────────────────────────────────

def _episodes_label(payload):
    eps = payload.get('episodes') or []
    codes = [f'S{e.get("seasonNumber", 0):02d}E{e.get("episodeNumber", 0):02d}' for e in eps]
    if len(codes) > 3:
        return f'{codes[0]}–{codes[-1]} ({len(codes)} episodes)'
    return ', '.join(codes)


def _label(kind, payload):
    if kind == 'sonarr':
        title = (payload.get('series') or {}).get('title') or 'Unknown show'
        eps = _episodes_label(payload)
        return f'{title} {eps}'.strip()
    movie = payload.get('movie') or {}
    year = movie.get('year')
    return f'{movie.get("title") or "Unknown movie"}{f" ({year})" if year else ""}'


# ── Handlers ──────────────────────────────────────────────────────────────────

def _on_test(kind, svc, payload):
    emit(
        EventType.FILE_MOVED,
        log_payload={'title': f'{kind.title()} test event', 'app': kind, 'test': True},
        title=f'{kind.title()} connected',
        message=f'Test webhook from {kind.title()} received — Daredevil will now hear about grabs and imports.',
        priority='low', tags=['white_check_mark'],
    )


def _on_grab(kind, svc, payload):
    release = payload.get('release') or {}
    label = _label(kind, payload)
    quality = release.get('quality') or ''
    detail = ' · '.join(x for x in (quality, (release.get('indexer') or '').replace(' (Prowlarr)', '')) if x)
    if kind == 'sonarr':
        eps = payload.get('episodes') or []
        first = eps[0] if eps else {}
        emit(
            EventType.EPISODE_QUEUED,
            log_payload={
                'app': kind, 'count': len(eps), 'show': (payload.get('series') or {}).get('title'),
                'season': first.get('seasonNumber'), 'episode': first.get('episodeNumber'),
                'release': release.get('releaseTitle'), 'series_id': (payload.get('series') or {}).get('id'),
            },
            title='Episode Grabbed' if len(eps) == 1 else 'Episodes Grabbed',
            message=f'{label}{f" — {detail}" if detail else ""}',
            tags=['tv', 'arrow_down'],
        )
    else:
        movie = payload.get('movie') or {}
        emit(
            EventType.MOVIE_AVAILABLE,
            log_payload={
                'app': kind, 'title': movie.get('title'), 'year': movie.get('year'),
                'release': release.get('releaseTitle'), 'movie_id': movie.get('id'),
            },
            title='Movie Grabbed',
            message=f'{label}{f" — {detail}" if detail else ""}',
            tags=['clapper', 'arrow_down'],
        )


def _imported_path(kind, payload):
    f = payload.get('episodeFile' if kind == 'sonarr' else 'movieFile') or {}
    if f.get('path'):
        return f['path']
    base = (payload.get('series') or {}).get('path') if kind == 'sonarr' else (payload.get('movie') or {}).get('folderPath')
    rel = f.get('relativePath')
    return os.path.join(base, rel) if base and rel else None


def confirm_on_disk(svc, remote_path):
    """True/False if the imported file can be checked from inside Daredevil,
    None if there's no path mapping for it (nothing to check against)."""
    if not remote_path:
        return None
    local = svc.to_local(remote_path)
    if local == remote_path and not os.path.exists(local):
        return None
    return os.path.exists(local)


def _on_download(kind, svc, payload):
    label = _label(kind, payload)
    path = _imported_path(kind, payload)
    confirmed = confirm_on_disk(svc, path)
    upgrade = bool(payload.get('isUpgrade'))
    log_payload = {
        'app': kind, 'title': label, 'path': path, 'confirmed': confirmed, 'upgrade': upgrade,
        'series_id': (payload.get('series') or {}).get('id'), 'movie_id': (payload.get('movie') or {}).get('id'),
    }
    if confirmed is False:
        emit(
            EventType.FILE_MOVE_FAILED,
            log_payload={**log_payload, 'error': f'{kind.title()} reported an import but the file is not visible at {svc.to_local(path)}'},
            title='Import Not Confirmed',
            message=f'{label} — {kind.title()} says it imported it, but Daredevil can\'t see the file. Check the path mappings in Settings → {kind.title()}.',
            priority='high', tags=['warning'],
        )
        return
    emit(
        EventType.FILE_MOVED,
        log_payload=log_payload,
        title='Upgraded' if upgrade else 'Ready to Watch',
        message=f'{label} {"upgraded" if upgrade else "imported to library"}{" ✓" if confirmed else ""}',
        priority='low', tags=['tada'],
    )


def _on_manual_interaction(kind, svc, payload):
    label = _label(kind, payload)
    msgs = []
    for sm in payload.get('downloadStatusMessages') or []:
        msgs.extend(sm.get('messages') or [sm.get('title') or ''])
    reason = '; '.join(m for m in msgs if m) or 'manual import required'
    emit(
        EventType.FILE_MOVE_FAILED,
        log_payload={'app': kind, 'title': label, 'error': reason},
        title='Import Needs Attention',
        message=f'{label} — {reason}',
        priority='high', tags=['warning'],
    )


def _on_library_change(kind, svc, payload):
    invalidate_library(kind)


_HANDLERS = {
    'Test': _on_test,
    'Grab': _on_grab,
    'Download': _on_download,
    'ManualInteractionRequired': _on_manual_interaction,
    'SeriesAdd': _on_library_change,
    'SeriesDelete': _on_library_change,
    'MovieAdded': _on_library_change,
    'MovieDelete': _on_library_change,
}
