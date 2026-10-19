"""App Settings — every tab. Saves that only write back the current values run
for real (they're what "I can't save settings" was about); anything that
would change Sonarr, Radarr, Prowlarr or qBittorrent is intercepted."""
import re

import pytest
from conftest import api_get, expect, intercept, open_page, toast


def settings_tab(page, key, wait_ms=2500):
    open_page(page, f'/settings/#{key}', wait_ms=wait_ms)
    pane = page.locator(f'div[x-show="tab === \'{key}\'"]').first   # the Tasks panel has a "tasks" sub-tab too
    expect(pane).to_be_visible()
    return pane


def test_tabs_switch_and_keep_the_address(page):
    open_page(page, '/settings/')
    for key, label in (('sonarr', 'Sonarr'), ('qbittorrent', 'qBittorrent'), ('foryou', 'For You'), ('tasks', 'Background Tasks')):
        page.locator('button', has_text=re.compile(f'^\\s*{label}\\s*$')).first.click()
        assert page.url.endswith(f'#{key}')
        expect(page.locator(f'div[x-show="tab === \'{key}\'"]')).to_be_visible()


# ── General ───────────────────────────────────────────────────────────────────

def test_general_save_sends_every_field(page):
    pane = settings_tab(page, 'general', wait_ms=500)
    caught = intercept(page, '**/settings/save/', {'ok': True, 'live': ['TMDB_API_KEY'], 'restart_required': []})
    pane.get_by_role('button', name='Save All').click()
    expect(pane.get_by_text('Saved to .env')).to_be_visible()
    sent = caught.last()['json']
    for key in ('TMDB_API_KEY', 'PLEX_URL', 'PLEX_TOKEN', 'NTFY_URL', 'NTFY_TOPIC', 'TZ', 'ALLOWED_HOSTS'):
        assert key in sent, key


def test_show_and_hide_secret_fields(page):
    pane = settings_tab(page, 'general', wait_ms=500)
    toggle = pane.get_by_role('button', name='Show token').first
    toggle.click()
    expect(pane.get_by_role('button', name='Hide token').first).to_be_visible()


def test_ntfy_test_button(page):
    pane = settings_tab(page, 'general', wait_ms=500)
    caught = intercept(page, '**/settings/ntfy-test/', {'ok': True})
    pane.get_by_role('button', name='Send Test').click()
    expect(pane.get_by_role('button', name='Sent!')).to_be_visible()
    assert caught


def test_phone_tab_bar_can_be_customised(page):
    pane = settings_tab(page, 'general', wait_ms=500)
    toggles = pane.locator('button[\\@click^="toggleNav("]')
    if not toggles.count():
        pytest.skip('no tab bar picker')
    before = page.evaluate('localStorage.getItem("dd-mobile-nav")')
    try:
        enabled = [b for b in toggles.all() if b.is_enabled()]
        enabled[-1].click()
        assert page.evaluate('localStorage.getItem("dd-mobile-nav")') != before
    finally:
        page.evaluate('v => v === null ? localStorage.removeItem("dd-mobile-nav") : localStorage.setItem("dd-mobile-nav", v)', before)


# ── Sonarr / Radarr ───────────────────────────────────────────────────────────

@pytest.fixture(params=['sonarr', 'radarr'])
def arr(request, page):
    pane = settings_tab(page, request.param)
    expect(pane.get_by_text(re.compile(r'\bv\d+\.\d+'))).to_be_visible(timeout=20_000)   # connected → version shown
    return request.param, pane


@pytest.mark.changes
def test_arr_save_connection_and_defaults(page, arr):
    """Real: saves the values already there — the fix for "can't save settings"."""
    kind, pane = arr
    with page.expect_response(f'**/arr/{kind}/config/') as resp:
        pane.get_by_role('button', name='Save', exact=True).first.click()
    body = resp.value.json()
    assert body['ok'] and body['connected'], body
    with page.expect_response(f'**/arr/{kind}/config/') as resp:
        pane.get_by_role('button', name='Save defaults').click()
    assert resp.value.json()['ok']
    expect(pane.get_by_role('button', name='Save defaults')).to_be_enabled()


