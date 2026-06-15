import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone

from .models import TVShow, Season, Episode, Movie
from .tmdb import tmdb
from apps.downloads.models import DownloadItem


# ── Dashboard ────────────────────────────────────────────────────────────────

def dashboard(request):
    context = {
        'show_count': TVShow.objects.count(),
        'movie_count': Movie.objects.count(),
        'recent_shows': TVShow.objects.order_by('-added_at')[:6],
        'recent_movies': Movie.objects.order_by('-added_at')[:6],
        'downloading_count': DownloadItem.objects.filter(status=DownloadItem.Status.DOWNLOADING).count(),
        'queued_count': DownloadItem.objects.filter(status=DownloadItem.Status.PENDING).count(),
        'downloaded_count': DownloadItem.objects.filter(status=DownloadItem.Status.COMPLETED).count(),
        'favourite_shows': TVShow.objects.filter(is_favourite=True)[:6],
        'favourite_movies': Movie.objects.filter(is_favourite=True)[:6],
    }
    return render(request, 'dashboard.html', context)


def dashboard_discover(request):
    """AJAX — all 8 discover sliders, 30-min cached, fetched in parallel."""
    from django.core.cache import cache
    from datetime import date, timedelta
    import concurrent.futures

    today = date.today()
    this_week_start = today - timedelta(days=today.weekday())
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end   = last_week_start + timedelta(days=6)

    IMG_BASE  = 'https://image.tmdb.org/t/p/w300'
    STREAMING = '8|9|337|350|15|1899'   # Netflix|Prime|Disney+|Apple TV+|Hulu|Max
    CACHE_TTL = 1800

    def _raw(key, method, **kwargs):
        cache_key = f'disc_{key}_{today}'
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
        try:
            results = method(**kwargs).get('results', [])
        except Exception:
            results = []
        cache.set(cache_key, results, CACHE_TTL)
        return results

    def _fmt(results, is_tv):
        items = []
        for r in results[:20]:
            poster = r.get('poster_path')
            items.append({
                'tmdb_id': r.get('id'),
                'title':   r.get('name') if is_tv else r.get('title'),
                'poster':  f'{IMG_BASE}{poster}' if poster else None,
                'year':    (r.get('first_air_date') or r.get('release_date') or '')[:4],
                'rating':  round(r.get('vote_average', 0), 1),
            })
        return items

    spec = [
        ('tv_new_this_week', tmdb.discover_tv, True, {
            'first_air_date.gte': this_week_start.isoformat(),
            'first_air_date.lte': today.isoformat(),
            'sort_by': 'popularity.desc',
        }),
        ('tv_new_last_week', tmdb.discover_tv, True, {
            'first_air_date.gte': last_week_start.isoformat(),
            'first_air_date.lte': last_week_end.isoformat(),
            'sort_by': 'popularity.desc',
        }),
        ('tv_returning_this_week', tmdb.discover_tv, True, {
            'air_date.gte': this_week_start.isoformat(),
            'air_date.lte': today.isoformat(),
            'with_status': 0,
            'sort_by': 'popularity.desc',
        }),
        ('tv_returning_last_week', tmdb.discover_tv, True, {
            'air_date.gte': last_week_start.isoformat(),
            'air_date.lte': last_week_end.isoformat(),
            'with_status': 0,
            'sort_by': 'popularity.desc',
        }),
        ('movies_streaming_this_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': this_week_start.isoformat(),
            'primary_release_date.lte': today.isoformat(),
            'with_watch_providers': STREAMING,
            'watch_region': 'US',
            'sort_by': 'popularity.desc',
        }),
        ('movies_streaming_last_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': last_week_start.isoformat(),
            'primary_release_date.lte': last_week_end.isoformat(),
            'with_watch_providers': STREAMING,
            'watch_region': 'US',
            'sort_by': 'popularity.desc',
        }),
        ('movies_digital_this_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': this_week_start.isoformat(),
            'primary_release_date.lte': today.isoformat(),
            'with_release_type': 4,
            'sort_by': 'popularity.desc',
        }),
        ('movies_digital_last_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': last_week_start.isoformat(),
            'primary_release_date.lte': last_week_end.isoformat(),
            'with_release_type': 4,
            'sort_by': 'popularity.desc',
        }),
    ]

    def _fetch(entry):
        key, method, is_tv, kwargs = entry
        return key, _fmt(_raw(key, method, **kwargs), is_tv)

    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for key, items in pool.map(_fetch, spec):
            result[key] = items

    return JsonResponse(result)


# ── TV Shows ─────────────────────────────────────────────────────────────────

