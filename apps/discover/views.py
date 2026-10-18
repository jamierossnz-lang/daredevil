from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from apps.arr.http import json_body

from .models import DiscoverDecision, DiscoverItem, DiscoverSettings

# Deck order: two recommendations, then a trending pick, and so on
RECS_PER_TRENDING = 2


def swipe_deck(request):
    return render(request, 'discover/swipe.html')


def _ordered_deck():
    recs = sorted(DiscoverItem.objects.filter(source=DiscoverItem.Source.RECOMMENDATION),
                  key=lambda i: (len(i.because), i.score), reverse=True)
    trending = list(DiscoverItem.objects.filter(source=DiscoverItem.Source.ANTICIPATED).order_by('-score', '-added_at'))
    deck = []
    while recs or trending:
        deck += recs[:RECS_PER_TRENDING]
        del recs[:RECS_PER_TRENDING]
        if trending:
            deck.append(trending.pop(0))
    return deck


def deck_data(request):
    data = [{
        'id': i.pk,
        'tmdb_id': i.tmdb_id,
        'media_type': i.media_type,
        'title': i.title,
        'overview': i.overview,
        'poster_url': i.poster_url,
        'backdrop_url': i.backdrop_url,
        'release_date': i.release_date.isoformat() if i.release_date else None,
        'is_released': i.is_released,
        'vote_average': round(i.vote_average, 1),
        'genres': [g.strip() for g in i.genres.split(',') if g.strip()],
        'source': i.source,
        'source_title': i.source_title,
        'because': i.because,
    } for i in _ordered_deck()]
    return JsonResponse({'items': data})


def trailer(request, pk):
    """
    Fetched on demand rather than cached on the DiscoverItem at pool-
    generation time — an unreleased title may not have a trailer yet when
    it's first added to the deck, but could by the time the user actually
    looks at the card.
    """
    item = get_object_or_404(DiscoverItem, pk=pk)
    from apps.media_tracker.tmdb import tmdb

    try:
        if item.media_type == DiscoverItem.MediaType.MOVIE:
            data = tmdb.get_movie_videos(item.tmdb_id)
        else:
            data = tmdb.get_tv_videos(item.tmdb_id)
    except Exception:
        return JsonResponse({'key': None})

    return JsonResponse({'key': _pick_trailer_key(data.get('results', []))})


def _pick_trailer_key(results):
    candidates = [v for v in results if v.get('site') == 'YouTube' and v.get('type') in ('Trailer', 'Teaser')]
    if not candidates:
        return None
    # Prefer an official Trailer over a Teaser or fan-made upload.
    candidates.sort(key=lambda v: (v.get('type') == 'Trailer', v.get('official', False)), reverse=True)
    return candidates[0].get('key')


def _record_decision(item, direction):
    DiscoverDecision.objects.update_or_create(
        tmdb_id=item.tmdb_id, media_type=item.media_type,
        defaults={'direction': direction},
    )


@require_POST
def swipe_left(request, pk):
    item = get_object_or_404(DiscoverItem, pk=pk)
    _record_decision(item, DiscoverDecision.Direction.LEFT)
    item.delete()
    return JsonResponse({'status': 'ok'})


@require_POST
def swipe_right(request, pk):
    """Right swipe — called once the show/movie has been added through the
    shared Add dialog (tagged 'for-you' in Sonarr/Radarr)."""
    item = get_object_or_404(DiscoverItem, pk=pk)
    _record_decision(item, DiscoverDecision.Direction.RIGHT)
    item.delete()
    return JsonResponse({'status': 'ok'})


# ── App Settings → For You ────────────────────────────────────────────────────

DECK_SIZES = (10, 20, 40, 60, 100, 200)


def _settings_payload():
    cfg = DiscoverSettings.get()
    items = DiscoverItem.objects
    decisions = DiscoverDecision.objects
    return {
        'ok': True,
        'settings': {
            'deck_size': cfg.deck_size, 'min_matches': cfg.min_matches, 'include_movies': cfg.include_movies,
            'include_tv': cfg.include_tv, 'include_trending': cfg.include_trending,
        },
        'deck_sizes': DECK_SIZES,
        'stats': {
            'waiting': items.count(),
            'recommendations': items.filter(source=DiscoverItem.Source.RECOMMENDATION).count(),
            'trending': items.filter(source=DiscoverItem.Source.ANTICIPATED).count(),
            'liked': decisions.filter(direction=DiscoverDecision.Direction.RIGHT).count(),
            'passed': decisions.filter(direction=DiscoverDecision.Direction.LEFT).count(),
        },
    }


def settings_view(request):
    """GET: settings + deck stats. POST: save settings."""
    if request.method == 'POST':
        data = json_body(request)
        cfg = DiscoverSettings.get()
        if str(data.get('deck_size', '')).isdigit():
            cfg.deck_size = min(max(int(data['deck_size']), 5), 500)
        if str(data.get('min_matches', '')).isdigit():
            cfg.min_matches = min(max(int(data['min_matches']), 1), 10)
        for key in ('include_movies', 'include_tv', 'include_trending'):
            if key in data:
                setattr(cfg, key, bool(data[key]))
        if not (cfg.include_movies or cfg.include_tv):
            return JsonResponse({'ok': False, 'error': 'Keep movies or TV switched on'}, status=400)
        cfg.save()
    return JsonResponse(_settings_payload())


@require_POST
def clear_deck(request):
    """Empty the deck — the next rebuild (nightly, or Rebuild now) refills it."""
    removed = DiscoverItem.objects.all().delete()[0]
    return JsonResponse({**_settings_payload(), 'message': f'Cleared {removed} card{"" if removed == 1 else "s"}'})


@require_POST
def reset_history(request):
    """Forget every swipe, so titles passed on before can come back."""
    removed = DiscoverDecision.objects.all().delete()[0]
    return JsonResponse({**_settings_payload(), 'message': f'Forgot {removed} swipe{"" if removed == 1 else "s"}'})


@require_POST
def rebuild(request):
    from .tasks import rebuild_for_you
    try:
        return JsonResponse({'ok': True, 'task_id': rebuild_for_you.delay().id})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'Could not queue the rebuild — is Celery running? ({e})'}, status=500)
