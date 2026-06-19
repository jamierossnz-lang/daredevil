from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Fetch TVMaze air times for every tracked show, convert to UTC, store on '
        'Episode.air_datetime, then apply awaiting_release/waiting_for_download '
        'statuses to episodes of monitored shows.'
    )

    def handle(self, *args, **options):
        from apps.media_tracker.models import TVShow, Episode
        from apps.media_tracker.tvmaze import tvmaze
        from apps.media_tracker.tasks import _apply_episode_statuses
        from django.utils import timezone

        shows = list(TVShow.objects.prefetch_related('seasons__episodes').all())
        self.stdout.write(f'Found {len(shows)} shows to process.\n')

        total_airdates = 0
        errors = 0

        for show in shows:
            self.stdout.write(f'  [{show.pk}] {show.name} ... ', ending='')
            try:
                updated = tvmaze.sync_airdates_for_show(show)
                show.refresh_from_db()
                _apply_episode_statuses(show)
                total_airdates += updated
                monitor_note = ' (monitoring — statuses applied)' if show.monitor_new_episodes else ''
                self.stdout.write(self.style.SUCCESS(f'{updated} air datetimes updated{monitor_note}'))
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(f'ERROR — {e}'))

        # Global pass: same rules as update_episode_statuses
        # - air_datetime: wait 1 hour after broadcast
        # - date-only: wait until the next day (no exact time known)
        from datetime import timedelta
        now = timezone.now()
        today = timezone.localdate()
        one_hour_ago = now - timedelta(hours=1)

        moved_dt = Episode.objects.filter(
            download_status=Episode.DownloadStatus.AWAITING_RELEASE,
            air_datetime__isnull=False,
            air_datetime__lte=one_hour_ago,
        ).update(download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD)

        moved_date = Episode.objects.filter(
            download_status=Episode.DownloadStatus.AWAITING_RELEASE,
            air_datetime__isnull=True,
            air_date__lt=today,
        ).update(download_status=Episode.DownloadStatus.WAITING_FOR_DOWNLOAD)

        self.stdout.write(
            f'\nDone. {total_airdates} air datetimes set, '
            f'{moved_dt + moved_date} episodes moved to waiting_for_download, '
            f'{errors} show(s) with errors.'
        )
