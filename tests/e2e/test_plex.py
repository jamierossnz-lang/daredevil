"""Plex Library — the tabs, cleaning up, and deleting (always intercepted)."""
import re

import pytest
from conftest import expect, intercept, open_page

TABS = ['recent', 'watched', 'movies', 'shows', 'missing', 'cleanup', 'delete']


def plex(page):
    open_page(page, '/plex/', wait_ms=800)
    if page.get_by_text(re.compile('Plex (is )?not (connected|configured)', re.I)).count():
        pytest.skip('Plex is not connected')


def tab(page, key):
    page.locator(f'button[\\@click="tab = \'{key}\'"]').click()
    page.wait_for_timeout(200)


@pytest.mark.parametrize('key', TABS)
def test_tabs_switch(page, key):
    plex(page)
    tab(page, key)
    expect(page.locator(f'button[\\@click="tab = \'{key}\'"]')).to_have_class(re.compile('bg-'))


def test_refresh_from_plex(page):
    plex(page)
    open_page(page, '/plex/?refresh=1', wait_ms=500)


def test_missing_title_can_be_removed_from_sonarr_or_radarr(page):
    plex(page)
    tab(page, 'missing')
    remove = page.locator('[id^="missing-"] button:visible').first
    if not remove.count():
        pytest.skip('everything in Sonarr / Radarr is in Plex')
    caught = intercept(page, re.compile(r'^.*/(movies|shows)/\d+/delete/$').pattern)
    remove.click()
    page.wait_for_timeout(500)
    assert 'Files on disk are kept' in page.dialogs_seen[-1]
    assert caught.last()['method'] == 'POST' and not caught.last()['json']   # nothing asks for files to go


def first_delete_button(page, *tabs):
    for key in tabs:
        tab(page, key)
        button = page.get_by_role('button', name=re.compile('Delete from Plex')).locator('visible=true').first
        if button.count():
            return button
    pytest.skip('nothing to delete')


def test_delete_from_plex_asks_first(page):
    plex(page)
    button = first_delete_button(page, 'cleanup', 'delete')
    caught = intercept(page, '**/plex/delete/*/', {'ok': True, 'deleted': [], 'warnings': [], 'via': 'removed it from Radarr (files deleted)'})
    page.dialog_answers = [False]
    button.click()
    page.wait_for_timeout(300)
    assert 'cannot be undone' in page.dialogs_seen[-1] and not caught
    page.dialog_answers = [True]
    button.click()
    page.wait_for_timeout(800)
    assert caught.last()['method'] == 'POST'


def test_dismiss_a_cleanup_suggestion(page):
    plex(page)
    tab(page, 'cleanup')
    button = page.get_by_role('button', name='Dismiss').locator('visible=true').first
    if not button.count():
        pytest.skip('no clean-up suggestions')
    caught = intercept(page, '**/plex/ignore/*/', {'ok': True})
    button.click()
    page.wait_for_timeout(500)
    assert re.match(r'/plex/ignore/\w+/', caught.last()['path'])


def test_pick_episodes_of_a_show_to_delete(page):
    plex(page)
    for key in ('cleanup', 'delete'):
        tab(page, key)
        picker = page.get_by_role('button', name=re.compile('Delete Episodes')).locator('visible=true').first
        if picker.count():
            break
    else:
        pytest.skip('no shows to clean up')
    with page.expect_response('**/plex/episodes/*/') as resp:
        picker.click()
    assert resp.value.ok
    episodes = page.locator('input[type=checkbox][\\:checked="selected.has(ep.rating_key)"]')
    expect(episodes.first).to_be_visible()
    episodes.first.check()
    caught = intercept(page, '**/plex/delete/*/', {'ok': True, 'deleted': ['/x'], 'warnings': []})
    page.locator('[x-show="show"] button[\\@click="deleteSelected()"]:visible').click()
    page.wait_for_timeout(800)
    assert page.dialogs_seen and caught


def test_delete_watched_items_in_bulk(page):
    plex(page)
    tab(page, 'delete')
    rows = page.locator('label[id^="watched-"]')
    if not rows.count():
        pytest.skip('nothing watched recently')
    caught = intercept(page, '**/plex/delete/*/', {'ok': True, 'deleted': ['/x'], 'warnings': []})
    page.get_by_role('button', name='Select all').locator('visible=true').click()
    delete = page.get_by_role('button', name=re.compile(r'Delete Selected \(\d+\)')).locator('visible=true')
    expect(delete).to_be_enabled()
    count = rows.count()
    delete.click()
    expect(rows).to_have_count(0)
    assert 'cannot be undone' in page.dialogs_seen[-1] and len(caught) == count
