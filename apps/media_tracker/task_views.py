import json
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_celery_beat.models import PeriodicTask, IntervalSchedule
from django_celery_results.models import TaskResult

TASK_REGISTRY = {
    'sync_all_shows': {
        'label': 'Sync All TV Shows',
        'description': 'Re-fetch every tracked show from TMDB + TVMaze (air times) and update episode statuses.',
        'icon': 'fa-tv',
        'color': 'brand',
    },
    'update_episode_statuses': {
        'label': 'Update Episode Statuses',
        'description': 'Transition awaiting_release → waiting_for_download for episodes whose NZT air time has passed.',
        'icon': 'fa-satellite-dish',
        'color': 'purple',
    },
    'queue_waiting_episodes': {
        'label': 'Queue Waiting Episodes',
        'description': 'Queue waiting_for_download episodes on monitored shows for immediate download.',
        'icon': 'fa-rss',
        'color': 'brand',
    },
    'check_movie_releases': {
        'label': 'Check Movie Releases',
        'description': 'Check if any waiting movies are now digitally available and move them to the download queue.',
        'icon': 'fa-film',
        'color': 'cyan',
    },
    'sync_download_progress': {
        'label': 'Sync Download Progress',
        'description': 'Poll qBittorrent for progress updates on all active downloads.',
        'icon': 'fa-arrow-down',
        'color': 'yellow',
    },
    'refresh_movie_release_dates': {
        'label': 'Refresh Movie Release Dates',
        'description': 'Re-check TMDB for confirmed digital release dates (type 4) on waiting movies; falls back to theatrical + 45 days.',
        'icon': 'fa-calendar-check',
        'color': 'cyan',
    },
    'cleanup_non_video_files': {
        'label': 'Clean Up Non-Video Files',
        'description': 'Delete junk files (.nfo, .txt, .jpg, …) left in completed download folders, keeping only video files.',
        'icon': 'fa-broom',
        'color': 'yellow',
    },
    'remove_empty_folders': {
        'label': 'Remove Empty Folders',
        'description': 'Delete any empty directories found inside download path(s). Runs bottom-up so nested empties collapse in one pass.',
        'icon': 'fa-folder-minus',
        'color': 'orange',
    },
    'auto_search_queue': {
        'label': 'Auto-Search Queue',
        'description': 'Fire search_and_download for any queued items stuck in SEARCHING status without an active task — so searches run without the queue page open.',
        'icon': 'fa-magnifying-glass-arrow-right',
        'color': 'cyan',
    },
    'poll_download_progress': {
        'label': 'Poll Download Progress',
        'description': 'Check qBittorrent for completed downloads and trigger file moves — runs in the background so the queue page does not need to be open.',
        'icon': 'fa-rotate',
        'color': 'brand',
    },
    'check_storage': {
        'label': 'Check Storage Usage',
        'description': 'Alert via ntfy when any configured drive reaches 75 % (caution) or 90 % (critical). Suppresses duplicate alerts for 4 hours.',
        'icon': 'fa-hard-drive',
        'color': 'yellow',
    },
}

PERIOD_CHOICES = [
    ('minutes', 'Minutes'),
    ('hours', 'Hours'),
    ('days', 'Days'),
]


def tasks_dashboard(request):
    beat_tasks = {t.task: t for t in PeriodicTask.objects.all()}

    tasks = []
    for task_name, meta in TASK_REGISTRY.items():
        beat = beat_tasks.get(task_name)
        last_result = (
            TaskResult.objects.filter(task_name=task_name)
            .order_by('-date_done')
            .first()
        )
        tasks.append({
            'name': task_name,
            'label': meta['label'],
            'description': meta['description'],
            'icon': meta['icon'],
            'color': meta['color'],
            'beat': beat,
            'last_result': last_result,
            'schedule': _describe_schedule(beat),
            'last_run': beat.last_run_at if beat else None,
            'run_count': beat.total_run_count if beat else 0,
        })

    # All periodic tasks for the schedule manager (includes Django internals etc.)
    all_schedules = PeriodicTask.objects.select_related('interval', 'crontab').order_by('name')

    recent = _annotate_recent(TaskResult.objects.order_by('-date_done')[:100])

    context = {
        'tasks': tasks,
        'recent': recent,
        'all_schedules': all_schedules,
        'period_choices': PERIOD_CHOICES,
        'beat_configured': bool(beat_tasks),
    }
    return render(request, 'tasks/dashboard.html', context)


