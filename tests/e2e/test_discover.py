"""Finding something new — the Dashboard sliders, Streaming and the For You deck."""
import re

import pytest
from conftest import BASE, api_get, expect, intercept, open_page

# ── Dashboard ─────────────────────────────────────────────────────────────────

def test_dashboard_sliders_load_and_switch_weeks(page):
    open_page(page, '/')
    sliders = page.locator('[x-data="discoverSliders()"]')
    expect(sliders.locator('img').first).to_be_visible(timeout=30_000)
    data = api_get(page, '/discover/')
    assert any(isinstance(v, list) and v for v in data.values()), 'TMDB returned nothing for any slider'
    week = sliders.get_by_role('button', name='Last week').first
    week.click()
    expect(week).to_have_class(re.compile('bg-'))


def test_dashboard_card_opens_the_add_dialog_or_the_library(page):
    open_page(page, '/')
    sliders = page.locator('[x-data="discoverSliders()"]')
    expect(sliders.locator('img').first).to_be_visible(timeout=30_000)
    sliders.locator('[\\@click^="add(item"]').first.click()
    page.wait_for_timeout(1500)
    in_library = re.search(r'/(shows|movies)/\d+/$', page.url)
    assert in_library or page.locator('[x-data="arrAddModal()"]').is_visible()


# ── Streaming ─────────────────────────────────────────────────────────────────

def test_streaming_providers_types_and_genres(page):
    open_page(page, '/streaming/', wait_ms=1500)
    expect(page.locator('#streamGrid > *').first).to_be_visible(timeout=30_000)
    providers = page.locator('a[href^="?provider="]')
    second = providers.nth(1).get_attribute('href')
    page.goto(page.url.split('?')[0] + second)
    expect(page.locator('#streamGrid > *').first).to_be_visible(timeout=30_000)
    page.goto(page.url.split('?')[0] + second.replace('type=movie', 'type=tv'))
    expect(page.locator('#streamGrid > *').first).to_be_visible(timeout=30_000)


@pytest.mark.parametrize('sort,label', [('popular', 'Popular'), ('trending', 'Trending'), ('recent', 'Recently released')])
def test_streaming_sorts(page, sort, label):
    open_page(page, '/streaming/?provider=netflix&type=movie')
    page.get_by_role('navigation', name='Sort').get_by_role('link', name=label).click()
    page.wait_for_url(re.compile(f'sort={sort}'))
    expect(page.get_by_role('link', name=label)).to_have_attribute('aria-current', 'page')
    expect(page.locator('#streamGrid > *').first).to_be_visible(timeout=30_000)
    # Switching service, type or genre keeps the sort, and so does the next page
    for link in ('a[href^="?provider="]', 'a[href*="&type=tv"]', 'a[href*="&genre=27"], a[href*="&genre=18"]'):
        assert f'sort={sort}' in page.locator(link).first.get_attribute('href')
    with page.expect_request(re.compile(r'[?&]json=1')) as req:
        page.locator('#streamSentinel').scroll_into_view_if_needed()
    assert f'sort={sort}' in req.value.url


def test_streaming_sorts_differ(page):
    """Recently released really is newest first."""
    first = {}
    for sort in ('popular', 'recent'):
        resp = page.request.get(f'{BASE}/streaming/?provider=netflix&type=movie&sort={sort}&page=1&json=1')
        first[sort] = [i['year'] for i in resp.json()['items']]
    assert first['recent'] == sorted(first['recent'], reverse=True)
    assert first['popular'] != first['recent']


def test_streaming_infinite_scroll(page):
    open_page(page, '/streaming/', wait_ms=1500)
    first = page.locator('#streamGrid > *').count()
    page.locator('#streamSentinel').scroll_into_view_if_needed()
    page.wait_for_timeout(3000)
    assert page.locator('#streamGrid > *').count() > first or page.locator('#streamEnd').is_visible()


def test_streaming_card_info_and_add(page):
    open_page(page, '/streaming/', wait_ms=1500)
    poster = page.locator('#streamGrid [onclick="toggleStreamInfo(this)"]').first
    poster.click()
    expect(poster.locator('.stream-overview')).to_be_visible()
    add = page.locator('#streamGrid [onclick^="addMovieFromStream"], #streamGrid [onclick^="addShowFromStream"]').first
    if not add.count():
        pytest.skip('everything shown is already in the library')
    add.click()
    expect(page.locator('[x-data="arrAddModal()"]').get_by_role('button', name=re.compile('Add to Library'))).to_be_visible()


