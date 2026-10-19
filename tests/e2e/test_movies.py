"""Movies — the library, a movie's page, searching and adding (Radarr)."""
import re

import pytest
from conftest import api_get, api_post, expect, intercept, open_page, toast, url

from data import RELEASES


def library(page):
    open_page(page, '/movies/')
    return page.eval_on_selector_all('#posterGrid > [data-id]', '''cards => cards.map(c => ({
        id: Number(c.dataset.id), name: c.dataset.name, state: c.dataset.state, favourite: c.dataset.favourite === '1'}))''')


@pytest.fixture(scope='module')
def movies(browser, signed_in):
    ctx = browser.new_context(storage_state=signed_in)
    found = library(ctx.new_page())
    ctx.close()
    if not found:
        pytest.skip('Radarr has no movies')
    return found


@pytest.fixture(scope='module')
def movie(movies):
    """A downloaded movie if there is one — it has a file to test Reset with."""
    return next((m for m in movies if m['state'] == 'downloaded'), movies[0])


def card_menu(page, movie_id):
    c = page.locator(f'#posterGrid > [data-id="{movie_id}"]')
    c.hover()
    c.get_by_label('More actions').click()
    return c


def more_actions(page):
    """Open the movie page's "…" menu (Reset, Refresh, Radarr, Remove)."""
    page.get_by_role('button', name='More actions').click()


def visible_ids(page):
    return set(page.eval_on_selector_all('#posterGrid > [data-id]', 'cs => cs.filter(c => c.offsetParent).map(c => Number(c.dataset.id))'))


# ── Library ───────────────────────────────────────────────────────────────────

