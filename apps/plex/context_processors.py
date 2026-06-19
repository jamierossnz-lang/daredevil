from django.core.cache import cache


def drive_usage(request):
    drives = cache.get('plex_drive_usage')
    if drives is None:
        try:
            from .utils import get_disk_usage
            drives = get_disk_usage()
        except Exception:
            drives = []
        cache.set('plex_drive_usage', drives, 60)
    return {'drives': drives}