def test_arr_test_connection(page, arr):
    kind, pane = arr
    with page.expect_response(f'**/arr/{kind}/test/') as resp:
        pane.get_by_role('button', name='Test', exact=True).first.click()
    assert resp.value.json()['ok']


def test_arr_root_folders(page, arr):
    kind, pane = arr
    added = intercept(page, f'**/arr/{kind}/rootfolders/', {'ok': True, 'id': 999})
    root_input = pane.locator('input[x-model="newRoot"]')
    root_input.fill('/e2e/new-root')
    pane.get_by_role('button', name='Add', exact=True).click()
    page.wait_for_timeout(400)
    assert added.last()['json'] == {'path': '/e2e/new-root'}


def test_arr_downloads_and_mappings(page, arr):
    kind, pane = arr
    downloads = intercept(page, f'**/arr/{kind}/downloads/', {'ok': True, 'downloads': {'category': kind, 'effective': '/downloads/' + kind, 'visible': True, 'local': '/media/downloads/' + kind, 'local_ok': True}})
    pane.locator('button[\\@click="saveDownloads()"]').click()
    page.wait_for_timeout(400)
    assert downloads.last()['json']['category']
    config = intercept(page, f'**/arr/{kind}/config/', {'ok': True, 'connected': True, 'version': 'e2e'})
    pane.locator('details:has(button[\\@click="saveMappings()"]) > summary').click()   # folded away by default
    pane.locator('button[\\@click="saveMappings()"]').click()
    page.wait_for_timeout(400)
    assert 'path_mappings' in config.last()['json']


def test_arr_webhook_and_qbittorrent_wiring(page, arr):
    kind, pane = arr
    hook = intercept(page, f'**/arr/{kind}/webhook/', {'ok': True, 'id': 1, 'url': 'http://web:8000/arr/webhook/x/y/'})
    pane.locator('button[\\@click="connect(\'webhook\')"]').click()
    page.wait_for_timeout(400)
    assert hook
    test = intercept(page, f'**/arr/{kind}/webhook/test/', {'ok': True})
    pane.locator('button[\\@click="testWebhook()"]').click()
    page.wait_for_timeout(400)
    assert test


def test_arr_quality_profiles(page, arr):
    kind, pane = arr
    pane.get_by_role('button', name='Show quality profiles').click()
    profiles = pane.locator('button[\\@click="selectProfile(p.id)"]')
    expect(profiles.first).to_be_visible(timeout=20_000)
    saved = intercept(page, f'**/arr/{kind}/quality/profiles/*/', {'ok': True})
    pane.locator('button[\\@click="saveProfile()"]').click()
    page.wait_for_timeout(500)
    sent = saved.last()['json']
    assert 'cutoff' in sent and 'allowed' in sent


def test_arr_naming(page, arr):
    kind, pane = arr
    saved = intercept(page, f'**/arr/{kind}/naming/', {'ok': True})
    pane.locator('button[\\@click="saveNaming()"]').click()
    page.wait_for_timeout(400)
    sent = saved.last()['json']
    assert set(sent) == {'naming', 'media_management'} and sent['naming']


def test_arr_plex_connection_status(page, arr):
    kind, _ = arr
    data = api_get(page, f'/arr/{kind}/plex/')
    assert 'ok' in data


# ── Prowlarr ──────────────────────────────────────────────────────────────────

@pytest.fixture
def prowlarr(page):
    pane = settings_tab(page, 'prowlarr')
    expect(pane.get_by_text(re.compile(r'Connected · v\d+'))).to_be_visible(timeout=20_000)
    return pane


@pytest.mark.changes
def test_prowlarr_save_connection(page, prowlarr):
    with page.expect_response('**/arr/prowlarr/config/') as resp:
        prowlarr.get_by_role('button', name='Save', exact=True).first.click()
    assert resp.value.json()['ok']


