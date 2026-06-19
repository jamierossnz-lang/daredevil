def notification_count(request):
    try:
        from .models import Notification
        return {'notif_unread': Notification.objects.filter(read=False).count()}
    except Exception:
        return {'notif_unread': 0}