# ── For You ───────────────────────────────────────────────────────────────────

@pytest.fixture
def deck(page):
    items = api_get(page, '/foryou/data/')['items']
    if not items:
        pytest.skip('the For You deck is empty')
    return items


def test_deck_shows_why_each_pick_is_there(page, deck):
    open_page(page, '/foryou/', wait_ms=1500)
    first = deck[0]
    expect(page.get_by_text(first['title']).first).to_be_visible()
    expect(page.get_by_text(re.compile(r'\d+ picks? waiting'))).to_be_visible()
    if first['source'] == 'recommendation':
        expect(page.get_by_text('Because you like')).to_be_visible()
        if len(first['because']) > 1:
            expect(page.get_by_text(f'Matches {len(first["because"])} of yours')).to_be_visible()


def test_recommendations_lead_and_match_several_titles(page, deck):
    recs = [i for i in deck if i['source'] == 'recommendation']
    if not recs:
        pytest.skip('no recommendations in the deck')
    assert deck[0]['source'] == 'recommendation'
    counts = [len(i['because']) for i in recs]
    assert counts == sorted(counts, reverse=True), 'recommendations matching more of your titles come first'
    settings = api_get(page, '/foryou/settings/')['settings']
    assert min(counts) >= settings['min_matches']
    assert len(deck) <= settings['deck_size']


def test_pass_on_a_pick(page, deck):
    open_page(page, '/foryou/', wait_ms=1500)
    caught = intercept(page, '**/foryou/*/left/')
    page.get_by_role('button', name='Pass').click()
    page.wait_for_timeout(700)
    assert caught.last()['path'] == f'/foryou/{deck[0]["id"]}/left/'
    if len(deck) > 1:
        expect(page.get_by_text(deck[1]['title']).first).to_be_visible()


def test_like_opens_add_dialog_and_cancel_puts_the_card_back(page, deck):
    open_page(page, '/foryou/', wait_ms=1500)
    kept = intercept(page, '**/foryou/*/right/')
    page.get_by_role('button', name='Like — add it').click()
    modal = page.locator('[x-data="arrAddModal()"]')
    expect(modal.get_by_role('button', name=re.compile('Add to Library'))).to_be_visible()
    modal.get_by_title('Cancel').click()
    expect(page.get_by_text(deck[0]['title']).first).to_be_visible()
    assert not kept


def test_like_and_add_records_the_swipe_with_the_for_you_tag(page, deck):
    open_page(page, '/foryou/', wait_ms=1500)
    kept = intercept(page, '**/foryou/*/right/')
    add_kind = '**/movies/add/' if deck[0]['media_type'] == 'movie' else '**/shows/add/'
    added = intercept(page, add_kind, {'status': 'added', 'id': 1, 'title': deck[0]['title'], 'url': '/x/', 'seasons': [1]})
    page.get_by_role('button', name='Like — add it').click()
    page.locator('[x-data="arrAddModal()"]').get_by_role('button', name=re.compile('Add to Library')).click()
    page.wait_for_timeout(500)
    assert added.last()['json']['tags'] == ['for-you']
    assert kept.last()['path'] == f'/foryou/{deck[0]["id"]}/right/'


def test_drag_a_card_to_pass(page, deck):
    open_page(page, '/foryou/', wait_ms=1500)
    caught = intercept(page, '**/foryou/*/left/')
    card = page.get_by_text(deck[0]['title']).first
    box = card.bounding_box()
    x, y = box['x'] + box['width'] / 2, box['y']
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(x - step * 30, y)
    page.mouse.up()
    page.wait_for_timeout(700)
    assert caught, 'dragging the card left should pass on it'


def test_trailer(page, deck):
    open_page(page, '/foryou/', wait_ms=1500)
    page.get_by_role('button', name=re.compile('Trailer')).first.click()
    player = page.locator('[x-show="showTrailer"]')
    expect(player).to_be_visible()
    page.wait_for_timeout(2500)
    assert player.locator('iframe').count() or player.get_by_text(re.compile('No trailer', re.I)).count()
    page.get_by_role('button', name='Close trailer').click()
    expect(player).to_be_hidden()


def test_gear_opens_for_you_settings(page):
    open_page(page, '/foryou/')
    page.locator('a[href="/settings/#foryou"]').first.click()
    page.wait_for_url('**/settings/#foryou')
    expect(page.get_by_role('heading', name='Deck size')).to_be_visible()
