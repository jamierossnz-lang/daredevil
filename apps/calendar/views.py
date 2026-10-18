"""
Calendar — what's airing and being released, from Sonarr and Radarr — and
an .ics feed of it for Google / Apple Calendar.
"""
from datetime import date, datetime, time, timedelta

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_POST

from apps.arr.services import is_configured

from . import ics
from .events import calendar_events, local_day
from .models import CalendarFeed

MAX_RANGE_DAYS = 100
# What a subscribed calendar gets: a month back, half a year ahead (a year for movies)
FEED_PAST_DAYS, FEED_TV_DAYS, FEED_MOVIE_DAYS = 30, 180, 365


def _midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def calendar_page(request):
    return render(request, 'calendar/calendar.html', {
        'feed_path': reverse('calendar_feed', args=[CalendarFeed.get().token]),
        'sonarr_ready': is_configured('sonarr'),
        'radarr_ready': is_configured('radarr'),
    })


def events_json(request):
    """?start=YYYY-MM-DD&end=YYYY-MM-DD (inclusive) &tv=0 &movies=0 &monitored=1"""
    try:
        first = date.fromisoformat(request.GET.get('start', ''))
        last = date.fromisoformat(request.GET.get('end', ''))
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'start and end must be YYYY-MM-DD'}, status=400)
    if last < first or (last - first).days > MAX_RANGE_DAYS:
        return JsonResponse({'ok': False, 'error': f'Pick a range of up to {MAX_RANGE_DAYS} days'}, status=400)
    events, errors = calendar_events(
        _midnight(first), _midnight(last + timedelta(days=1)),
        tv=request.GET.get('tv') != '0', movies=request.GET.get('movies') != '0',
        monitored_only=request.GET.get('monitored') == '1',
    )
    out = []
    for e in events:
        local = None if e['all_day'] else timezone.localtime(e['start'])
        out.append({
            **{k: e[k] for k in ('uid', 'app', 'kind', 'title', 'subtitle', 'all_day', 'status', 'poster', 'link', 'network', 'overview')},
            'day': local_day(e).isoformat(),
            'time': local.strftime('%-I:%M %p') if local else '',
            # For "Add to Google Calendar" — UTC for timed events, dates for all-day ones
            'start': e['start'].isoformat(),
            'end': e['end'].isoformat(),
        })
    return JsonResponse({'ok': True, 'events': out, 'errors': errors})


def feed(request, token):
    """The subscribable .ics feed — the token in the URL is the only credential."""
    if not constant_time_compare(token, CalendarFeed.get().token):
        raise Http404
    now = timezone.now()
    tv = request.GET.get('tv') != '0'
    movies = request.GET.get('movies') != '0'
    events = []
    if tv:
        events += calendar_events(now - timedelta(days=FEED_PAST_DAYS), now + timedelta(days=FEED_TV_DAYS),
                                  tv=True, movies=False)[0]
    if movies:
        events += calendar_events(now - timedelta(days=FEED_PAST_DAYS), now + timedelta(days=FEED_MOVIE_DAYS),
                                  tv=False, movies=True)[0]
    body = ics.render(events, request.build_absolute_uri('/'))
    resp = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = 'inline; filename="daredevil.ics"'
    resp['Cache-Control'] = 'private, max-age=900'
    return resp


def event_ics(request):
    """One event as an .ics file (?uid=…&day=YYYY-MM-DD) — on an iPhone, Safari
    offers "Add to Calendar" for it straight away."""
    uid = request.GET.get('uid', '')
    try:
        day = date.fromisoformat(request.GET.get('day', ''))
    except ValueError:
        raise Http404
    events, _ = calendar_events(_midnight(day - timedelta(days=1)), _midnight(day + timedelta(days=2)))
    match = [e for e in events if e['uid'] == uid]
    if not match:
        raise Http404
    resp = HttpResponse(ics.render(match, request.build_absolute_uri('/')), content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = 'inline; filename="event.ics"'
    return resp


@require_POST
def feed_reset(request):
    """New secret URL — every calendar subscribed to the old one stops updating."""
    feed = CalendarFeed.get()
    feed.reset()
    return JsonResponse({'ok': True, 'feed_path': reverse('calendar_feed', args=[feed.token])})
