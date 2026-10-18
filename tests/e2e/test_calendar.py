"""Calendar — the month and list views, filters, an event, and the .ics feed."""
import re
from datetime import date

import pytest
from conftest import api_get, expect, intercept, open_page, url


@pytest.fixture
def month_events(page):
    first = date.today().replace(day=1)
    last = (first.replace(month=first.month % 12 + 1, year=first.year + first.month // 12)).toordinal() - 1
    data = api_get(page, f'/calendar/events/?start={first}&end={date.fromordinal(last)}')
    assert data['ok'] and not data['errors'], data.get('errors')
    return data['events']


def calendar(page):
    open_page(page, '/calendar/')
    cal = page.locator('[x-data^="calendarPage"]')
    expect(cal.locator('h2[x-text="monthLabel()"]')).not_to_be_empty()
    page.wait_for_timeout(1200)
    return cal


def test_month_view_moves_between_months(page):
    cal = calendar(page)
    label = cal.locator('h2[x-text="monthLabel()"]')
    this_month = label.inner_text()
    cal.get_by_label('Next month').click()
    expect(label).not_to_have_text(this_month)
    cal.get_by_label('Previous month').click()
    cal.get_by_label('Previous month').click()
    expect(label).not_to_have_text(this_month)
    cal.get_by_role('button', name='Today').click()
    expect(label).to_have_text(this_month)


def test_events_appear_in_month_and_list_views(page, month_events):
    if not month_events:
        pytest.skip('nothing on the calendar this month')
    cal = calendar(page)
    title = month_events[0]['title']
    cal.get_by_role('button', name=re.compile(r'List$')).click()
    expect(cal.get_by_role('button', name=re.compile(re.escape(title))).first).to_be_visible()
    cal.get_by_role('button', name=re.compile(r'Month$')).click()
    expect(cal.locator('[\\@click="pickDay(cell)"]').first).to_be_visible()


def test_filters_change_the_request_and_are_remembered(page, month_events):
    cal = calendar(page)
    with page.expect_request(re.compile(r'/calendar/events/\?.*tv=0')):
        cal.get_by_role('button', name='TV episodes').click()
    if any(e['kind'] == 'episode' for e in month_events):
        cal.get_by_role('button', name=re.compile(r'List$')).click()
        page.wait_for_timeout(800)
        episode = next(e for e in month_events if e['kind'] == 'episode')
        expect(cal.get_by_role('button', name=re.compile(re.escape(episode['subtitle'])))).to_have_count(0)
    page.reload()
    page.wait_for_timeout(1200)
    tv = page.get_by_role('button', name='TV episodes')
    expect(tv).not_to_have_class(re.compile('bg-brand-600/25'))
    tv.click()   # back on for the next test
    expect(tv).to_have_class(re.compile('bg-brand-600/25'))


def test_event_sheet_offers_google_apple_and_the_title(page, month_events):
    if not month_events:
        pytest.skip('nothing on the calendar this month')
    cal = calendar(page)
    cal.get_by_role('button', name=re.compile(r'List$')).click()
    event = month_events[0]
    cal.get_by_role('button', name=re.compile(re.escape(event['title']))).first.click()
    google = page.locator('a[href^="https://calendar.google.com/calendar/render?action=TEMPLATE"]')
    expect(google).to_be_visible()
    assert 'text=' in google.get_attribute('href')
    ics = page.locator('a[href*="/calendar/event.ics?"]')
    resp = page.request.get(url(ics.get_attribute('href')))
    assert resp.ok and resp.headers['content-type'].startswith('text/calendar')
    assert 'BEGIN:VEVENT' in resp.text() and event['uid'] in resp.text()
    expect(page.get_by_role('link', name=re.compile('Open (show|movie)'))).to_have_attribute('href', event['link'])


def test_subscribe_sheet(page):
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    calendar(page)
    page.get_by_role('button', name='Subscribe').click()
    webcal = page.locator('a[href^="webcal://"]')
    expect(webcal).to_be_visible()
    assert re.search(r'/calendar/feed/[\w-]+\.ics', webcal.get_attribute('href'))
    expect(page.locator('a[href*="calendar.google.com/calendar/render?cid="]')).to_be_visible()
    page.get_by_role('button', name='Copy').click()
    expect(page.get_by_role('button', name='Copied')).to_be_visible()
    assert '/calendar/feed/' in page.evaluate('navigator.clipboard.readText()')


def test_new_feed_address(page):
    """Intercepted — a real reset would cut off your subscribed calendars."""
    calendar(page)
    caught = intercept(page, '**/calendar/reset-feed/', {'ok': True, 'feed_path': '/calendar/feed/e2e-new-token.ics'})
    page.get_by_role('button', name='Subscribe').click()
    page.get_by_role('button', name='Make a new address').click()
    expect(page.locator('a[href^="webcal://"]')).to_have_attribute('href', re.compile('e2e-new-token'))
    assert caught and page.dialogs_seen


def test_feed_is_valid_icalendar(page, anon):
    calendar(page)
    page.get_by_role('button', name='Subscribe').click()
    feed = page.locator('a[href^="webcal://"]').get_attribute('href').replace('webcal://', 'http://')
    resp = anon.request.get(feed)   # calendar apps can't sign in — the token is enough
    assert resp.ok and resp.headers['content-type'].startswith('text/calendar')
    body = resp.body()
    assert body.startswith(b'BEGIN:VCALENDAR\r\n') and body.rstrip().endswith(b'END:VCALENDAR')
    assert body.count(b'BEGIN:VEVENT') == body.count(b'END:VEVENT')
    assert all(len(line) <= 75 for line in body.split(b'\r\n')), 'lines must be folded at 75 octets'
    movies_only = anon.request.get(feed + '?tv=0').body()
    assert b'sonarr-episode-' not in movies_only


def test_bad_ranges_are_refused(page):
    resp = page.request.get(url('/calendar/events/?start=2026-01-01&end=2026-12-31'))
    assert resp.status == 400
    resp = page.request.get(url('/calendar/events/?start=nope&end=2026-01-02'))
    assert resp.status == 400