def test_search_box_filters_the_library(page, movies):
    open_page(page, '/movies/')
    target = movies[len(movies) // 2]
    page.fill('#filterInput', target['name'][:5])
    assert target['id'] in visible_ids(page)
    expect(page.locator('#addMovieBtn')).to_have_attribute('href', re.compile(r'/movies/search/\?q='))


@pytest.mark.parametrize('state', ['downloaded', 'missing', 'waiting', 'unmonitored', 'favourite'])
def test_status_filter(page, movies, state):
    open_page(page, '/movies/')
    if not page.locator(f'#filterStatus option[value="{state}"]').count():
        pytest.skip(f'no "{state}" filter')
    page.select_option('#filterStatus', state)
    key = 'favourite' if state == 'favourite' else 'state'
    expected = {m['id'] for m in movies if (m['favourite'] if key == 'favourite' else m['state'] == state)}
    assert visible_ids(page) == expected


def test_card_menu_search_and_remove(page, movie):
    open_page(page, '/movies/')
    search = intercept(page, f'**/movies/{movie["id"]}/download/', {'ok': True, 'message': 'Radarr is searching'})
    card_menu(page, movie['id']).get_by_role('button', name=re.compile('Search')).click()
    expect(toast(page, 'Radarr is searching')).to_be_visible()
    assert search.last()['method'] == 'POST'

    open_page(page, '/movies/')
    remove = intercept(page, f'**/movies/{movie["id"]}/delete/')
    page.dialog_answers = [True, True]
    card_menu(page, movie['id']).get_by_role('button', name=re.compile('Remove')).click()
    page.wait_for_timeout(500)
    assert remove.last()['json'] == {'delete_files': True}


@pytest.mark.changes
def test_favourite_and_unfavourite(page, movie):
    """Real: a favourite tag in Radarr — then put back."""
    before = movie['favourite']
    for expected in (not before, before):
        open_page(page, f'/movies/{movie["id"]}/')
        with page.expect_navigation():
            page.get_by_role('button', name=re.compile('Favourite')).click()
        assert next(m for m in library(page) if m['id'] == movie['id'])['favourite'] == expected


# ── A movie's page ────────────────────────────────────────────────────────────

def test_movie_page(page, movie):
    open_page(page, f'/movies/{movie["id"]}/')
    expect(page.locator('#qualityProfile')).to_be_visible()
    expect(page.locator('#availability')).to_be_visible()
    status = api_get(page, f'/movies/{movie["id"]}/status/')
    assert status['ok'] and 'has_file' in status


def test_search_now(page, movie):
    open_page(page, f'/movies/{movie["id"]}/')
    caught = intercept(page, f'**/movies/{movie["id"]}/download/', {'ok': True, 'message': 'Radarr is searching'})
    page.get_by_role('button', name='Search now').click()
    expect(toast(page, 'Radarr is searching')).to_be_visible()
    assert caught.last()['method'] == 'POST'


def test_change_quality_and_availability(page, movie):
    open_page(page, f'/movies/{movie["id"]}/')
    caught = intercept(page, f'**/movies/{movie["id"]}/edit/')
    profiles = page.eval_on_selector_all('#qualityProfile option', 'os => os.map(o => o.value)')
    page.select_option('#qualityProfile', profiles[-1])
    expect(toast(page, 'Quality profile updated')).to_be_visible()
    assert caught.last()['json'] == {'quality_profile_id': profiles[-1]}
    page.select_option('#availability', 'announced')
    page.wait_for_timeout(300)
    assert caught.last()['json'] == {'minimum_availability': 'announced'}


def test_pick_a_release(page, movie):
    open_page(page, f'/movies/{movie["id"]}/')
    intercept(page, f'**/movies/{movie["id"]}/releases/', RELEASES)
    grab = intercept(page, '**/movies/releases/grab/')
    page.get_by_role('button', name='Pick release').click()
    modal = page.locator('[x-data="arrReleaseModal()"]')
    expect(modal.locator('p[x-text="r.title"]')).to_have_count(3)
    modal.get_by_role('button', name='Grab anyway').click()
    expect(toast(page, 'Grabbed — sent to qBittorrent by Radarr')).to_be_visible()
    assert grab.last()['json'] == {'guid': 'e2e-rejected', 'indexer_id': 7}


def test_reset_and_redownload(page, movies):
    target = next((m for m in movies if m['state'] == 'downloaded'), None)
    if not target:
        pytest.skip('no downloaded movie')
    open_page(page, f'/movies/{target["id"]}/')
    caught = intercept(page, '**/movies/moviefiles/*/delete/')
    more_actions(page)
    page.get_by_role('menuitem', name=re.compile('Reset')).click()
    page.wait_for_timeout(500)
    assert caught.last()['json'] == {'movie_id': target['id'], 'search': True}
    assert not caught.last()['path'].startswith('/movies/moviefiles/0/')


def test_refresh_and_remove(page, movie):
    open_page(page, f'/movies/{movie["id"]}/')
    more_actions(page)
    page.get_by_role('menuitem', name=re.compile('Refresh')).click()   # real, harmless
    expect(toast(page, 'Radarr is refreshing')).to_be_visible()
    remove = intercept(page, f'**/movies/{movie["id"]}/delete/')
    page.dialog_answers = [True, False]
    more_actions(page)
    with page.expect_navigation(url=url('/movies/')):
        page.get_by_role('menuitem', name='Remove').click()
    assert remove.last()['json'] == {'delete_files': False}


def test_similar_movies_can_be_added(page, movie):
    open_page(page, f'/movies/{movie["id"]}/')
    add = page.locator('[onclick="addFromCard(this)"]')
    if not add.count():
        pytest.skip('no recommendations for this movie')
    add.first.scroll_into_view_if_needed()
    add.first.click()
    expect(page.locator('[x-data="arrAddModal()"]').get_by_role('button', name=re.compile('Add to Library'))).to_be_visible()


# ── Finding and adding a movie ────────────────────────────────────────────────

def test_search_radarr_for_a_movie(page):
    open_page(page, '/movies/search/')
    page.fill('#searchInput', 'dune')
    page.get_by_role('button', name='Search').click()
    expect(page.locator('#results [onclick="addFromCard(this)"], #results a[href^="/movies/"]').first).to_be_visible(timeout=30_000)


def test_add_dialog_for_a_movie(page):
    open_page(page, '/movies/search/?q=the matrix')
    page.locator('#results [onclick="addFromCard(this)"]').first.click()
    modal = page.locator('[x-data="arrAddModal()"]')
    expect(modal.locator('select[x-model="form.minimum_availability"]')).to_be_visible()
    caught = intercept(page, '**/movies/add/', {'status': 'added', 'id': 1, 'title': 'X', 'url': '/movies/1/'})
    modal.locator('select[x-model="form.monitor"]').select_option('movieOnly')
    modal.get_by_role('button', name=re.compile('Add to Plex')).click()
    sent = caught.last()['json']
    assert sent['search'] is True and sent['monitor'] == 'movieOnly' and sent['tmdb_id']


@pytest.mark.changes
def test_add_a_movie_then_remove_it(page):
    """Real: adds an unmonitored movie to Radarr (nothing downloads), then removes it again."""
    open_page(page, '/movies/search/?q=Plan 9 from Outer Space')
    add = page.locator('#results [onclick="addFromCard(this)"]').first
    expect(add).to_be_visible(timeout=30_000)
    add.click()
    modal = page.locator('[x-data="arrAddModal()"]')
    button = modal.get_by_role('button', name=re.compile('Add to Library'))
    expect(button).to_be_enabled()
    if modal.get_by_text('In library').count() or 'In library' in add.inner_text():
        pytest.skip('already in the library')
    with page.expect_response('**/movies/add/') as resp:
        button.click()
    added = resp.value.json()
    assert added['status'] == 'added', added
    try:
        open_page(page, f'/movies/{added["id"]}/')
        expect(page.locator('h1').first).to_contain_text('Plan 9')
    finally:
        status, body = api_post(page, f'/movies/{added["id"]}/delete/', {'delete_files': False})
        assert status == 200 and body['ok'], body
    assert added['id'] not in {m['id'] for m in library(page)}