@require_POST
def trigger_task(request):
    task_name = request.POST.get('task')
    if task_name not in TASK_REGISTRY:
        return JsonResponse({'error': 'Unknown task'}, status=400)
    try:
        result = _dispatch(task_name)
        return JsonResponse({'task_id': result.id, 'task': task_name})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def task_poll(request, task_id):
    try:
        result = TaskResult.objects.get(task_id=task_id)
        return JsonResponse({
            'status': result.status,
            'result': result.result,
            'date_done': result.date_done.isoformat() if result.date_done else None,
            'traceback': result.traceback or '',
        })
    except TaskResult.DoesNotExist:
        return JsonResponse({'status': 'PENDING'})


def recent_results_partial(request):
    recent = _annotate_recent(TaskResult.objects.order_by('-date_done')[:100])
    return render(request, 'tasks/_recent_results.html', {'recent': recent})


def _annotate_recent(qs):
    import json as _json
    rows = []
    for r in qs:
        try:
            val = _json.loads(r.result) if r.result else None
        except Exception:
            val = r.result

        detail = None
        if isinstance(val, str) and val:
            detail = val
        elif isinstance(val, dict):
            detail = val.get('message') or val.get('status') or str(val)

        # Friendly label map (covers both task_name populated and not)
        name = r.task_name or ''
        label = {
            'sync_all_shows':              'Sync All TV Shows',
            'update_episode_statuses':     'Update Episode Statuses',
            'queue_waiting_episodes':      'Queue Waiting Episodes',
            'sync_tvmaze_show':            'Sync TVMaze Airdates',
            'check_movie_releases':        'Check Movie Releases',
            'sync_download_progress':      'Sync Download Progress',
            'poll_download_progress':      'Poll Download Progress',
            'search_and_download':         'Search & Download',
            'refresh_movie_release_dates': 'Refresh Movie Release Dates',
            'cleanup_non_video_files':     'Clean Up Non-Video Files',
            'remove_empty_folders':        'Remove Empty Folders',
            'execute_file_move':           'Execute File Move',
            'auto_search_queue':           'Auto-Search Queue',
            'check_storage':               'Check Storage Usage',
        }.get(name, name or 'Unknown Task')

        r.label = label
        r.detail = detail
        rows.append(r)
    return rows


@require_POST
def schedule_toggle(request, pk):
    """Enable or disable a periodic task."""
    task = get_object_or_404(PeriodicTask, pk=pk)
    task.enabled = not task.enabled
    task.save(update_fields=['enabled'])
    return JsonResponse({'enabled': task.enabled})


@require_POST
def schedule_update(request, pk):
    """Update the interval of a periodic task."""
    task = get_object_or_404(PeriodicTask, pk=pk)
    try:
        data = json.loads(request.body)
        every = int(data.get('every', 1))
        period = data.get('period', 'hours')
        if period not in dict(PERIOD_CHOICES):
            return JsonResponse({'error': 'Invalid period'}, status=400)
        schedule, _ = IntervalSchedule.objects.get_or_create(every=every, period=period)
        task.interval = schedule
        task.crontab = None
        task.save(update_fields=['interval', 'crontab'])
        return JsonResponse({'ok': True, 'schedule': f'Every {every} {period}'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_POST
def schedule_delete(request, pk):
    """Delete a periodic task entirely."""
    task = get_object_or_404(PeriodicTask, pk=pk)
    name = task.name
    task.delete()
    return JsonResponse({'ok': True, 'name': name})


# ── Helpers ──────────────────────────────────────────────────────────────────

def _dispatch(task_name):
    from apps.media_tracker.tasks import (
        sync_all_shows,
        update_episode_statuses, queue_waiting_episodes,
        check_movie_releases, sync_download_progress,
        cleanup_non_video_files, refresh_movie_release_dates,
        remove_empty_folders, auto_search_queue, check_storage,
    )
    from apps.downloads.tasks import poll_download_progress
    mapping = {
        'sync_all_shows':              sync_all_shows,
        'update_episode_statuses':     update_episode_statuses,
        'queue_waiting_episodes':      queue_waiting_episodes,
        'check_movie_releases':        check_movie_releases,
        'sync_download_progress':      sync_download_progress,
        'cleanup_non_video_files':     cleanup_non_video_files,
        'refresh_movie_release_dates': refresh_movie_release_dates,
        'remove_empty_folders':        remove_empty_folders,
        'auto_search_queue':           auto_search_queue,
        'poll_download_progress':      poll_download_progress,
        'check_storage':               check_storage,
    }
    return mapping[task_name].delay()


def _describe_schedule(beat):
    if not beat:
        return 'Not scheduled'
    if beat.interval:
        iv = beat.interval
        return f'Every {iv.every} {iv.period}'
    if beat.crontab:
        c = beat.crontab
        return f'Cron: {c.minute} {c.hour} {c.day_of_week} {c.day_of_month} {c.month_of_year}'
    return 'Scheduled'
