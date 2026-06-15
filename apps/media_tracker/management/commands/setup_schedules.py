from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule


class Command(BaseCommand):
    help = 'Create default Celery Beat periodic tasks'

    def handle(self, *args, **options):
        hourly,   _ = IntervalSchedule.objects.get_or_create(every=1,  period=IntervalSchedule.HOURS)
        daily,    _ = IntervalSchedule.objects.get_or_create(every=24, period=IntervalSchedule.HOURS)
        every30m, _ = IntervalSchedule.objects.get_or_create(every=30, period=IntervalSchedule.MINUTES)

        tasks = [
            ('Sync all TV shows from TMDB',           'sync_all_shows',           daily),
            ('Queue new episodes for monitored shows', 'queue_new_episodes',       every30m),
            ('Check movie digital release dates',      'check_movie_releases',     hourly),
            ('Clean up non-video files',               'cleanup_non_video_files',  daily),
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
