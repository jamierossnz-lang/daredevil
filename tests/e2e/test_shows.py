"""TV Shows — the library, a show's page, searching and adding (Sonarr)."""
import re

import pytest
from conftest import api_get, api_post, expect, intercept, open_page, toast, url

from data import RELEASES


def library(page):
    """[{id, name, status, monitored, favourite, files, wanted}] from the TV Shows page."""
    open_page(page, '/shows/')
    return page.eval_on_selector_all('#posterGrid > [data-id]', '''cards => cards.map(c => {
        const [files, wanted] = (c.innerText.match(/(\\d+)\\/(\\d+)/) || [0, 0, 0]).slice(1).map(Number);
        return {id: Number(c.dataset.id), name: c.dataset.name, status: c.dataset.status,
                monitored: c.dataset.monitored === '1', favourite: c.dataset.favourite === '1', files, wanted};
    })''')


@pytest.fixture(scope='module')
def shows(browser, signed_in):
    ctx = browser.new_context(storage_state=signed_in)
    found = library(ctx.new_page())
    ctx.close()
    if not found:
        pytest.skip('Sonarr has no shows')
    return found


@pytest.fixture(scope='module')
def show(shows):
    """The show with the most episodes on disk — it has seasons, episodes and files to test with."""
    return max(shows, key=lambda s: (s['files'], s['wanted']))


def more_actions(page):
    """Open the show page's "…" menu (Search missing, Refresh, Sonarr, Remove)."""
    page.get_by_role('button', name='More actions').click()


def card(page, show_id):
    return page.locator(f'#posterGrid > [data-id="{show_id}"]')


def card_menu(page, show_id):
    c = card(page, show_id)
    c.hover()
    c.get_by_label('More actions').click()
    return c


def open_season_of(page, locator):
    """Switch to the season tab that holds `locator` (other seasons are hidden)."""
    season = locator.first.evaluate('el => el.closest("[x-show^=\'active ===\']").getAttribute("x-show").split("=== ")[1]')
    page.locator(f'button[\\@click="active = {season}"]').click()


# ── Library ───────────────────────────────────────────────────────────────────

