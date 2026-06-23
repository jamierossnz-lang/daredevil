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
    from datetime import timedelta

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    week_ago_date = timezone.localdate() - timedelta(days=7)

    upcoming_episodes = list(
        Episode.objects
        .filter(
            download_status=Episode.DownloadStatus.AWAITING_RELEASE,
            season__show__monitor_new_episodes=True,
        )
        .select_related('season__show')
        .order_by('air_datetime', 'air_date')
        [:20]
    )

    new_episodes = list(
        Episode.objects
        .filter(
            season__show__monitor_new_episodes=True,
            download_status__in=[
                Episode.DownloadStatus.WAITING_FOR_DOWNLOAD,
                Episode.DownloadStatus.QUEUED,
                Episode.DownloadStatus.DOWNLOADING,
                Episode.DownloadStatus.DOWNLOADED,
            ],
        )
        .filter(
            Q(air_datetime__gte=week_ago) |
            Q(air_datetime__isnull=True, air_date__gte=week_ago_date)
        )
        .select_related('season__show')
        .order_by('-air_datetime', '-air_date')
        [:20]
    )

    context = {
        'show_count': TVShow.objects.count(),
        'movie_count': Movie.objects.count(),
        'downloading_count': DownloadItem.objects.filter(status=DownloadItem.Status.DOWNLOADING).count(),
        'queued_count': DownloadItem.objects.filter(status=DownloadItem.Status.PENDING).count(),
        'downloaded_count': DownloadItem.objects.filter(status=DownloadItem.Status.COMPLETED).count(),
        'upcoming_episodes': upcoming_episodes,
        'new_episodes': new_episodes,
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
            'with_original_language': 'en',
        }),
        ('tv_new_last_week', tmdb.discover_tv, True, {
            'first_air_date.gte': last_week_start.isoformat(),
            'first_air_date.lte': last_week_end.isoformat(),
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        ('tv_returning_this_week', tmdb.discover_tv, True, {
            'air_date.gte': this_week_start.isoformat(),
            'air_date.lte': today.isoformat(),
            'with_status': 0,
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        ('tv_returning_last_week', tmdb.discover_tv, True, {
            'air_date.gte': last_week_start.isoformat(),
            'air_date.lte': last_week_end.isoformat(),
            'with_status': 0,
            'sort_by': 'popularity.desc',
            'with_original_language': 'en',
        }),
        # Movies new to rent/stream (digital release type 4) — replaces old streaming+digital
        ('movies_rent_stream_this_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': (this_week_start - timedelta(days=14)).isoformat(),
            'primary_release_date.lte': today.isoformat(),
            'with_release_type': '4',
            'sort_by': 'popularity.desc',
        }),
        ('movies_rent_stream_last_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': (last_week_start - timedelta(days=14)).isoformat(),
            'primary_release_date.lte': last_week_end.isoformat(),
            'with_release_type': '4',
            'sort_by': 'popularity.desc',
        }),
        # Movies released theatrically this/last week
        ('movies_released_this_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': this_week_start.isoformat(),
            'primary_release_date.lte': today.isoformat(),
            'with_release_type': '3',
            'sort_by': 'popularity.desc',
        }),
        ('movies_released_last_week', tmdb.discover_movie, False, {
            'primary_release_date.gte': last_week_start.isoformat(),
            'primary_release_date.lte': last_week_end.isoformat(),
            'with_release_type': '3',
            'sort_by': 'popularity.desc',
        }),
        # Upcoming / announced movies (next 6 months, by popularity)
        ('movies_announced', tmdb.discover_movie, False, {
            'primary_release_date.gte': today.isoformat(),
            'primary_release_date.lte': (today + timedelta(days=180)).isoformat(),
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
    from django.db.models import Count, Q
    show = get_object_or_404(TVShow, pk=pk)
    seasons = (
        show.seasons
        .prefetch_related('episodes')
        .annotate(
            downloaded_count=Count('episodes', filter=Q(episodes__download_status='downloaded')),
            active_count=Count('episodes', filter=Q(episodes__download_status__in=['queued', 'downloading'])),
        )
        .order_by('season_number')
    )
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
    fields = ['monitor_new_episodes']
    if show.monitor_new_episodes and show.monitor_from is None:
        show.monitor_from = timezone.now().date()
        fields.append('monitor_from')
    show.save(update_fields=fields)
    if show.monitor_new_episodes:
        # Apply awaiting_release/waiting_for_download statuses (also syncs TVMaze if needed)
        from .tasks import sync_tvmaze_show
        sync_tvmaze_show.delay(show.pk)
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
        # Fetch precise TVMaze air times asynchronously so the response is fast
        from .tasks import sync_tvmaze_show
        sync_tvmaze_show.delay(show.pk)
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
        fields = ['monitor_new_episodes']
        if show.monitor_from is None:
            show.monitor_from = timezone.now().date()
            fields.append('monitor_from')
        show.save(update_fields=fields)

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
            ep_quality = show.preferred_quality if show.preferred_quality != 'auto' else '1080p'
            sq = f'{show.name} S{season_num:02d}E{ep.episode_number:02d} {ep_quality}'
            item, is_new = DownloadItem.objects.get_or_create(
                media_type=DownloadItem.MediaType.EPISODE,
                episode_id=ep.id,
                defaults={
                    'title': show.name,
                    'subtitle': str(ep),
                    'poster_path': show.poster_path,
                    'status': DownloadItem.Status.SEARCHING,
                    'release_date': ep.air_date,
                    'quality': ep_quality,
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
    IMG_BASE = 'https://image.tmdb.org/t/p/w185'
    POSTER_BASE = 'https://image.tmdb.org/t/p/w300'

    cast = []
    directors = []
    try:
        credits = tmdb.get_movie_credits(movie.tmdb_id)
        for p in credits.get('crew', []):
            if p.get('job') == 'Director':
                directors.append(p['name'])
        for c in credits.get('cast', [])[:10]:
            profile = c.get('profile_path')
            cast.append({
                'name': c.get('name', ''),
                'character': c.get('character', ''),
                'photo': f'{IMG_BASE}{profile}' if profile else None,
            })
    except Exception:
        pass

    similar = []
    try:
        existing_ids = set(Movie.objects.values_list('tmdb_id', flat=True))
        recs = tmdb.get_movie_recommendations(movie.tmdb_id)
        for r in recs.get('results', [])[:12]:
            poster = r.get('poster_path')
            similar.append({
                'tmdb_id': r.get('id'),
                'title': r.get('title', ''),
                'year': (r.get('release_date') or '')[:4],
                'rating': round(r.get('vote_average', 0), 1),
                'poster': f'{POSTER_BASE}{poster}' if poster else None,
                'already_added': r.get('id') in existing_ids,
            })
    except Exception:
        pass

    return render(request, 'media_tracker/movie_detail.html', {
        'movie': movie,
        'cast': cast,
        'directors': directors,
        'similar': similar,
    })


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
    quality = request.POST.get('quality', '1080p')
    if quality not in ('1080p', '2160p'):
        quality = '1080p'
    if Movie.objects.filter(tmdb_id=tmdb_id).exists():
        movie = Movie.objects.get(tmdb_id=tmdb_id)
        item = _queue_movie(movie, quality=quality)
        return JsonResponse({'status': 'exists', 'pk': movie.pk, 'title': movie.title,
                             'download_item_pk': item.pk if item else None})
    try:
        movie = tmdb.sync_movie_to_db(tmdb_id)
        item = _queue_movie(movie, quality=quality)
        return JsonResponse({'status': 'added', 'pk': movie.pk, 'title': movie.title,
                             'download_item_pk': item.pk if item else None,
                             'search_query': item.search_query if item else ''})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@require_POST
def movie_queue_download(request, pk):
    """Queue a movie download with a chosen quality (1080p or 2160p)."""
    movie = get_object_or_404(Movie, pk=pk)
    quality = request.POST.get('quality', '1080p')
    if quality not in ('1080p', '2160p'):
        quality = '1080p'
    item = _queue_movie(movie, quality=quality)
    return JsonResponse({'status': 'ok', 'quality': quality,
                         'download_item_pk': item.pk if item else None})


@require_POST
def movie_reset_download(request, pk):
    """Clear all download state so the movie can be re-queued from scratch."""
    movie = get_object_or_404(Movie, pk=pk)
    DownloadItem.objects.filter(
        media_type=DownloadItem.MediaType.MOVIE,
        movie_id=movie.id,
    ).delete()
    movie.download_status = Movie.DownloadStatus.NONE
    movie.save(update_fields=['download_status'])
    return JsonResponse({'status': 'ok'})


@require_POST
def tv_show_reset_download(request, pk):
    """Clear download state for all episodes so they can be re-queued."""
    show = get_object_or_404(TVShow, pk=pk)
    episode_ids = list(
        Episode.objects.filter(season__show=show).values_list('id', flat=True)
    )
    DownloadItem.objects.filter(
        media_type=DownloadItem.MediaType.EPISODE,
        episode_id__in=episode_ids,
    ).delete()
    Episode.objects.filter(season__show=show).update(
        download_status=Episode.DownloadStatus.NONE
    )
    return JsonResponse({'status': 'ok'})


# ── App Settings + Restart ───────────────────────────────────────────────────

def app_settings_view(request):
    from django.conf import settings as s
    return render(request, 'app_settings.html', {
        'cfg': {
            'TMDB_API_KEY':        s.TMDB_API_KEY,
            'DOWNLOAD_PATH':       s.DOWNLOAD_PATH,
            'REDIS_URL':           s.REDIS_URL,
            'TZ':                  s.TIME_ZONE,
            'DEBUG':               s.DEBUG,
            'ALLOWED_HOSTS':       ','.join(s.ALLOWED_HOSTS),
            'PLEX_CLAIM':          s.PLEX_CLAIM,
            'PLEX_URL':            s.PLEX_URL,
            'PLEX_TOKEN':          s.PLEX_TOKEN,
            'PLEX_MOVIE_SECTION':  s.PLEX_MOVIE_SECTION,
            'PLEX_TV_SECTION':     s.PLEX_TV_SECTION,
            'NTFY_URL':            s.NTFY_URL,
            'NTFY_TOPIC':          s.NTFY_TOPIC,
            'NTFY_TOKEN':          s.NTFY_TOKEN,
            'PUID':                s.PUID,
            'PGID':                s.PGID,
        }
    })


@require_POST
def app_settings_save(request):
    from django.conf import settings as s
    data = json.loads(request.body)

    live = {}
    env_updates = {}
    needs_restart = []

    def _set(key, val, attr=None, restart=False):
        env_updates[key] = str(val)
        if restart:
            needs_restart.append(key)
        elif attr:
            setattr(s, attr, val)
            live[key] = val

    if 'TMDB_API_KEY' in data:
        _set('TMDB_API_KEY', data['TMDB_API_KEY'].strip(), attr='TMDB_API_KEY')
    if 'DOWNLOAD_PATH' in data:
        _set('DOWNLOAD_PATH', data['DOWNLOAD_PATH'].strip(), attr='DOWNLOAD_PATH')
    if 'REDIS_URL' in data:
        _set('REDIS_URL', data['REDIS_URL'].strip(), restart=True)
    if 'TZ' in data:
        _set('TZ', data['TZ'].strip(), restart=True)
    if 'DEBUG' in data:
        _set('DEBUG', 'True' if data['DEBUG'] else 'False', restart=True)
    if 'ALLOWED_HOSTS' in data:
        _set('ALLOWED_HOSTS', data['ALLOWED_HOSTS'].strip(), restart=True)
    if 'PLEX_CLAIM' in data:
        _set('PLEX_CLAIM', data['PLEX_CLAIM'].strip(), attr='PLEX_CLAIM')
    if 'PLEX_URL' in data:
        _set('PLEX_URL', data['PLEX_URL'].strip(), attr='PLEX_URL')
    if 'PLEX_TOKEN' in data:
        _set('PLEX_TOKEN', data['PLEX_TOKEN'].strip(), attr='PLEX_TOKEN')
    if 'PLEX_MOVIE_SECTION' in data:
        _set('PLEX_MOVIE_SECTION', data['PLEX_MOVIE_SECTION'].strip(), attr='PLEX_MOVIE_SECTION')
    if 'PLEX_TV_SECTION' in data:
        _set('PLEX_TV_SECTION', data['PLEX_TV_SECTION'].strip(), attr='PLEX_TV_SECTION')
    if 'NTFY_URL' in data:
        _set('NTFY_URL', data['NTFY_URL'].strip(), attr='NTFY_URL')
    if 'NTFY_TOPIC' in data:
        _set('NTFY_TOPIC', data['NTFY_TOPIC'].strip(), attr='NTFY_TOPIC')
    if 'NTFY_TOKEN' in data:
        _set('NTFY_TOKEN', data['NTFY_TOKEN'].strip(), attr='NTFY_TOKEN')
    if 'PUID' in data:
        _set('PUID', str(data['PUID']).strip(), attr='PUID')
    if 'PGID' in data:
        _set('PGID', str(data['PGID']).strip(), attr='PGID')

    _update_env_file(env_updates)

    return JsonResponse({
        'ok': True,
        'live': list(live.keys()),
        'restart_required': needs_restart,
    })


def _update_env_file(updates: dict):
    import re
    from django.conf import settings as s
    env_file = s.BASE_DIR / '.env'
    if not env_file.exists():
        return
    text = env_file.read_text()
    for key, value in updates.items():
        pattern = re.compile(rf'^{re.escape(key)}\s*=.*', re.MULTILINE)
        replacement = f'{key}={value}'
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip('\n') + f'\n{replacement}\n'
    env_file.write_text(text)


def server_ping(request):
    return JsonResponse({'ok': True})


@require_POST
def server_restart(request):
    import threading
    from django.conf import settings as s

    def _touch_and_restart():
        import time
        time.sleep(0.4)
        manage_py = s.BASE_DIR / 'manage.py'
        manage_py.touch()

    threading.Thread(target=_touch_and_restart, daemon=True).start()
    return JsonResponse({'ok': True, 'msg': 'Django server restarting via auto-reloader…'})


@require_POST
def celery_restart(request):
    try:
        from config.celery import app as celery_app
        celery_app.control.broadcast('pool_restart', arguments={'reload': True})
        return JsonResponse({'ok': True, 'msg': 'Celery pool restart broadcast sent'})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)


@require_POST
def ntfy_test(request):
    """Send a test notification using the ntfy settings from the request body."""
    import requests as http_requests
    data = json.loads(request.body)
    ntfy_url   = (data.get('NTFY_URL') or 'https://ntfy.sh').rstrip('/')
    ntfy_topic = (data.get('NTFY_TOPIC') or '').strip()
    ntfy_token = (data.get('NTFY_TOKEN') or '').strip()

    if not ntfy_topic:
        return JsonResponse({'ok': False, 'error': 'NTFY_TOPIC is required'})

    headers = {'Title': 'Daredevil Test', 'Priority': '3', 'Tags': 'tada,clapper'}
    if ntfy_token:
        headers['Authorization'] = f'Bearer {ntfy_token}'

    try:
        r = http_requests.post(
            f'{ntfy_url}/{ntfy_topic}',
            data='Test notification from Daredevil — notifications are working!'.encode(),
            headers=headers,
            timeout=8,
        )
        r.raise_for_status()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


@require_POST
def quality_profiles_save(request):
    """Save size brackets for all quality+media_type combos. Accepts JSON list."""
    from .models import QualityProfile
    data = json.loads(request.body)
    valid = {('1080p', 'tv'), ('2160p', 'tv'), ('1080p', 'movie'), ('2160p', 'movie')}
    for item in data:
        key = (item.get('quality'), item.get('media_type'))
        if key not in valid:
            continue
        QualityProfile.objects.update_or_create(
            quality=key[0],
            media_type=key[1],
            defaults={
                'min_size_mb': int(item.get('min_size_mb') or 0) or None,
                'max_size_mb': int(item.get('max_size_mb') or 0) or None,
            },
        )
    return JsonResponse({'ok': True})


@require_POST
def tv_show_quality_save(request, pk):
    """Set preferred_quality on a TVShow ('auto', '1080p', or '2160p')."""
    show = get_object_or_404(TVShow, pk=pk)
    data = json.loads(request.body)
    quality = data.get('quality', 'auto')
    if quality not in ('auto', '1080p', '2160p'):
        return JsonResponse({'error': 'Invalid quality'}, status=400)
    show.preferred_quality = quality
    show.save(update_fields=['preferred_quality'])
    return JsonResponse({'preferred_quality': show.preferred_quality})


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
    return item


# ── Streaming browse ─────────────────────────────────────────────────────────

STREAMING_PROVIDERS = [
    {'key': 'netflix',  'id': 8,    'name': 'Netflix',      'bg': 'bg-red-700',     'ring': 'ring-red-500'},
    {'key': 'disney',   'id': 337,  'name': 'Disney+',      'bg': 'bg-blue-800',    'ring': 'ring-blue-400'},
    {'key': 'max',      'id': 1899, 'name': 'Max',          'bg': 'bg-purple-800',  'ring': 'ring-purple-400'},
    {'key': 'peacock',  'id': 386,  'name': 'Peacock',      'bg': 'bg-yellow-600',  'ring': 'ring-yellow-400'},
    {'key': 'appletv',  'id': 350,  'name': 'Apple TV+',    'bg': 'bg-gray-600',    'ring': 'ring-gray-400'},
    {'key': 'amazon',   'id': 9,    'name': 'Prime Video',  'bg': 'bg-cyan-700',    'ring': 'ring-cyan-400'},
]


def streaming_browse(request):
    provider_key = request.GET.get('provider', 'netflix')
    media_type = request.GET.get('type', 'movie')
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    provider = next((p for p in STREAMING_PROVIDERS if p['key'] == provider_key), STREAMING_PROVIDERS[0])

    items = []
    total_pages = 1
    error = None
    try:
        if media_type == 'tv':
            data = tmdb.discover_tv(
                with_watch_providers=provider['id'],
                watch_region='US',
                sort_by='popularity.desc',
                page=page,
            )
            existing_ids = set(TVShow.objects.values_list('tmdb_id', flat=True))
            for r in data.get('results', []):
                items.append({
                    'id': r['id'],
                    'title': r.get('name', ''),
                    'year': (r.get('first_air_date') or '')[:4],
                    'poster_path': r.get('poster_path', ''),
                    'vote_average': r.get('vote_average', 0),
                    'already_added': r['id'] in existing_ids,
                    'media': 'tv',
                })
        else:
            data = tmdb.discover_movie(
                with_watch_providers=provider['id'],
                watch_region='US',
                sort_by='popularity.desc',
                page=page,
            )
            existing_ids = set(Movie.objects.values_list('tmdb_id', flat=True))
            for r in data.get('results', []):
                items.append({
                    'id': r['id'],
                    'title': r.get('title', ''),
                    'year': (r.get('release_date') or '')[:4],
                    'poster_path': r.get('poster_path', ''),
                    'vote_average': r.get('vote_average', 0),
                    'already_added': r['id'] in existing_ids,
                    'media': 'movie',
                })
        total_pages = min(data.get('total_pages', 1), 20)
    except Exception as e:
        error = str(e)

    return render(request, 'media_tracker/streaming.html', {
        'items': items,
        'providers': STREAMING_PROVIDERS,
        'provider': provider,
        'provider_key': provider_key,
        'media_type': media_type,
        'page': page,
        'total_pages': total_pages,
        'error': error,
    })


