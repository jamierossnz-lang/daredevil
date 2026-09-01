import random

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import DiscoverItem, DiscoverDecision


def swipe_deck(request):
    return render(request, 'discover/swipe.html')


def deck_data(request):
    """Full current pool, shuffled — the deck is small/curated enough not to need pagination."""
    items = list(DiscoverItem.objects.all())
    random.shuffle(items)
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
    } for i in items]
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
def swipe_right_add(request, pk):
    """Right swipe, 'Add to Library' — track it, no download/monitor triggered."""
    item = get_object_or_404(DiscoverItem, pk=pk)
    _record_decision(item, DiscoverDecision.Direction.RIGHT)
    result = _add_to_library(item, download=False)
    item.delete()
    return JsonResponse(result)


@require_POST
def swipe_right_download(request, pk):
    """Right swipe, 'Add to Library + Download' — for unreleased titles this
    falls through to the same waiting-for-release / monitor behaviour the
    rest of the app already uses, it doesn't try to download something that
    doesn't exist yet."""
    item = get_object_or_404(DiscoverItem, pk=pk)
    _record_decision(item, DiscoverDecision.Direction.RIGHT)
    result = _add_to_library(item, download=True)
    item.delete()
    return JsonResponse(result)


def _add_to_library(item, download):
    from apps.media_tracker.tmdb import tmdb
    from apps.media_tracker.models import Movie, TVShow
    from apps.media_tracker.views import _queue_movie

    if item.media_type == DiscoverItem.MediaType.MOVIE:
        movie = Movie.objects.filter(tmdb_id=item.tmdb_id).first()
        if not movie:
            movie = tmdb.sync_movie_to_db(item.tmdb_id)
        if download:
            _queue_movie(movie, quality='1080p')
        return {'status': 'ok', 'title': movie.title, 'media_type': 'movie', 'pk': movie.pk}

    show = TVShow.objects.filter(tmdb_id=item.tmdb_id).first()
    if not show:
        show = tmdb.sync_show_to_db(item.tmdb_id)
        from apps.media_tracker.tasks import sync_tvmaze_show
        sync_tvmaze_show.delay(show.pk)
    if download:
        fields = []
        if not show.monitor_new_episodes:
            show.monitor_new_episodes = True
            fields.append('monitor_new_episodes')
        if show.monitor_from is None:
            show.monitor_from = timezone.now().date()
            fields.append('monitor_from')
        if fields:
            show.save(update_fields=fields)
    return {'status': 'ok', 'title': show.name, 'media_type': 'tv', 'pk': show.pk}
