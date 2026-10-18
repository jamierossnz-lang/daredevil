"""Search — Prowlarr's indexers, and handing a result to Sonarr / Radarr."""
import re

from conftest import expect, intercept, open_page

from data import search_result

RESULTS = {'ok': True, 'count': 3, 'offset': 0, 'limit': 100, 'results': [
    search_result('Show.Name.S01E01.1080p.WEB.h264-E2E', 'e2e-r1', tvdb_id=81189),
    search_result('Show.Name.S01E02.720p.HDTV.x264-E2E', 'e2e-r2', tvdb_id=81189),
    search_result('Unknown.Show.S03E04.1080p.WEB-E2E', 'e2e-r3'),
]}


def search_page(page, mocked=True):
    open_page(page, '/search/')
    if mocked:
        return intercept(page, '**/search/run/?*', RESULTS)
    return None


def as_tv(page):
    """With both Sonarr and Radarr connected, the user is asked which one it's for."""
    chooser = page.locator('[x-show="chooser"]')
    try:
        chooser.wait_for(state='visible', timeout=3000)
    except Exception:
        return
    chooser.get_by_role('button', name=re.compile('TV show')).click()


def run_search(page, query='show name'):
    page.locator('input[x-ref="q"]').fill(query)
    page.get_by_role('button', name='Search', exact=True).click()


def test_real_search_through_prowlarr(page):
    """Real: one search across your indexers."""
    open_page(page, '/search/')
    with page.expect_response('**/search/run/?*', timeout=90_000) as resp:
        run_search(page, 'the office')
    data = resp.value.json()
    assert data['ok'], data.get('error')
    if data['results']:
        expect(page.get_by_text(data['results'][0]['title']).first).to_be_attached()


def test_search_type_tokens_indexers_and_categories(page):
    caught = search_page(page)
    page.get_by_role('button', name=re.compile('Movies')).first.click()
    page.get_by_role('button', name='{Year:2000}').click()
    expect(page.locator('input[x-ref="q"]')).to_have_value(re.compile(r'\{Year:2000\}'))
    page.locator('input[x-ref="q"]').fill('dune {Year:2021}')
    page.get_by_role('button', name='Search', exact=True).click()
    page.wait_for_timeout(500)
    sent = caught.last()['url']
    # No category picked → none sent; the server searches the whole Movies tree
    assert 'type=movie' in sent and 'q=dune' in sent and 'cat=' not in sent


def test_results_filter_select_and_download_as_tv(page):
    search_page(page)
    run_search(page)
    expect(page.get_by_text('Show.Name.S01E01.1080p.WEB.h264-E2E').first).to_be_visible()
    grab = intercept(page, '**/search/grab/', {'ok': True, 'outcomes': [
        {'title': r['title'], 'guid': r['guid'], 'approved': True, 'rejections': [], 'unknown': False, 'can_force': False}
        for r in RESULTS['results'][:2]]})
    page.get_by_role('button', name='Select all').first.click()
    page.get_by_label('Select Unknown.Show.S03E04.1080p.WEB-E2E').first.uncheck()
    page.locator('button:visible', has_text=re.compile('Download')).first.click()
    as_tv(page)
    expect(page.get_by_text('Downloading via Sonarr')).to_be_visible()
    sent = grab.last()['json']
    assert sent['action'] == 'sonarr' and [r['guid'] for r in sent['releases']] == ['e2e-r1', 'e2e-r2']


def test_turned_down_release_can_be_downloaded_anyway(page):
    search_page(page)
    run_search(page)
    grab = intercept(page, '**/search/grab/', {'ok': False, 'outcomes': [{
        'title': RESULTS['results'][0]['title'], 'guid': 'e2e-r1', 'approved': False,
        'rejections': ['Existing file on disk is of equal or higher preference'], 'unknown': False, 'can_force': True}]})
    page.locator('button[title^="Download — Sonarr"]:visible').first.click()
    as_tv(page)
    expect(page.get_by_text('Existing file on disk is of equal or higher preference')).to_be_visible()
    page.get_by_role('button', name=re.compile('Download anyway')).click()
    page.wait_for_timeout(500)
    assert grab.last()['json']['action'] == 'force_sonarr'


def test_unknown_show_is_added_then_downloaded(page):
    search_page(page)
    run_search(page)
    match = {'kind': 'sonarr', 'title': 'Unknown Show', 'year': 2024, 'tvdb_id': 4242, 'tmdb_id': None,
             'status': 'continuing', 'poster': None, 'in_library': False, 'id': None, 'seasons': [1, 2, 3]}
    grab = intercept(page, '**/search/grab/', {'ok': False, 'outcomes': [{
        'title': RESULTS['results'][2]['title'], 'guid': 'e2e-r3', 'approved': False,
        'rejections': ['Unknown Series'], 'unknown': True, 'can_force': False, 'match': match}]})
    page.locator('button[title^="Download — Sonarr"]:visible').nth(2).click()
    as_tv(page)
    expect(page.get_by_text('Not in your library yet — it looks like Unknown Show (2024).')).to_be_visible()
    intercept(page, '**/arr/sonarr/lookup/?*', {'ok': True, **match})
    added = intercept(page, '**/shows/add/', {'status': 'added', 'id': 99, 'title': 'Unknown Show', 'url': '/shows/99/', 'seasons': [1]})
    page.get_by_role('button', name=re.compile('Add to Sonarr & download')).click()
    modal = page.locator('[x-data="arrAddModal()"]')
    expect(modal.get_by_text('Unknown.Show.S03E04.1080p.WEB-E2E')).to_be_visible()
    modal.get_by_role('button', name=re.compile('Add & download this release')).click()
    page.wait_for_timeout(800)
    assert added.last()['json']['tvdb_id'] == 4242
    last = grab.last()['json']
    assert last['series_id'] == 99 and last['force_if_rejected'] is True