def tv_shows(request):
    qs = TVShow.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    status_filter = request.GET.get('status', '')
    if status_filter:
        qs = qs.filter(status=status_filter)
    context = {
        'shows': qs,
        'q': q,
        'status_filter': status_filter,
        'status_choices': TVShow.Status.choices,
    }
    return render(request, 'media_tracker/tv_shows.html', context)


def tv_show_detail(request, pk):
    show = get_object_or_404(TVShow, pk=pk)
    seasons = show.seasons.prefetch_related('episodes').all()
    context = {'show': show, 'seasons': seasons}
    return render(request, 'media_tracker/tv_show_detail.html', context)


@require_POST
def tv_show_toggle_favourite(request, pk):
    show = get_object_or_404(TVShow, pk=pk)
    show.is_favourite = not show.is_favourite
    show.save(update_fields=['is_favourite'])
    return JsonResponse({'is_favourite': show.is_favourite})


@require_POST
def tv_show_toggle_monitor(request, pk):
    show = get_object_or_404(TVShow, pk=pk)
    show.monitor_new_episodes = not show.monitor_new_episodes
    show.save(update_fields=['monitor_new_episodes'])
    return JsonResponse({'monitor_new_episodes': show.monitor_new_episodes})


@require_POST
def tv_show_delete(request, pk):
    show = get_object_or_404(TVShow, pk=pk)
    show.delete()
    if request.headers.get('HX-Request'):
        return render(request, 'media_tracker/_show_deleted.html', {'name': show.name})
    messages.success(request, f'Removed {show.name}')
    return redirect('tv_shows')


def tmdb_search_tv(request):
    query = request.GET.get('q', '').strip()
    results = []
    if query:
        try:
            data = tmdb.search_tv(query)
            existing_ids = set(TVShow.objects.values_list('tmdb_id', flat=True))
            results = [
                {**r, 'already_added': r['id'] in existing_ids}
                for r in data.get('results', [])[:12]
            ]
        except Exception as e:
            messages.error(request, f'TMDB search failed: {e}')
    context = {'results': results, 'query': query}
    if request.headers.get('HX-Request'):
        return render(request, 'media_tracker/_tv_search_results.html', context)
    return render(request, 'media_tracker/tmdb_search_tv.html', context)


@require_POST
def tv_show_add(request):
    tmdb_id = int(request.POST.get('tmdb_id'))
    if TVShow.objects.filter(tmdb_id=tmdb_id).exists():
        show = TVShow.objects.get(tmdb_id=tmdb_id)
        return JsonResponse({'status': 'exists', 'pk': show.pk, 'name': show.name})
    try:
        show = tmdb.sync_show_to_db(tmdb_id)
        return JsonResponse({'status': 'added', 'pk': show.pk, 'name': show.name})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@require_POST
def tv_show_queue_download(request, pk):
    """Queue download for a show — whole show, seasons, episodes, or monitor."""
    show = get_object_or_404(TVShow, pk=pk)
    mode = request.POST.get('mode', 'whole')  # whole | seasons | episodes | monitor
    monitor = request.POST.get('monitor', 'false') == 'true'

    if monitor:
        show.monitor_new_episodes = True
        show.save(update_fields=['monitor_new_episodes'])

    # Build (episode, season_number) pairs so we can pre-fill the search query
    ep_pairs = []  # list of (Episode, season_number)

    if mode == 'whole':
        for season in show.seasons.prefetch_related('episodes').all():
            for ep in season.episodes.all():
                ep_pairs.append((ep, season.season_number))
    elif mode == 'seasons':
        season_numbers = [int(s) for s in request.POST.getlist('seasons')]
        for season in show.seasons.filter(season_number__in=season_numbers).prefetch_related('episodes'):
            for ep in season.episodes.all():
                ep_pairs.append((ep, season.season_number))
    elif mode == 'episodes':
        episode_ids = [int(e) for e in request.POST.getlist('episode_ids')]
        eps = Episode.objects.filter(id__in=episode_ids, season__show=show).select_related('season')
        for ep in eps:
            ep_pairs.append((ep, ep.season.season_number))

    created = 0
    for ep, season_num in ep_pairs:
        if ep.download_status not in (Episode.DownloadStatus.QUEUED, Episode.DownloadStatus.DOWNLOADING, Episode.DownloadStatus.DOWNLOADED):
            sq = f'{show.name} S{season_num:02d}E{ep.episode_number:02d} 1080p'
            item, is_new = DownloadItem.objects.get_or_create(
                media_type=DownloadItem.MediaType.EPISODE,
                episode_id=ep.id,
                defaults={
                    'title': show.name,
                    'subtitle': str(ep),
                    'poster_path': show.poster_path,
                    'status': DownloadItem.Status.SEARCHING,
                    'release_date': ep.air_date,
                    'search_query': sq,
                },
            )
            if is_new:
                ep.download_status = Episode.DownloadStatus.QUEUED
                ep.save(update_fields=['download_status'])
                created += 1
                # Don't fire Celery here — browser handles the search when the
                # user lands on the queue page.  Celery would race and mark FAILED
                # before the browser even loads.

    return JsonResponse({'status': 'ok', 'queued': created})


