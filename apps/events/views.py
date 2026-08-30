from django.db.models import Count
from django.shortcuts import render

from .models import DomainEvent, EventType

# Icon + badge colour per event type — mirrors apps.notifications.models.CATEGORY_META
# so the log reads consistently with the bell/ntfy notifications the same events produce.
_EVENT_META = {
    EventType.EPISODE_QUEUED:     {'icon': 'fa-tv',              'badge': 'bg-brand-900/40 text-brand-400'},
    EventType.MOVIE_AVAILABLE:    {'icon': 'fa-film',            'badge': 'bg-cyan-900/30 text-cyan-400'},
    EventType.DOWNLOAD_COMPLETED: {'icon': 'fa-circle-check',    'badge': 'bg-green-900/30 text-green-400'},
    EventType.FILE_MOVED:         {'icon': 'fa-folder-open',     'badge': 'bg-blue-900/30 text-blue-400'},
    EventType.SEARCH_FAILED:      {'icon': 'fa-circle-xmark',    'badge': 'bg-red-900/30 text-red-400'},
    EventType.FILE_MOVE_FAILED:   {'icon': 'fa-circle-xmark',    'badge': 'bg-red-900/30 text-red-400'},
    EventType.STORAGE_WARNING:    {'icon': 'fa-hard-drive',      'badge': 'bg-yellow-900/30 text-yellow-400'},
}
_DEFAULT_META = {'icon': 'fa-bell', 'badge': 'bg-gray-700/50 text-gray-400'}


def _describe(event):
    """Human-readable one-line summary built from the event's stored payload."""
    p = event.payload or {}
    t = event.event_type

    if t == EventType.DOWNLOAD_COMPLETED:
        return f'{p.get("title", "Item")} finished downloading'
    if t == EventType.FILE_MOVED:
        return f'{p.get("title", "Item")} moved to library'
    if t == EventType.FILE_MOVE_FAILED:
        return f'{p.get("title") or "A move"} failed — {p.get("error", "unknown error")}'
    if t == EventType.EPISODE_QUEUED:
        if p.get('count') == 1:
            season = p.get('season') or 0
            ep = p.get('episode') or 0
            return f'{p.get("show", "Show")} S{season:02d}E{ep:02d} queued'
        return f'{p.get("count", 0)} episodes queued'
    if t == EventType.MOVIE_AVAILABLE:
        year = p.get('year')
        return f'{p.get("title", "Movie")}{f" ({year})" if year else ""} now available — downloading'
    if t == EventType.SEARCH_FAILED:
        return f'No torrent found for {p.get("title", "item")}'
    if t == EventType.STORAGE_WARNING:
        return f'{p.get("path", "A drive")} at {p.get("pct", "?")}% ({p.get("level", "warning")})'
    return event.get_event_type_display()


def activity_log(request):
    event_filter = request.GET.get('type', '')

    qs = DomainEvent.objects.all()
    if event_filter:
        qs = qs.filter(event_type=event_filter)

    counts = {row['event_type']: row['n'] for row in DomainEvent.objects.values('event_type').annotate(n=Count('id'))}

    events = []
    for e in qs:
        meta = _EVENT_META.get(e.event_type, _DEFAULT_META)
        events.append({
            'obj': e,
            'summary': _describe(e),
            'icon': meta['icon'],
            'badge': meta['badge'],
        })

    type_chips = [
        {
            'value': value,
            'label': label,
            'count': counts.get(value, 0),
            'icon': _EVENT_META.get(value, _DEFAULT_META)['icon'],
        }
        for value, label in EventType.choices
    ]

    return render(request, 'events/activity_log.html', {
        'events': events,
        'type_chips': type_chips,
        'event_filter': event_filter,
        'total_count': sum(counts.values()),
    })
