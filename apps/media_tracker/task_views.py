import json
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_celery_beat.models import PeriodicTask, IntervalSchedule
from django_celery_results.models import TaskResult

# Only Daredevil's own housekeeping lives here now — searching, downloading,
# importing and release-date watching are Sonarr/Radarr's job (see the
# Prowlarr / Sonarr / Radarr tabs on the Settings page).
TASK_REGISTRY = {
    'warm_caches': {
        'label': 'Keep Pages Fast',
        'description': 'Refetch what the Dashboard, Streaming and Plex pages show from TMDB and Plex, so they open straight from the cache.',
        'icon': 'fa-bolt',
        'color': 'brand',
    },
    'check_storage': {
        'label': 'Check Storage Usage',
        'description': 'Alert via ntfy when any configured drive reaches 75 % (caution) or 90 % (critical). Suppresses duplicate alerts for 4 hours.',
        'icon': 'fa-hard-drive',
        'color': 'yellow',
    },
    'generate_recommendations': {
        'label': 'For You: Recommendations',
        'description': 'Pull TMDB recommendations + similar titles for everything in Sonarr/Radarr and add new ones to the For You swipe deck.',
        'icon': 'fa-layer-group',
        'color': 'cyan',
    },
    'generate_anticipated': {
        'label': 'For You: New & Anticipated',
        'description': "Pull TMDB's Trending, New Releases, Coming Soon and Popular lists for movies and shows and add titles you haven't seen to the For You swipe deck.",
        'icon': 'fa-fire',
        'color': 'cyan',
    },
    'import_library_to_arr': {
        'label': 'Import Old Library into Sonarr/Radarr',
        'description': "One-off: add the shows/movies Daredevil tracked before Sonarr/Radarr took over. Existing files are picked up; nothing is re-downloaded. Safe to re-run.",
        'icon': 'fa-file-import',
        'color': 'brand',
    },
}

PERIOD_CHOICES = [
    ('minutes', 'Minutes'),
    ('hours', 'Hours'),
    ('days', 'Days'),
]


def _dashboard_context():
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
    return context


def tasks_dashboard(request):
    """Moved to App Settings → Background Tasks."""
    from django.shortcuts import redirect
    return redirect('/settings/#tasks')


def tasks_panel(request):
    """The Background Tasks panel, loaded into its App Settings tab."""
    return render(request, 'tasks/_panel.html', _dashboard_context())


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
            **{k: v['label'] for k, v in TASK_REGISTRY.items()},
            # Retired jobs — kept so their old results still read nicely
            'sync_all_shows':              'Sync All TV Shows (retired)',
            'update_episode_statuses':     'Update Episode Statuses (retired)',
            'queue_waiting_episodes':      'Queue Waiting Episodes (retired)',
            'check_movie_releases':        'Check Movie Releases (retired)',
            'poll_download_progress':      'Poll Download Progress (retired)',
            'search_and_download':         'Search & Download (retired)',
            'auto_search_queue':           'Auto-Search Queue (retired)',
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
    from apps.media_tracker.tasks import check_storage
    from apps.discover.tasks import generate_recommendations, generate_anticipated
    from apps.arr.tasks import import_library_to_arr
    mapping = {
        'check_storage':            check_storage,
        'generate_recommendations': generate_recommendations,
        'generate_anticipated':     generate_anticipated,
        'import_library_to_arr':    import_library_to_arr,
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
