from django.core.management.base import BaseCommand

from apps.arr.legacy import describe, import_legacy_library


class Command(BaseCommand):
    help = "Add Daredevil's old local shows/movies to Sonarr/Radarr (existing files are picked up, nothing is re-downloaded)"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List what would be added without changing anything')

    def handle(self, *args, dry_run=False, **options):
        summary = import_legacy_library(dry_run=dry_run)
        for title in summary['shows_added']:
            self.stdout.write(f'  {"Would add" if dry_run else "Added"} show:  {title}')
        for title in summary['movies_added']:
            self.stdout.write(f'  {"Would add" if dry_run else "Added"} movie: {title}')
        for err in summary['shows_failed'] + summary['movies_failed']:
            self.stdout.write(self.style.WARNING(f'  Failed: {err}'))
        self.stdout.write(self.style.SUCCESS(describe(summary)))
