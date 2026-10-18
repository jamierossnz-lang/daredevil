"""The bell (notification centre), which notifications to get, and the Activity log."""
import re

from conftest import api_get, expect, intercept, open_page

PANEL = '''
<div class="group" id="notif-9001"><div class="flex-1"><p>E2E Episode Grabbed</p><p>E2E Show S01E01</p></div>
  <button aria-label="Dismiss" @click="dismiss(9001)">x</button></div>
<div class="group" id="notif-9002"><div class="flex-1"><p>E2E Ready to Watch</p><p>E2E Movie</p></div>
  <button aria-label="Dismiss" @click="dismiss(9002)">x</button></div>
'''


def bell(page):
    page.route('**/notifications/panel/', lambda r: r.fulfill(status=200, content_type='text/html', body=PANEL))
    open_page(page, '/')
    page.get_by_role('button', name='Notifications').click()
    expect(page.get_by_text('E2E Episode Grabbed')).to_be_visible()


def test_unread_count(page):
    assert isinstance(api_get(page, '/notifications/count/')['count'], int)


def test_real_panel_opens(page):
    open_page(page, '/')
    with page.expect_response('**/notifications/panel/') as resp:
        page.get_by_role('button', name='Notifications').click()
    assert resp.value.ok


def test_dismiss_one(page):
    bell(page)
    caught = intercept(page, '**/notifications/9001/dismiss/')
    page.locator('#notif-9001').get_by_role('button', name='Dismiss').click()
    expect(page.locator('#notif-9001')).to_have_count(0)
    expect(page.get_by_text('E2E Ready to Watch')).to_be_visible()
    page.wait_for_timeout(300)
    assert caught.last()['method'] == 'POST'
    intercept(page, '**/notifications/9002/dismiss/')
    page.locator('#notif-9002').get_by_role('button', name='Dismiss').click()
    expect(page.get_by_text('No notifications')).to_be_visible()   # the last one gone → empty state


def test_clear_all(page):
    bell(page)
    caught = intercept(page, '**/notifications/clear/')
    page.get_by_role('button', name='Clear all').click()
    expect(page.get_by_text('No notifications')).to_be_visible()
    assert caught


def test_choose_which_notifications_to_get(page):
    open_page(page, '/settings/#general', wait_ms=1000)
    switches = page.locator('[role=switch][\\:aria-checked^="String(notifPrefs"]')
    expect(switches.first).to_be_visible()
    caught = intercept(page, '**/notifications/prefs/save/')
    before = switches.first.get_attribute('aria-checked')
    switches.first.click()
    expect(switches.first).not_to_have_attribute('aria-checked', before)
    page.wait_for_timeout(300)
    key, value = next((k, v) for k, v in caught.last()['json'].items() if str(v).lower() != before)
    assert isinstance(value, bool)


def test_activity_log_filters(page):
    open_page(page, '/activity/')
    chips = page.locator('a[href^="?type="]')
    expect(chips.first).to_be_visible()
    href = chips.nth(1).get_attribute('href')
    chips.nth(1).click()
    page.wait_for_url(re.compile(re.escape(href) + '$'))
    expect(page.locator('main')).not_to_contain_text('Traceback')
