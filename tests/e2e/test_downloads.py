"""Downloads — the live queue, its tabs and actions, and the Imports page."""
import re

import pytest
from conftest import api_get, expect, intercept, open_page, url

from data import QUEUE, sse


def queue_with_items(page):
    """The Queue page fed a known queue through its live stream."""
    page.route('**/downloads/status/stream/', lambda r: r.fulfill(status=200, content_type='text/event-stream', body=sse(QUEUE)))
    open_page(page, '/downloads/')
    expect(page.get_by_text('E2E Show')).to_be_visible()


def test_real_queue_and_stream(page):
    data = api_get(page, '/downloads/status/')
    assert set(data['status']) == {'sonarr', 'radarr', 'qbittorrent'}
    open_page(page, '/downloads/')
    with page.expect_response('**/downloads/status/stream/', timeout=20_000) as resp:
        page.reload()
    assert resp.value.headers['content-type'].startswith('text/event-stream')


def test_stream_shows_each_download(page):
    queue_with_items(page)
    for item in QUEUE['items']:
        expect(page.get_by_text(item['title'], exact=True)).to_be_visible()
    expect(page.get_by_text('Import blocked')).to_be_visible()
    expect(page.get_by_text('No files found are eligible for import')).to_be_visible()


@pytest.mark.parametrize('title,button,action,confirm', [
    ('E2E Show', 'Pause in qBittorrent', 'pause', False),
    ('E2E Paused', 'Resume in qBittorrent', 'resume', False),
    ('E2E Delayed', 'Grab now — skip the delay', 'grab', False),
    ('E2E Movie', 'Retry — blocklist this release and search for a different one', 'retry', True),
    ('E2E Show', 'Remove from the queue and qBittorrent', 'remove', True),
])
def test_queue_actions(page, title, button, action, confirm):
    queue_with_items(page)
    caught = intercept(page, '**/downloads/action/', {'ok': True, 'message': 'Done'})
    item = next(i for i in QUEUE['items'] if i['title'] == title)
    card = page.locator('div.glass', has=page.get_by_text(title, exact=True)).last
    card.get_by_title(button).click()
    page.wait_for_timeout(400)
    assert bool(page.dialogs_seen) == confirm
    assert caught.last()['json'] == {'action': action, 'app': item['app'], 'ids': item['ids'], 'hash': item['hash']}


def test_cancelled_remove_sends_nothing(page):
    queue_with_items(page)
    caught = intercept(page, '**/downloads/action/')
    page.dialog_answers = [False]
    page.locator('div.glass', has=page.get_by_text('E2E Show', exact=True)).last.get_by_title(re.compile('^Remove')).click()
    page.wait_for_timeout(400)
    assert not caught


@pytest.mark.parametrize('tab', ['waiting-tv', 'waiting-movies', 'wanted', 'history', 'failed'])
def test_tabs_load(page, tab):
    open_page(page, f'/downloads/#{tab}')
    page.wait_for_timeout(4000)   # lazy-loaded from Sonarr / Radarr
    body = page.locator('main').inner_text()
    assert 'Traceback' not in body and 'Server Error' not in body


def test_tab_buttons_switch_and_remember(page):
    open_page(page, '/downloads/')
    page.get_by_role('button', name=re.compile('Wanted')).click()
    assert page.url.endswith('#wanted')
    page.reload()
    expect(page.get_by_role('button', name=re.compile('Wanted'))).to_have_class(re.compile('bg-brand-600'))


def test_search_all_missing(page):
    open_page(page, '/downloads/#wanted')
    button = page.locator('button[onclick*="all_missing"]').first
    expect(button).to_be_visible(timeout=20_000)
    caught = intercept(page, '**/downloads/search/', {'ok': True, 'message': 'Sonarr is searching'})
    button.click()
    page.wait_for_timeout(400)
    assert caught.last()['json']['all_missing'] is True


def test_mark_a_grab_failed(page):
    open_page(page, '/downloads/#history')
    button = page.locator('button[onclick^="markFailed"]').first
    try:
        button.wait_for(timeout=15_000)
    except Exception:
        pytest.skip('no grabs in the history')
    caught = intercept(page, '**/downloads/history/*/*/failed/', {'ok': True, 'message': 'Marked failed'})
    button.click()
    page.wait_for_timeout(400)
    assert page.dialogs_seen and re.match(r'/downloads/history/(sonarr|radarr)/\d+/failed/', caught.last()['path'])


def test_imports_page_and_filters(page):
    open_page(page, '/downloads/imports/')
    for status in ('confirmed', 'replaced', 'missing'):
        page.goto(url(f'/downloads/imports/?status={status}'))
        expect(page.locator(f'a[href$="?status={status}"]')).to_have_class(re.compile(r'bg-(green|blue|red)-6'))
