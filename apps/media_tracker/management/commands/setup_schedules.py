from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule


class Command(BaseCommand):
    help = 'Create default Celery Beat periodic tasks'

    def handle(self, *args, **options):
        hourly, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
        daily, _ = IntervalSchedule.objects.get_or_create(every=24, period=IntervalSchedule.HOURS)
        every5min, _ = IntervalSchedule.objects.get_or_create(every=5, period=IntervalSchedule.MINUTES)

        tasks = [
            ('Sync all TV shows from TMDB', 'sync_all_shows', daily),
            ('Queue new episodes for monitored shows', 'queue_new_episodes', hourly),
            ('Check movie digital release dates', 'check_movie_releases', hourly),
            ('Sync download progress', 'sync_download_progress', every5min),
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
