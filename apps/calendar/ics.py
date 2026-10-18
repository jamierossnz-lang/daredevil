"""iCalendar (RFC 5545) output for the subscribable feed."""
from datetime import datetime, timezone as dt_timezone

_STATUS_LABEL = {'downloaded': 'In your library', 'missing': 'Missing', 'upcoming': 'Coming up',
                 'unmonitored': 'Not monitored'}


def _text(value):
    return (str(value or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\r\n', '\\n').replace('\n', '\\n'))


def _fold(line):
    """Lines longer than 75 octets continue on the next line after a space —
    never splitting a UTF-8 character."""
    data = line.encode('utf-8')
    parts, limit = [], 75
    while len(data) > limit:
        cut = limit
        while cut and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        if data[cut - 1:cut] == b'\\':  # legal to split, but some parsers trip on it
            cut -= 1
        parts.append(data[:cut].decode('utf-8'))
        data = data[cut:]
        limit = 74  # continuation lines start with a space
    parts.append(data.decode('utf-8'))
    return '\r\n '.join(parts)


def _utc(dt):
    return dt.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def render(events, base_url, name='Daredevil'):
    """events from calendar.events.calendar_events; base_url like https://host/"""
    stamp = _utc(datetime.now(dt_timezone.utc))
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Daredevil//Calendar//EN', 'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH', f'X-WR-CALNAME:{_text(name)}',
        'X-WR-CALDESC:TV episodes and movie releases from Sonarr and Radarr',
        # Hints for how often to refetch (Apple / Outlook honour these; Google picks its own)
        'REFRESH-INTERVAL;VALUE=DURATION:PT6H', 'X-PUBLISHED-TTL:PT6H',
    ]
    for e in events:
        if e['all_day']:
            when = [f'DTSTART;VALUE=DATE:{e["start"]:%Y%m%d}', f'DTEND;VALUE=DATE:{e["end"]:%Y%m%d}', 'TRANSP:TRANSPARENT']
        else:
            when = [f'DTSTART:{_utc(e["start"])}', f'DTEND:{_utc(e["end"])}']
        details = [e['subtitle'], _STATUS_LABEL.get(e['status'], ''), e.get('network', ''), e.get('overview', '')]
        lines += [
            'BEGIN:VEVENT', f'UID:{e["uid"]}@daredevil', f'DTSTAMP:{stamp}', *when,
            f'SUMMARY:{_text(e["title"] + (" — " + e["subtitle"] if e["kind"] == "episode" else " · " + e["subtitle"]))}',
            f'DESCRIPTION:{_text(chr(10).join(d for d in details if d))}',
            f'URL:{base_url.rstrip("/")}{e["link"]}',
            f'CATEGORIES:{"TV" if e["kind"] == "episode" else "Movies"}',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(line) for line in lines) + '\r\n'
