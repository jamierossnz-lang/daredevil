"""JSON request/response helpers shared by the views that front Sonarr, Radarr and Prowlarr."""
import json

from django.http import JsonResponse


def json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


def json_error(e, status=400, **extra):
    return JsonResponse({'ok': False, 'error': str(e), **extra}, status=status)
