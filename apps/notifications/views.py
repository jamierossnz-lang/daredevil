import json

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import CATEGORY_META, Notification, NotificationPrefs


def _time_ago(dt, now):
    diff = int((now - dt).total_seconds())
    if diff < 60:
        return 'just now'
    if diff < 3600:
        return f'{diff // 60}m ago'
    if diff < 86400:
        return f'{diff // 3600}h ago'
    return f'{diff // 86400}d ago'


def panel(request):
    """HTML partial for the notification panel content."""
    now = timezone.now()
    notifs = list(Notification.objects.all()[:40])
    items = [{'notif': n, 'time_ago': _time_ago(n.created_at, now)} for n in notifs]
    Notification.objects.filter(read=False).update(read=True)
    html = render_to_string('notifications/_panel.html', {'items': items}, request=request)
    return HttpResponse(html)


def count(request):
    return JsonResponse({'count': Notification.objects.filter(read=False).count()})


@require_POST
def dismiss(request, pk):
    Notification.objects.filter(pk=pk).delete()
    return JsonResponse({'ok': True})


@require_POST
def clear_all(request):
    Notification.objects.all().delete()
    return JsonResponse({'ok': True})


def prefs(request):
    p = NotificationPrefs.get()
    return JsonResponse({
        'prefs': p.as_dict(),
        'meta': [
            {'key': k, 'label': label, 'icon': icon, 'description': desc}
            for k, label, icon, desc in CATEGORY_META
        ],
    })


@require_POST
def save_prefs(request):
    data = json.loads(request.body)
    p = NotificationPrefs.get()
    for key, *_ in CATEGORY_META:
        if key in data:
            setattr(p, key, bool(data[key]))
    p.save()
    return JsonResponse({'ok': True})