def test_prowlarr_indexer_actions(page, prowlarr):
    rows = prowlarr.locator('button[\\@click="toggle(ix)"]')
    if not rows.count():
        pytest.skip('no indexers in Prowlarr')
    toggled = intercept(page, '**/arr/prowlarr/indexers/*/toggle/', {'ok': True, 'enable': False})
    rows.first.click()
    page.wait_for_timeout(300)
    assert toggled
    removed = intercept(page, '**/arr/prowlarr/indexers/*/delete/', {'ok': True})
    prowlarr.locator('button[\\@click="remove(ix)"]').first.click()
    page.wait_for_timeout(300)
    assert page.dialogs_seen and removed


def test_prowlarr_add_indexer_dialog(page, prowlarr):
    prowlarr.get_by_role('button', name='Add indexer').click()
    pick = page.locator('button[\\@click="pickDefinition(d)"]')
    expect(pick.first).to_be_visible(timeout=30_000)
    pick.first.click()
    tested = intercept(page, '**/arr/prowlarr/indexers/test/', {'ok': True})
    page.locator('button[\\@click="testDefinition()"]').click()
    page.wait_for_timeout(300)
    assert tested.last()['json']['key']
    # Prowlarr refuses an enabled indexer that fails its test — then "Save disabled" is offered
    saved = intercept(page, '**/arr/prowlarr/indexers/save/', {'ok': False, 'error': 'Unable to connect to indexer', 'can_save_disabled': True})
    page.locator('button[\\@click="saveDefinition(false)"]').click()
    expect(page.locator('button[\\@click="saveDefinition(true)"]')).to_be_visible()
    assert saved.last()['json']['save_disabled'] is False
    page.unroute('**/arr/prowlarr/indexers/save/')
    saved = intercept(page, '**/arr/prowlarr/indexers/save/', {'ok': True, 'id': 999, 'disabled': True})
    page.locator('button[\\@click="saveDefinition(true)"]').click()
    page.wait_for_timeout(300)
    assert saved.last()['json']['save_disabled'] is True


def test_prowlarr_wiring_buttons(page, prowlarr):
    connect = intercept(page, '**/arr/prowlarr/apps/connect/', {'ok': True, 'results': {'sonarr': 'connected', 'radarr': 'connected'}})
    sync = intercept(page, '**/arr/prowlarr/apps/sync/', {'ok': True})
    testall = intercept(page, '**/arr/prowlarr/indexers/testall/', {'ok': True, 'tested': 0, 'failed': 0, 'failures': {}})
    prowlarr.get_by_role('button', name='Connect Sonarr & Radarr').click()
    prowlarr.get_by_role('button', name='Sync indexers now').click()
    prowlarr.get_by_role('button', name='Test all').click()
    page.wait_for_timeout(500)
    assert connect and sync and testall


# ── qBittorrent ───────────────────────────────────────────────────────────────

@pytest.fixture
def qbittorrent(page):
    pane = settings_tab(page, 'qbittorrent')
    data = api_get(page, '/qbt/settings/data/')
    if not data['connected']:
        pytest.skip(f'qBittorrent is not connected: {data["error"]}')
    return pane


@pytest.mark.changes
def test_qbittorrent_save_and_test_connection(page, qbittorrent):
    """Real: writes the current login back to .env and logs in with it."""
    with page.expect_response('**/qbt/settings/connection/') as resp:
        qbittorrent.get_by_role('button', name='Save & test').click()
    assert resp.value.json()['connected']


def test_qbittorrent_preferences_and_categories(page, qbittorrent):
    prefs = intercept(page, '**/qbt/settings/save/', {'ok': True})
    qbittorrent.get_by_role('button', name='Save preferences').click()
    page.wait_for_timeout(300)
    assert prefs.last()['json']
    qbittorrent.get_by_role('button', name='Save folders').click()
    page.wait_for_timeout(300)
    assert 'save_path' in prefs.last()['json']
    cats = intercept(page, '**/qbt/categories/save/', {'ok': True, 'category_paths_switched_on': False})
    qbittorrent.locator('input[x-model="newCat.name"]').fill('e2e-category')
    qbittorrent.locator('button[\\@click="saveCategory(newCat, true)"]').click()
    page.wait_for_timeout(300)
    assert cats.last()['json']['name'] == 'e2e-category'


