from celery import shared_task


@shared_task(name='import_library_to_arr')
def import_library_to_arr():
    """Push Daredevil's old local library into Sonarr/Radarr (no re-downloads)."""
    from .legacy import describe, import_legacy_library
    return describe(import_legacy_library())