# ── Movies ───────────────────────────────────────────────────────────────────

def movies(request):
    qs = Movie.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(title__icontains=q)
    status_filter = request.GET.get('status', '')
    if status_filter:
        qs = qs.filter(download_status=status_filter)
    context = {
        'movies': qs,
        'q': q,
        'status_filter': status_filter,
        'status_choices': Movie.DownloadStatus.choices,
    }
    return render(request, 'media_tracker/movies.html', context)


def movie_detail(request, pk):
    movie = get_object_or_404(Movie, pk=pk)
    return render(request, 'media_tracker/movie_detail.html', {'movie': movie})


@require_POST
def movie_toggle_favourite(request, pk):
    movie = get_object_or_404(Movie, pk=pk)
    movie.is_favourite = not movie.is_favourite
    movie.save(update_fields=['is_favourite'])
    return JsonResponse({'is_favourite': movie.is_favourite})


@require_POST
def movie_delete(request, pk):
    movie = get_object_or_404(Movie, pk=pk)
    name = movie.title
    movie.delete()
    if request.headers.get('HX-Request'):
        return render(request, 'media_tracker/_movie_deleted.html', {'title': name})
    messages.success(request, f'Removed {name}')
    return redirect('movies')


def tmdb_search_movie(request):
    query = request.GET.get('q', '').strip()
    results = []
    if query:
        try:
            data = tmdb.search_movie(query)
            existing_ids = set(Movie.objects.values_list('tmdb_id', flat=True))
            results = [
                {**r, 'already_added': r['id'] in existing_ids}
                for r in data.get('results', [])[:12]
            ]
        except Exception as e:
            messages.error(request, f'TMDB search failed: {e}')
    context = {'results': results, 'query': query}
    if request.headers.get('HX-Request'):
        return render(request, 'media_tracker/_movie_search_results.html', context)
    return render(request, 'media_tracker/tmdb_search_movie.html', context)


@require_POST
def movie_add(request):
    tmdb_id = int(request.POST.get('tmdb_id'))
    if Movie.objects.filter(tmdb_id=tmdb_id).exists():
        movie = Movie.objects.get(tmdb_id=tmdb_id)
        return JsonResponse({'status': 'exists', 'pk': movie.pk, 'title': movie.title})
    try:
        movie = tmdb.sync_movie_to_db(tmdb_id)
        # Auto-queue: if released queue immediately, else wait for digital release
        _queue_movie(movie)
        return JsonResponse({'status': 'added', 'pk': movie.pk, 'title': movie.title})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@require_POST
def movie_queue_download(request, pk):
    """Queue a movie download with a chosen quality (1080p or 2160p)."""
    movie = get_object_or_404(Movie, pk=pk)
    quality = request.POST.get('quality', '1080p')
    if quality not in ('1080p', '2160p'):
        quality = '1080p'
    _queue_movie(movie, quality=quality)
    return JsonResponse({'status': 'ok', 'quality': quality})


def _queue_movie(movie, quality='1080p'):
    from apps.downloads.models import DownloadItem
    if movie.is_digitally_available:
        initial_status = DownloadItem.Status.SEARCHING
        dl_status = Movie.DownloadStatus.QUEUED
    else:
        initial_status = DownloadItem.Status.WAITING_RELEASE
        dl_status = Movie.DownloadStatus.WAITING_RELEASE

    year = str(movie.release_date.year) if movie.release_date else ''
    search_query = ' '.join(filter(None, [movie.title, year, quality]))

    item, created = DownloadItem.objects.get_or_create(
        media_type=DownloadItem.MediaType.MOVIE,
        movie_id=movie.id,
        defaults={
            'title': movie.title,
            'poster_path': movie.poster_path,
            'status': initial_status,
            'release_date': movie.digital_release_date or movie.release_date,
            'quality': quality,
            'search_query': search_query,
        },
    )
    if not created and item.status == DownloadItem.Status.FAILED:
        # Allow re-queue with (possibly new) quality
        item.quality = quality
        item.status = initial_status
        item.error_message = ''
        item.search_query = search_query
        item.save(update_fields=['quality', 'status', 'error_message', 'search_query'])

    movie.download_status = dl_status
    movie.save(update_fields=['download_status'])
    # Browser handles the search when the user lands on the queue page.