# ── For You ───────────────────────────────────────────────────────────────────

@pytest.mark.changes
def test_for_you_settings_save_and_restore(page):
    pane = settings_tab(page, 'foryou', wait_ms=1200)
    before = api_get(page, '/foryou/settings/')['settings']
    try:
        pane.get_by_role('button', name='3+ titles').click()
        pane.get_by_role('button', name='Save', exact=True).click()
        expect(toast(page, 'Saved')).to_be_visible()
        assert api_get(page, '/foryou/settings/')['settings']['min_matches'] == 3
    finally:
        pane.get_by_role('button', name='Any 1' if before['min_matches'] == 1 else f'{before["min_matches"]}+ titles').click()
        pane.get_by_role('button', name='Save', exact=True).click()
        page.wait_for_timeout(600)
    assert api_get(page, '/foryou/settings/')['settings'] == before


def test_for_you_needs_movies_or_tv(page):
    pane = settings_tab(page, 'foryou', wait_ms=1200)
    caught = intercept(page, '**/foryou/settings/', {'ok': False, 'error': 'Keep movies or TV switched on'}, status=400, method='POST')
    pane.get_by_role('switch', name=re.compile('Movies')).click()
    pane.get_by_role('switch', name=re.compile('TV shows')).click()
    pane.get_by_role('button', name='Save', exact=True).click()
    expect(toast(page, 'Keep movies or TV switched on')).to_be_visible()
    assert caught.last()['json']['include_movies'] is False


@pytest.mark.parametrize('button,endpoint,confirm', [
    ('Clear the deck', 'clear', True), ('Reset swipe history', 'reset-history', True), ('Rebuild now', 'rebuild', False)])
def test_for_you_deck_buttons(page, button, endpoint, confirm):
    pane = settings_tab(page, 'foryou', wait_ms=1200)
    reply = {'ok': True, 'task_id': 'e2e'} if endpoint == 'rebuild' else {**api_get(page, '/foryou/settings/'), 'message': 'E2E done'}
    caught = intercept(page, f'**/foryou/settings/{endpoint}/', reply)
    if endpoint == 'rebuild':
        intercept(page, '**/tasks/poll/e2e/', {'status': 'SUCCESS', 'result': '"E2E rebuilt"'})
    pane.get_by_role('button', name=button).click()
    expect(pane.get_by_text(re.compile('E2E (done|rebuilt)'))).to_be_visible(timeout=10_000)
    assert caught and bool(page.dialogs_seen) == confirm


# ── Background Tasks ──────────────────────────────────────────────────────────

def test_run_a_task_now(page):
    """Real: Keep Pages Fast just re-fetches TMDB / Plex into the cache."""
    pane = settings_tab(page, 'tasks')
    card = pane.locator('#card-warm_caches')
    with page.expect_response('**/tasks/trigger/') as resp:
        card.get_by_role('button', name='Run Now').click()
    task_id = resp.value.json()['task_id']
    expect(card.get_by_role('button', name='Running…')).to_be_visible()
    expect(card.get_by_role('button', name='Run Now')).to_be_visible(timeout=90_000)
    assert api_get(page, f'/tasks/poll/{task_id}/')['status'] == 'SUCCESS'


def test_schedules_and_history(page):
    pane = settings_tab(page, 'tasks')
    pane.get_by_role('button', name=re.compile('Schedules')).click()
    toggles = pane.locator('button[\\@click="toggle()"]:visible')
    expect(toggles.first).to_be_visible()
    caught = intercept(page, '**/tasks/schedule/*/toggle/', {'enabled': False})
    toggles.first.click()
    page.wait_for_timeout(300)
    assert caught
    pane.get_by_role('button', name='History').click()
    expect(pane.get_by_role('button', name='Refresh')).to_be_visible()


# ── FlareSolverr (Prowlarr tab) ───────────────────────────────────────────────

CLOUDFLARE = {'ok': False, 'error': 'Unable to access example.to, blocked by CloudFlare Protection.', 'cloudflare': True}


