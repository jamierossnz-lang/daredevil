"""
End-to-end tests — a real browser driving a running Daredevil.

    pip install -r tests/e2e/requirements.txt && playwright install chromium
    DD_BASE=http://localhost:8000 DD_USERNAME=… DD_PASSWORD=… pytest tests/e2e

(DD_SESSION=<sessionid cookie> instead of a username and password works too.)

They run against your real Sonarr, Radarr, Prowlarr, qBittorrent and Plex, so:

* reading is real — pages, lists, searches, the calendar feed;
* anything destructive (delete, remove, grab, swipe, clear, reset) is caught
  in the browser: the test checks the exact request the UI sends and answers
  it itself, so nothing is touched;
* the few real changes — a favourite, a monitoring switch, re-saving
  settings, adding a throwaway title — are put back before the test ends.
"""
import json
import os
import re
from urllib.parse import parse_qs

import pytest
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get('DD_BASE', 'http://localhost:8000').rstrip('/')
PHONE = {'viewport': {'width': 390, 'height': 844}, 'device_scale_factor': 2, 'is_mobile': True, 'has_touch': True}
DESKTOP = {'viewport': {'width': 1440, 'height': 900}}

# Console noise that isn't a Daredevil bug
IGNORED_CONSOLE = (
    'cdn.tailwindcss.com should not be used in production',  # dev server without the built CSS
    'Failed to load resource',                                 # a missing poster on TMDB / Plex
)

expect.set_options(timeout=15_000)


def url(path):
    return f'{BASE}{path}'


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get('DD_CHROMIUM') or None)
        yield b
        b.close()


@pytest.fixture(scope='session')
def signed_in(browser):
    """Storage state of a signed-in session, shared by every test."""
    ctx = browser.new_context()
    if os.environ.get('DD_SESSION'):
        ctx.add_cookies([{'name': 'sessionid', 'value': os.environ['DD_SESSION'], 'url': BASE}])
    elif os.environ.get('DD_USERNAME'):
        page = ctx.new_page()
        page.goto(url('/accounts/login/'))
        page.fill('input[name=login]', os.environ['DD_USERNAME'])
        page.fill('input[name=password]', os.environ.get('DD_PASSWORD', ''))
        page.click('form button[type=submit]')
        page.wait_for_load_state()
    else:
        pytest.exit('Set DD_USERNAME and DD_PASSWORD (or DD_SESSION) to sign in', returncode=2)
    page = ctx.new_page()
    page.goto(url('/'))
    if '/accounts/login' in page.url:
        pytest.exit(f'Could not sign in to {BASE}', returncode=2)
    state = ctx.storage_state()
    ctx.close()
    return state


def _page(browser, state, options):
    ctx = browser.new_context(storage_state=state, **options)
    page = ctx.new_page()
    page.js_errors = []
    page.on('pageerror', lambda e: page.js_errors.append(f'uncaught: {e}'))
    page.on('console', lambda m: m.type == 'error' and not any(s in m.text for s in IGNORED_CONSOLE)
            and page.js_errors.append(f'console: {m.text}'))
    # Answer confirm() / prompt() with the queue the test sets (default: OK)
    page.dialog_answers = []
    page.dialogs_seen = []

    def on_dialog(d):
        page.dialogs_seen.append(d.message)
        answer = page.dialog_answers.pop(0) if page.dialog_answers else True
        d.accept() if answer else d.dismiss()
    page.on('dialog', on_dialog)
    return ctx, page


@pytest.fixture
def page(browser, signed_in):
    """A phone-sized page (Daredevil is mobile first). Fails the test on any JS error."""
    ctx, pg = _page(browser, signed_in, PHONE)
    yield pg
    errors = pg.js_errors
    ctx.close()
    assert not errors, '\n'.join(errors)


@pytest.fixture
def desktop(browser, signed_in):
    ctx, pg = _page(browser, signed_in, DESKTOP)
    yield pg
    errors = pg.js_errors
    ctx.close()
    assert not errors, '\n'.join(errors)


@pytest.fixture
def anon(browser):
    """A signed-out page."""
    ctx = browser.new_context(**PHONE)
    yield ctx.new_page()
    ctx.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def open_page(page, path, wait_ms=0):
    """Go to `path` and check it answered (not an error page or the login form)."""
    resp = page.goto(url(path))
    assert resp is not None and resp.status < 400, f'{path} → HTTP {resp.status if resp else "no response"}'
    assert '/accounts/login' not in page.url, f'{path} redirected to the login page'
    if wait_ms:
        page.wait_for_timeout(wait_ms)
    return resp


class Intercepted(list):
    """Requests caught by intercept(): each is {'method', 'url', 'path', 'json', 'form'}."""

    def last(self):
        assert self, 'the UI sent no request'
        return self[-1]


def intercept(page, pattern, reply=None, status=200, method=None):
    """
    Answer requests matching `pattern` (a glob or regex) with `reply` (JSON)
    instead of letting them reach Daredevil, and record them. `method`
    limits it to e.g. POST, letting the page's GETs through.
    """
    caught = Intercepted()

    def handle(route):
        req = route.request
        if method and req.method != method:
            return route.continue_()
        body = req.post_data or ''
        try:
            as_json = json.loads(body) if body else None
        except ValueError:
            as_json = None
        caught.append({
            'method': req.method, 'url': req.url, 'path': re.sub(r'^https?://[^/]+', '', req.url),
            'json': as_json, 'form': _form(req, body) if body and as_json is None else {},
        })
        route.fulfill(status=status, content_type='application/json', body=json.dumps(reply if reply is not None else {'ok': True}))
    page.route(re.compile(pattern) if pattern.startswith('^') else pattern, handle)
    return caught


def _form(req, body):
    """Form fields of an urlencoded or multipart (FormData) body — a list where a name repeats."""
    if 'multipart/form-data' in (req.headers.get('content-type') or ''):
        pairs = re.findall(r'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, re.S)
    else:
        pairs = [(k, v) for k, vs in parse_qs(body).items() for v in vs]
    fields = {}
    for k, v in pairs:
        fields[k] = [*fields[k], v] if isinstance(fields.get(k), list) else ([fields[k], v] if k in fields else v)
    return fields


def api_get(page, path):
    resp = page.request.get(url(path))
    assert resp.ok, f'GET {path} → {resp.status}'
    return resp.json()


def api_post(page, path, data=None):
    """POST JSON as the signed-in user (with Django's CSRF token)."""
    if not any(c['name'] == 'csrftoken' for c in page.context.cookies()):
        page.goto(url('/settings/'))
    token = next(c['value'] for c in page.context.cookies() if c['name'] == 'csrftoken')
    resp = page.request.post(url(path), data=json.dumps(data or {}),
                             headers={'Content-Type': 'application/json', 'X-CSRFToken': token, 'Referer': url('/')})
    try:
        return resp.status, resp.json()
    except ValueError:
        return resp.status, resp.text()


def toast(page, text):
    """The ddToast message containing `text`."""
    return page.locator('div.fixed', has_text=text).last
