from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule


class Command(BaseCommand):
    help = 'Create default Celery Beat periodic tasks'

    def handle(self, *args, **options):
        hourly,   _ = IntervalSchedule.objects.get_or_create(every=1,  period=IntervalSchedule.HOURS)
        every6h,  _ = IntervalSchedule.objects.get_or_create(every=6,  period=IntervalSchedule.HOURS)
        daily,    _ = IntervalSchedule.objects.get_or_create(every=24, period=IntervalSchedule.HOURS)
        every30m, _ = IntervalSchedule.objects.get_or_create(every=30, period=IntervalSchedule.MINUTES)
        every5m,  _ = IntervalSchedule.objects.get_or_create(every=5,  period=IntervalSchedule.MINUTES)
        every30s, _ = IntervalSchedule.objects.get_or_create(every=30, period=IntervalSchedule.SECONDS)
        every15s, _ = IntervalSchedule.objects.get_or_create(every=15, period=IntervalSchedule.SECONDS)

        tasks = [
            ('Sync all TV shows (TMDB + TVMaze)',       'sync_all_shows',              daily),
            ('Update episode air statuses',             'update_episode_statuses',     every5m),
            ('Queue waiting episodes for download',     'queue_waiting_episodes',      every30m),
            ('Check movie watch providers',             'check_movie_releases',        hourly),
            ('Refresh movie digital release dates',     'refresh_movie_release_dates', daily),
            ('Clean up non-video files',                'cleanup_non_video_files',     daily),
            ('Remove empty folders',                    'remove_empty_folders',        daily),
            # These two are the "no browser tab open" backstops for live
            # progress and stuck-item search — kept tight so background
            # automation feels close to real-time. Safe at this frequency
            # only because DownloadItems get search_started_at stamped at
            # creation (see auto_search_queue's docstring) — that's what
            # actually prevents racing a browser tab, not the interval here.
            ('Poll download progress',                  'poll_download_progress',      every15s),
            ('Auto-search queue',                       'auto_search_queue',           every30s),
            ('Check storage usage',                     'check_storage',               every6h),
        ]

        for name, task_name, schedule in tasks:
            obj, created = PeriodicTask.objects.get_or_create(
                name=name,
                defaults={'task': task_name, 'interval': schedule},
            )
            if not created:
                obj.interval = schedule
                obj.save(update_fields=['interval'])
            self.stdout.write(f'  {"Created" if created else "Updated"}: {name}')

        self.stdout.write(self.style.SUCCESS('Schedules ready.'))
