"""
Search — replaces the old qBittorrent-plugin search with Prowlarr.

TV and movies only. Supports Prowlarr's search parameters for those two
types (https://wiki.servarr.com/prowlarr/search) — the {Token:value} query
parameters, indexer selection (all, all torrents, all usenet, or specific
indexers), categories and paging. Download hands a result to Sonarr (TV)
or Radarr (movies), which grab it, track it and import it into the library.
"""
import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .clients import ArrError
from .services import (
    identify_release, is_configured, normalize_search_result, prowlarr, push_release_to, send_to_downloader,
    wait_for_episodes,
)

log = logging.getLogger('daredevil.arr.search')

# Mirrors Prowlarr's own query-parameter modal (frontend/src/Search/QueryParameterModal.js)
SEARCH_TYPES = [
    {'key': 'tvsearch', 'label': 'TV', 'kind': 'sonarr', 'category': 5000, 'tokens': [
        {'token': '{ImdbId:tt1234567}', 'example': 'tt12345'},
        {'token': '{TvdbId:12345}', 'example': '12345'},
        {'token': '{TmdbId:12345}', 'example': '12345'},
        {'token': '{TvMazeId:12345}', 'example': '54321'},
        {'token': '{Season:00}', 'example': '01'},
        {'token': '{Episode:00}', 'example': '01'},
    ]},
    {'key': 'movie', 'label': 'Movies', 'kind': 'radarr', 'category': 2000, 'tokens': [
        {'token': '{ImdbId:tt1234567}', 'example': 'tt12345'},
        {'token': '{TmdbId:12345}', 'example': '12345'},
        {'token': '{Year:2000}', 'example': '2005'},
    ]},
]
_VALID_TYPES = {t['key'] for t in SEARCH_TYPES}


def search_page(request):
    configured = is_configured('prowlarr')
    indexers, categories, error = [], [], None
    if configured:
        try:
            c = prowlarr()
            indexers = [
                {'id': i['id'], 'name': i.get('name'), 'protocol': i.get('protocol'), 'privacy': i.get('privacy')}
                for i in c.indexers() if i.get('enable')
            ]
            top = {t['category'] for t in SEARCH_TYPES}
            categories = [
                {'id': cat['id'], 'name': cat['name'],
                 'subs': [{'id': s['id'], 'name': s['name']} for s in cat.get('subCategories') or []]}
                for cat in c.categories() if cat['id'] in top
            ]
        except ArrError as e:
            error = str(e)
    return render(request, 'search/prowlarr.html', {
        'configured': configured,
        'error': error,
        'indexers': indexers,
        'categories': categories,
        'search_types': SEARCH_TYPES,
        'initial': {
            'q': request.GET.get('q', ''),
            'type': request.GET.get('type') if request.GET.get('type') in _VALID_TYPES else 'tvsearch',
        },
        'sonarr_enabled': is_configured('sonarr'),
        'radarr_enabled': is_configured('radarr'),
    })


def search_run(request):
    query = request.GET.get('q', '').strip()
    search_type = request.GET.get('type', 'tvsearch')
    if search_type not in _VALID_TYPES:
        search_type = 'tvsearch'
    # Nothing picked → the whole TV (5000) or Movies (2000) tree
    categories = [int(c) for c in request.GET.getlist('cat') if c.lstrip('-').isdigit()] or \
        [next(t['category'] for t in SEARCH_TYPES if t['key'] == search_type)]
    indexer_ids = [int(i) for i in request.GET.getlist('indexer') if i.lstrip('-').isdigit()]
    try:
        limit = max(1, min(1000, int(request.GET.get('limit', 100))))
        offset = max(0, int(request.GET.get('offset', 0)))
    except ValueError:
        limit, offset = 100, 0
    if not query:
        return JsonResponse({'ok': False, 'error': 'Enter something to search for'}, status=400)
    log.info('search_run: q=%r type=%s indexers=%s cats=%s limit=%d offset=%d',
             query, search_type, indexer_ids, categories, limit, offset)
    try:
        raw = prowlarr().search(query=query, search_type=search_type, indexer_ids=indexer_ids or None,
                                categories=categories or None, limit=limit, offset=offset)
    except ArrError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=200)
    results = [normalize_search_result(r) for r in raw]
    return JsonResponse({'ok': True, 'results': results, 'count': len(results), 'offset': offset, 'limit': limit})


@require_POST
def search_grab(request):
    """
    Download: hand results to Sonarr (TV) or Radarr (movies).

    action = 'sonarr' / 'radarr' → pushed; the app decides whether it wants
               it, then grabs, tracks and imports it. Each outcome says why
               it was turned down, whether it's a show/movie that has to be
               added first ('unknown' + 'match'), or whether it can still be
               downloaded on request ('can_force').
           = 'force_sonarr' / 'force_radarr' → "Download anyway": straight
               to qBittorrent under that app's category, so it still imports.
    """
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid request'}, status=400)
    action = data.get('action', '')
    releases = data.get('releases') or []
    if not releases:
        return JsonResponse({'ok': False, 'error': 'Nothing selected'}, status=400)

    if action in ('force_sonarr', 'force_radarr'):
        kind = action.split('_', 1)[1]
        try:
            for r in releases:
                category = send_to_downloader(kind, r)
            return JsonResponse({'ok': True, 'category': category})
        except ArrError as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=200)

    if action not in ('sonarr', 'radarr'):
        return JsonResponse({'ok': False, 'error': 'Unknown action'}, status=400)
    # Straight after "Add & download this release": Sonarr can only match the
    # release once the new show's episodes have loaded. The user picked this
    # exact file, so if it's turned down only for quality/monitoring reasons
    # it's downloaded anyway (force_if_rejected).
    series_id = data.get('series_id')
    if action == 'sonarr' and str(series_id or '').isdigit():
        wait_for_episodes(int(series_id))
    outcomes = []
    for r in releases:
        try:
            outcome = push_release_to(action, r)
            if not outcome['approved'] and outcome['can_force'] and data.get('force_if_rejected'):
                send_to_downloader(action, r)
                outcome.update(approved=True, forced=True)
        except ArrError as e:
            outcome = {'approved': False, 'rejections': [str(e)], 'unknown': False, 'can_force': False}
        if outcome['unknown']:
            outcome['match'] = identify_release(action, r)
        outcomes.append({'title': r.get('title'), 'guid': r.get('guid'), **outcome})
    return JsonResponse({'ok': any(o['approved'] for o in outcomes), 'outcomes': outcomes})