def test_search_box_filters_the_library(page, shows):
    open_page(page, '/shows/')
    target = shows[len(shows) // 2]
    page.fill('#filterInput', target['name'][:6])
    visible = page.eval_on_selector_all('#posterGrid > [data-id]', 'cs => cs.filter(c => c.offsetParent).map(c => c.dataset.name)')
    assert target['name'] in visible
    assert all(target['name'][:6] in name for name in visible)
    # The Add button offers to search for what was typed
    expect(page.locator('#addShowBtn')).to_have_attribute('href', re.compile(r'/shows/search/\?q='))
    page.fill('#filterInput', 'zz-no-such-show-zz')
    expect(page.locator('#noMatches')).to_be_visible()


@pytest.mark.parametrize('status,check', [
    ('ended', lambda s: s['status'] == 'ended'),
    ('continuing', lambda s: s['status'] == 'continuing'),
    ('unmonitored', lambda s: not s['monitored']),
    ('favourite', lambda s: s['favourite']),
])
def test_status_filter(page, shows, status, check):
    open_page(page, '/shows/')
    page.select_option('#filterStatus', status)
    visible = set(page.eval_on_selector_all('#posterGrid > [data-id]', 'cs => cs.filter(c => c.offsetParent).map(c => Number(c.dataset.id))'))
    assert visible == {s['id'] for s in shows if check(s)}


def test_library_tabs(page, shows):
    open_page(page, '/shows/')
    page.click('#libTabs [data-tab="recent"]')
    expect(page.locator('#libTabs [data-tab="recent"]')).to_have_attribute('aria-pressed', 'true')
    page.click('#libTabs [data-tab="watching"]')
    page.wait_for_timeout(2500)  # asks Plex
    shown = page.eval_on_selector_all('#posterGrid > [data-id]', 'cs => cs.filter(c => c.offsetParent).length')
    assert shown or page.locator('#noMatches').is_visible()
    data = api_get(page, '/shows/watching/')
    assert data['ok'] or 'Plex' in data['error']


def test_card_menu_search_missing(page, show):
    open_page(page, '/shows/')
    caught = intercept(page, f'**/shows/{show["id"]}/download/', {'ok': True, 'message': 'Searching all monitored missing episodes'})
    card_menu(page, show['id']).get_by_role('button', name='Search missing').click()
    expect(toast(page, 'Searching all monitored')).to_be_visible()
    assert caught.last()['json'] == {'scope': 'monitored'}


def test_card_menu_remove_asks_twice_and_can_keep_files(page, show):
    open_page(page, '/shows/')
    caught = intercept(page, f'**/shows/{show["id"]}/delete/')
    page.dialog_answers = [True, False]  # remove it — but keep the files
    card_menu(page, show['id']).get_by_role('button', name='Remove from Sonarr').click()
    page.wait_for_timeout(500)
    assert len(page.dialogs_seen) == 2 and 'delete' in page.dialogs_seen[1].lower()
    assert caught.last()['json'] == {'delete_files': False}


def test_card_menu_remove_cancelled_sends_nothing(page, show):
    open_page(page, '/shows/')
    caught = intercept(page, f'**/shows/{show["id"]}/delete/')
    page.dialog_answers = [False]
    card_menu(page, show['id']).get_by_role('button', name='Remove from Sonarr').click()
    page.wait_for_timeout(500)
    assert not caught


@pytest.mark.changes
def test_favourite_and_unfavourite(page, show):
    """Real: favourite in Sonarr (a tag) — then put it back."""
    before = show['favourite']
    open_page(page, f'/shows/{show["id"]}/')
    for expected in (not before, before):
        with page.expect_navigation():
            page.click('#favBtn')
        assert next(s for s in library(page) if s['id'] == show['id'])['favourite'] == expected
        open_page(page, f'/shows/{show["id"]}/')


@pytest.mark.changes
def test_stop_and_restart_monitoring(page, shows):
    """Real: switch a monitored show off and on again from the library menu."""
    target = next((s for s in shows if s['monitored']), None)
    if not target:
        pytest.skip('no monitored show')
    open_page(page, '/shows/')
    try:
        with page.expect_navigation():
            card_menu(page, target['id']).get_by_role('button', name='Stop monitoring').click()
        assert card(page, target['id']).get_attribute('data-monitored') == ''
    finally:
        status, body = api_post(page, f'/shows/{target["id"]}/monitor/', {'monitored': True})
        assert status == 200 and body['ok']
    open_page(page, '/shows/')
    assert card(page, target['id']).get_attribute('data-monitored') == '1'


# ── A show's page ─────────────────────────────────────────────────────────────

def test_show_page_lists_seasons_and_episodes(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    seasons = page.locator('button[\\@click^="active ="]')
    assert seasons.count() >= 1
    seasons.last.click()
    expect(page.locator('.ep-row:visible').first).to_be_visible()
    status = api_get(page, f'/shows/{show["id"]}/status/')
    assert status['ok'] and isinstance(status['has_file'], list)


def test_download_whole_show_from_the_dialog(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    caught = intercept(page, f'**/shows/{show["id"]}/download/', {'ok': True, 'message': 'Searching'})
    page.get_by_role('button', name='Download', exact=True).click()
    expect(page.locator('#dlModal')).to_be_visible()
    page.check('input[name=dlMode][value=seasons]')
    expect(page.locator('#seasonPicker')).to_be_visible()
    page.locator('#seasonPicker label').first.click()
    page.click('#dlSubmit')
    expect(page.locator('#dlModal')).to_be_hidden()
    sent = caught.last()['json']
    assert sent['scope'] == 'seasons' and len(sent['seasons']) == 1


def test_download_a_season(page, shows):
    """On a show with a season that isn't complete."""
    for s in sorted(shows, key=lambda s: s['files'] - s['wanted']):
        open_page(page, f'/shows/{s["id"]}/')
        button = page.locator('button', has_text='Download season')
        if button.count():
            break
    else:
        pytest.skip('every season of every show is complete')
    open_season_of(page, button)
    caught = intercept(page, f'**/shows/{s["id"]}/download/', {'ok': True, 'message': 'Searching 1 season'})
    page.locator('button:visible', has_text='Download season').first.click()
    expect(toast(page, 'Searching 1 season')).to_be_visible()
    assert caught.last()['json']['scope'] == 'seasons'


def test_select_episodes_then_download_or_monitor_them(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    boxes = page.locator('.ep-select:visible')
    if boxes.count() < 2:
        pytest.skip('needs two episodes')
    ids = [int(boxes.nth(i).get_attribute('value')) for i in range(2)]
    boxes.nth(0).check()
    boxes.nth(1).check()
    expect(page.locator('#bulkCount')).to_have_text('2 episodes')

    caught = intercept(page, f'**/shows/{show["id"]}/download/', {'ok': True, 'message': 'Searching 2 episodes'})
    page.locator('#bulkBar').get_by_role('button', name='Download').click()
    expect(page.locator('#bulkBar')).to_be_hidden()
    assert caught.last()['json'] == {'scope': 'episodes', 'episode_ids': ids}

    boxes.nth(0).check()
    monitor = intercept(page, '**/shows/episodes/monitor/')
    page.locator('#bulkBar').get_by_role('button', name='Unmonitor').click()
    page.wait_for_timeout(300)
    assert monitor.last()['json'] == {'episode_ids': ids[:1], 'monitored': False}


def test_monitoring_switches_send_the_right_requests(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    caught = intercept(page, f'**/shows/{show["id"]}/seasons/*/monitor/')
    page.locator('[x-show^="active ==="]:visible button[title*="season" i]').first.click()
    page.wait_for_timeout(300)
    assert 'monitored' in caught.last()['json']

    open_page(page, f'/shows/{show["id"]}/')
    caught = intercept(page, '**/shows/episodes/monitor/')
    page.locator('.ep-row:visible button[title*="onitor"]').first.click()
    page.wait_for_timeout(300)
    assert len(caught.last()['json']['episode_ids']) == 1

    open_page(page, f'/shows/{show["id"]}/')
    caught = intercept(page, f'**/shows/{show["id"]}/monitoring/')
    page.select_option('#monitorPreset', 'future')
    page.wait_for_timeout(300)
    assert page.dialogs_seen and caught.last()['json'] == {'monitor': 'future'}


def test_change_quality_profile(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    options = page.eval_on_selector_all('#qualityProfile option', 'os => os.map(o => o.value).filter(Boolean)')
    caught = intercept(page, f'**/shows/{show["id"]}/edit/')
    page.select_option('#qualityProfile', options[-1])
    expect(toast(page, 'Quality profile updated')).to_be_visible()
    assert caught.last()['json'] == {'quality_profile_id': options[-1]}


def test_refresh_asks_sonarr_to_rescan(page, show):
    """Real, and harmless: Sonarr refreshes metadata and rescans the folder."""
    open_page(page, f'/shows/{show["id"]}/')
    more_actions(page)
    page.click('button[title^="Refresh metadata"]')
    expect(toast(page, 'Sonarr is refreshing')).to_be_visible()


def test_pick_a_release_for_a_season(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    intercept(page, f'**/shows/{show["id"]}/releases/?season=*', RELEASES)
    grab = intercept(page, '**/shows/releases/grab/')
    page.locator('button:visible[title^="Pick a release yourself (season"]').first.click()
    modal = page.locator('[x-data="arrReleaseModal()"]')
    expect(modal.get_by_text('Show.S01E01.1080p.WEB-DL')).to_be_visible()
    expect(modal.get_by_text('Not wanted in profile')).to_be_visible()
    # Filter, hide rejected, sort
    modal.get_by_placeholder('Filter results…').fill('720p')
    expect(modal.get_by_text('3', exact=False).first).to_be_visible()
    expect(modal.locator('p[x-text="r.title"]')).to_have_count(1)
    modal.get_by_placeholder('Filter results…').fill('')
    modal.get_by_label('Show rejected').uncheck()
    expect(modal.locator('p[x-text="r.title"]')).to_have_count(2)
    modal.locator('select[x-model="sort"]').select_option('size')
    expect(modal.locator('p[x-text="r.title"]').first).to_contain_text('1080p')
    modal.get_by_role('button', name='Grab').first.click()
    expect(toast(page, 'Grabbed — sent to qBittorrent by Sonarr')).to_be_visible()
    assert grab.last()['json'] == {'guid': 'e2e-best', 'indexer_id': 7}


def test_pick_a_release_shows_sonarr_errors(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    intercept(page, f'**/shows/{show["id"]}/releases/?season=*', {'ok': False, 'error': 'Prowlarr did not respond in time'})
    page.locator('button:visible[title^="Pick a release yourself (season"]').first.click()
    expect(page.get_by_text('Prowlarr did not respond in time')).to_be_visible()


def test_reset_an_episode(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    reset = page.locator('button[title^="Reset"]')
    if not reset.count():
        pytest.skip('no episode files')
    open_season_of(page, reset)
    caught = intercept(page, '**/shows/episodefiles/*/delete/')
    page.dialog_answers = [True]
    page.locator('button[title^="Reset"]:visible').first.click()
    page.wait_for_timeout(500)
    assert 'download it again' in page.dialogs_seen[0]
    assert caught.last()['json']['search'] is True


def test_remove_show_from_its_page(page, show):
    open_page(page, f'/shows/{show["id"]}/')
    caught = intercept(page, f'**/shows/{show["id"]}/delete/')
    page.dialog_answers = [True, True]
    with page.expect_navigation(url=url('/shows/')):
        more_actions(page)
        page.click('button[onclick="removeSeries()"]')
    assert caught.last()['json'] == {'delete_files': True}


def test_unknown_tmdb_link_offers_a_search(page):
    page.goto(url('/shows/tmdb/999999999/'))
    assert '/shows/search/?tmdb=999999999' in page.url


# ── Finding and adding a show ─────────────────────────────────────────────────

def test_search_sonarr_for_a_show(page):
    open_page(page, '/shows/search/')
    page.fill('#searchInput', 'the office')
    page.press('#searchInput', 'Enter')
    results = page.locator('#results [onclick="addFromCard(this)"], #results a[href^="/shows/"]')
    expect(results.first).to_be_visible(timeout=30_000)


def test_add_dialog_offers_three_ways_to_add(page):
    open_page(page, '/shows/search/?q=breaking bad')
    page.locator('#results [onclick="addFromCard(this)"]').first.click()
    modal = page.locator('[x-data="arrAddModal()"]')
    for name in ('Add to Library', 'Add & pick release', 'Add to Plex'):
        expect(modal.get_by_role('button', name=re.compile(re.escape(name)))).to_be_visible()
    expect(modal.locator('select[x-model="form.quality_profile_id"] option').first).to_be_attached()
    caught = intercept(page, '**/shows/add/', {'status': 'added', 'id': 1, 'title': 'X', 'url': '/shows/1/', 'seasons': [1]})
    modal.get_by_role('button', name=re.compile('Add to Library')).click()
    sent = caught.last()['json']
    assert sent['monitor'] == 'none' and sent['search'] is False and (sent.get('tvdb_id') or sent.get('tmdb_id'))
    expect(page.get_by_text('not monitored, nothing will download')).to_be_visible()


@pytest.mark.changes
def test_add_a_show_then_remove_it(page):
    """Real: adds an unmonitored show to Sonarr (nothing downloads), then removes it again."""
    open_page(page, '/shows/search/?q=Detectorists')
    add = page.locator('#results [onclick="addFromCard(this)"]').first
    expect(add).to_be_visible(timeout=30_000)
    if 'In library' in add.inner_text():
        pytest.skip('Detectorists is already in the library')
    add.click()
    modal = page.locator('[x-data="arrAddModal()"]')
    button = modal.get_by_role('button', name=re.compile('Add to Library'))
    expect(button).to_be_enabled()
    if modal.get_by_text('In library').count():
        pytest.skip('already in the library')
    with page.expect_response('**/shows/add/') as resp:
        button.click()
    added = resp.value.json()
    assert added['status'] == 'added', added
    try:
        expect(add).to_contain_text('In library')
        open_page(page, f'/shows/{added["id"]}/')
        expect(page.locator('h1').first).to_contain_text('Detectorists')
        # Remove it the way a person would
        page.dialog_answers = [True, False]   # remove, keep files (there are none)
        more_actions(page)
        with page.expect_navigation(url=url('/shows/')):
            page.click('button[onclick="removeSeries()"]')
        assert not page.locator(f'#posterGrid > [data-id="{added["id"]}"]').count()
    finally:
        # Whatever happened above, never leave the test show in Sonarr
        api_post(page, f'/shows/{added["id"]}/delete/', {'delete_files': False})
