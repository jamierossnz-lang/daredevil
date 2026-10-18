from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule

# Download automation that Sonarr/Radarr now do themselves. Their schedule
# rows are deleted on every deploy so an upgraded install stops running them.
RETIRED_TASKS = (
    'sync_all_shows', 'update_episode_statuses', 'queue_waiting_episodes',
    'check_movie_releases', 'refresh_movie_release_dates', 'cleanup_non_video_files',
    'remove_empty_folders', 'poll_download_progress', 'auto_search_queue',
    'sync_download_progress', 'search_and_download', 'sync_tvmaze_show', 'execute_file_move',
)
# Still runnable by hand from Background Tasks, but no longer scheduled on
# their own: run together they raced, so rebuild_for_you runs them in turn.
UNSCHEDULED_TASKS = ('generate_recommendations', 'generate_anticipated')


class Command(BaseCommand):
    help = 'Create default Celery Beat periodic tasks (and remove retired ones)'

    def handle(self, *args, **options):
        every10m, _ = IntervalSchedule.objects.get_or_create(every=10, period=IntervalSchedule.MINUTES)
        every6h, _ = IntervalSchedule.objects.get_or_create(every=6,  period=IntervalSchedule.HOURS)
        daily,   _ = IntervalSchedule.objects.get_or_create(every=24, period=IntervalSchedule.HOURS)

        tasks = [
            ('Keep pages fast',                         'warm_caches',                 every10m),
            ('Check storage usage',                     'check_storage',               every6h),
            # For You swipe deck — rebuilt daily (interval rather than a fixed clock time)
            ('Rebuild For You deck',                    'rebuild_for_you',             daily),
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

        removed = PeriodicTask.objects.filter(task__in=RETIRED_TASKS + UNSCHEDULED_TASKS)
        for obj in removed:
            self.stdout.write(f'  Removed: {obj.name}')
        removed.delete()

        self.stdout.write(self.style.SUCCESS('Schedules ready.'))