def test_flaresolverr_status(page, prowlarr):
    status = api_get(page, '/arr/prowlarr/flaresolverr/')
    card = page.locator('[x-init="loadFlare()"]')
    expect(card.get_by_role('heading', name='FlareSolverr')).to_be_visible()
    if status['ready']:
        expect(card.get_by_text(re.compile(r'Running'))).to_be_visible()
    else:
        expect(card.get_by_text(status['unreachable'])).to_be_visible()
    expect(card.locator('#flareUrl')).to_have_value(status['url'])


def test_flaresolverr_connect_sends_the_address(page, prowlarr):
    status = api_get(page, '/arr/prowlarr/flaresolverr/')
    caught = intercept(page, '**/arr/prowlarr/flaresolverr/', {**status, 'ok': True, 'connected': True}, method='POST')
    card = page.locator('[x-init="loadFlare()"]')
    card.locator('#flareUrl').fill('http://my-flaresolverr:8191')
    card.get_by_role('button', name=re.compile('Connect to Prowlarr|Update')).click()
    expect(toast(page, 'FlareSolverr connected to Prowlarr')).to_be_visible()
    assert caught.last()['json'] == {'url': 'http://my-flaresolverr:8191'}


def test_indexer_flaresolverr_switch(page, prowlarr):
    switch = prowlarr.get_by_role('button', name=re.compile('^FlareSolverr for ')).first
    if not switch.count():
        pytest.skip('no indexers in Prowlarr')
    was_on = switch.get_attribute('aria-pressed') == 'true'
    caught = intercept(page, '**/arr/prowlarr/indexers/*/flaresolverr/', {'ok': True, 'flaresolverr': not was_on})
    intercept(page, '**/arr/prowlarr/flaresolverr/', api_get(page, '/arr/prowlarr/flaresolverr/'))
    switch.click()
    expect(switch).to_have_attribute('aria-pressed', str(not was_on).lower())
    assert caught.last()['json'] == {'enabled': not was_on}


def test_cloudflare_block_offers_flaresolverr(page, prowlarr):
    rows = prowlarr.locator('tr', has=page.locator('button[aria-pressed="false"][aria-label^="FlareSolverr for"]'))
    if not rows.count():
        pytest.skip('every indexer already uses FlareSolverr')
    row = rows.first
    intercept(page, '**/arr/prowlarr/indexers/*/test/', CLOUDFLARE)
    switched = intercept(page, '**/arr/prowlarr/indexers/*/flaresolverr/', {'ok': True, 'flaresolverr': True})
    intercept(page, '**/arr/prowlarr/flaresolverr/', api_get(page, '/arr/prowlarr/flaresolverr/'))
    row.get_by_title('Test', exact=True).click()
    offer = row.get_by_role('button', name=re.compile('Blocked by Cloudflare — use FlareSolverr'))
    expect(offer).to_be_visible()
    offer.click()
    expect(offer).to_be_hidden()
    assert switched.last()['json'] == {'enabled': True}


def test_new_indexer_blocked_by_cloudflare_retries_through_flaresolverr(page, prowlarr):
    prowlarr.get_by_role('button', name='Add indexer').click()
    pick = page.locator('button[\\@click="pickDefinition(d)"]')
    expect(pick.first).to_be_visible(timeout=30_000)
    pick.first.click()
    replies = [CLOUDFLARE, {'ok': True, 'id': 999, 'disabled': False}]
    sent = []

    def save(route):
        import json as _json
        sent.append(_json.loads(route.request.post_data))
        route.fulfill(status=200, content_type='application/json', body=_json.dumps(replies[len(sent) - 1]))
    page.route('**/arr/prowlarr/indexers/save/', save)
    page.locator('button[\\@click="saveDefinition(false)"]').click()
    retry = page.get_by_role('button', name='Use FlareSolverr and try again')
    expect(retry).to_be_visible()
    retry.click()
    expect(toast(page, 'Indexer saved')).to_be_visible()
    assert sent[0]['flaresolverr'] is False and sent[1]['flaresolverr'] is True
