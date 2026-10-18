"""Every page opens, on a phone and on a desktop, without errors — and the
login wall holds."""
import pytest
from conftest import expect, open_page, url

PAGES = [
    ('/', 'Dashboard'),
    ('/shows/', 'TV Shows'),
    ('/shows/search/?q=office', None),
    ('/movies/', 'Movies'),
    ('/movies/search/?q=dune', None),
    ('/streaming/', None),
    ('/foryou/', 'For You'),
    ('/calendar/', 'Calendar'),
    ('/search/', None),
    ('/downloads/', None),
    ('/downloads/imports/', None),
    ('/qbt/', None),
    ('/qbt/files/', None),
    ('/plex/', None),
    ('/activity/', None),
    ('/settings/', 'App Settings'),
]

# Pages that moved — their old addresses should land in the right place
REDIRECTS = [
    ('/tasks/', '/settings/'),
    ('/qbt/settings/', '/settings/'),
    ('/qbt/categories/', '/settings/'),
]


@pytest.mark.parametrize('path,title', PAGES, ids=[p for p, _ in PAGES])
def test_page_opens_on_a_phone(page, path, title):
    open_page(page, path, wait_ms=1200)
    if title:
        expect(page.locator('h1').first).to_contain_text(title)
    # Nothing wider than the screen (no sideways scrolling)
    width = page.evaluate('document.documentElement.scrollWidth')
    assert width <= 390, f'{path} is {width}px wide on a 390px phone'


@pytest.mark.parametrize('path,title', PAGES, ids=[p for p, _ in PAGES])
def test_page_opens_on_a_desktop(desktop, path, title):
    open_page(desktop, path, wait_ms=1200)
    expect(desktop.locator('body')).not_to_contain_text('Traceback')


@pytest.mark.parametrize('old,new', REDIRECTS)
def test_moved_pages_redirect(page, old, new):
    page.goto(url(old))
    assert page.url.startswith(url(new))


def test_signed_out_visitors_get_the_login_page(anon):
    anon.goto(url('/shows/'))
    assert '/accounts/login/' in anon.url and 'next=/shows/' in anon.url
    expect(anon.locator('input[name=login]')).to_be_visible()
    expect(anon.locator('input[name=password]')).to_be_visible()


def test_wrong_password_is_refused(anon):
    anon.goto(url('/accounts/login/'))
    anon.fill('input[name=login]', 'e2e-nobody')
    anon.fill('input[name=password]', 'not-the-password')
    anon.click('form button[type=submit]')
    anon.wait_for_load_state()
    assert '/accounts/login/' in anon.url
    expect(anon.locator('body')).to_contain_text('not correct')


def test_signed_out_api_calls_are_refused(anon):
    for path in ('/foryou/settings/', '/qbt/torrents.json', '/calendar/events/?start=2026-01-01&end=2026-01-31'):
        resp = anon.request.get(url(path), max_redirects=0)
        assert resp.status == 302 and '/accounts/login/' in resp.headers['location'], path


def test_webhook_rejects_a_wrong_secret(anon):
    resp = anon.request.post(url('/arr/webhook/sonarr/not-the-secret/'), data='{"eventType": "Test"}',
                             headers={'Content-Type': 'application/json'})
    assert resp.status == 403


def test_calendar_feed_needs_the_right_token(anon):
    assert anon.request.get(url('/calendar/feed/not-the-token.ics')).status == 404


def test_phone_tab_bar_and_more_menu(page):
    open_page(page, '/')
    bar = page.locator('#ddMobileNavInner')
    expect(bar).to_be_visible()
    for label in ('Home', 'Shows', 'Movies'):
        expect(bar).to_contain_text(label)
    bar.locator('button', has_text='More').click()
    menu = page.locator('div[x-show="sidebarOpen"]')
    expect(menu).to_be_visible()
    menu.get_by_role('link', name='Calendar').click()
    page.wait_for_url(url('/calendar/'))


def test_desktop_sidebar_links_all_work(desktop):
    open_page(desktop, '/')
    hrefs = desktop.eval_on_selector_all('aside a[href^="/"]', 'els => [...new Set(els.map(e => e.getAttribute("href")))]')
    assert len(hrefs) >= 8, hrefs
    for href in hrefs:
        resp = desktop.request.get(url(href))
        assert resp.ok, f'sidebar link {href} → {resp.status}'


def test_notification_bell_opens(page):
    open_page(page, '/')
    page.click('button[title="Notifications"]')
    expect(page.locator('[x-ref="list"]')).to_be_visible()
    page.wait_for_timeout(800)
    expect(page.locator('[x-ref="list"]')).not_to_be_empty()
