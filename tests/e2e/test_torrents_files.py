"""qBittorrent — the torrent manager — and the File Browser."""
import re

import pytest
from conftest import expect, intercept, open_page, toast, url

from data import TORRENTS


def manager(page):
    """The torrent manager fed a known list of torrents."""
    intercept(page, '**/qbt/torrents.json', TORRENTS)
    open_page(page, '/qbt/')
    expect(page.get_by_text('E2E.Show.S08.1080p.BluRay-E2E')).to_be_visible()


def names(page):
    return [n.strip() for n in page.locator('[x-for="t in visible()"] ~ * [x-text="t.name"], [x-text="t.name"]:visible').all_inner_texts()]


def test_real_torrent_list_and_speeds(page):
    resp = page.request.get(url('/qbt/torrents.json'))
    assert resp.status in (200, 503)
    if resp.status == 200:
        assert isinstance(resp.json()['torrents'], list)
    assert page.request.get(url('/qbt/transfer.json')).status in (200, 503)


def test_filter_by_state_name_and_category(page):
    manager(page)
    assert len(names(page)) == len(TORRENTS['torrents'])
    page.get_by_role('button', name=re.compile('Seeding')).click()
    assert names(page) == ['E2E.Movie.2008.2160p.UHD.BluRay-E2E']
    page.get_by_role('button', name=re.compile('^\\W*All')).first.click()
    page.get_by_placeholder('Filter by name…').fill('other')
    assert names(page) == ['E2E.Other.S01E01.1080p-E2E']
    page.get_by_placeholder('Filter by name…').fill('')
    page.locator('select[x-model="category"]').select_option('sonarr')
    assert sorted(names(page)) == sorted(x['name'] for x in TORRENTS['torrents'] if x['category'] == 'sonarr')


def test_sort(page):
    manager(page)
    page.locator('select[x-model="sort"]:visible').select_option('size')
    assert names(page)[0] == 'E2E.Movie.2008.2160p.UHD.BluRay-E2E'
    page.locator('select[x-model="sort"]:visible').select_option('name')
    assert names(page) == sorted(names(page), key=str.lower)


@pytest.mark.parametrize('title,action', [('Start', 'start'), ('Pause', 'stop'),
                                          ('Ask trackers for more peers', 'reannounce'), ('Re-check the downloaded data', 'recheck')])
def test_row_actions(page, title, action):
    manager(page)
    caught = intercept(page, '**/qbt/torrents/action/')
    page.get_by_title(title, exact=True).locator('visible=true').first.click()
    page.wait_for_timeout(300)
    sent = caught.last()['json']
    assert sent['action'] == action and len(sent['hashes']) == 1 and sent['hashes'][0].startswith('e2e')


def test_select_several_then_act_on_them(page):
    manager(page)
    caught = intercept(page, '**/qbt/torrents/action/')
    boxes = page.locator('input[type=checkbox][x-model="selected"]:visible')
    boxes.nth(0).check()
    boxes.nth(1).check()
    page.locator('button:visible', has_text=re.compile('^\\s*Start')).first.click()
    page.wait_for_timeout(300)
    assert caught.last()['json'] == {'action': 'start', 'hashes': [boxes.nth(0).get_attribute('value'), boxes.nth(1).get_attribute('value')]}


def test_remove_with_files(page):
    manager(page)
    caught = intercept(page, '**/qbt/torrents/action/')
    page.get_by_title('Remove', exact=True).locator('visible=true').first.click()
    dialog = page.locator('[x-show="removing"]')
    expect(dialog.get_by_text('Remove torrent?')).to_be_visible()
    dialog.get_by_label(re.compile('Also delete the downloaded files')).check()
    dialog.get_by_role('button', name=re.compile('Remove')).click()
    expect(toast(page, 'and their files')).to_be_visible()
    sent = caught.last()['json']
    assert sent['action'] == 'delete' and sent['delete_files'] is True and len(sent['hashes']) == 1


def test_remove_cancelled(page):
    manager(page)
    caught = intercept(page, '**/qbt/torrents/action/')
    page.get_by_title('Remove', exact=True).locator('visible=true').first.click()
    page.locator('[x-show="removing"]').get_by_role('button', name='Cancel').click()
    assert not caught


# ── File Browser ──────────────────────────────────────────────────────────────

def file_browser(page):
    open_page(page, '/qbt/files/')
    tabs = page.locator('button[\\@click^="openRoot("]')
    if not tabs.count():
        pytest.skip('no File Browser folders (no path mappings)')
    return tabs


def test_every_folder_tab_lists(page):
    tabs = file_browser(page)
    for i in range(tabs.count()):
        with page.expect_response('**/qbt/files/list/?*') as resp:
            tabs.nth(i).click()
        assert resp.value.status in (200, 404), resp.value.text()   # 404: a folder that isn't mounted here
    expect(page.get_by_title('Go up')).to_be_disabled()


def test_browse_into_a_folder_and_back(page):
    tabs = file_browser(page)
    tabs.first.click()
    folder = page.locator('[x-text="entry.name"]').first
    try:
        folder.wait_for(timeout=5000)
    except Exception:
        pytest.skip('the first folder is empty')
    page.wait_for_timeout(300)
    entries = page.evaluate('() => Alpine.$data(document.querySelector("[x-data^=\'fileBrowser\']")).entries')
    first_dir = next((e for e in entries if e['type'] == 'dir'), None)
    if not first_dir:
        pytest.skip('no sub-folders')
    page.locator('[x-text="entry.name"]', has_text=first_dir['name']).first.click()
    expect(page.get_by_title('Go up')).to_be_enabled()
    page.get_by_title('Go up').click()
    expect(page.get_by_title('Go up')).to_be_disabled()


def test_delete_and_import_send_the_selected_paths(page):
    tabs = file_browser(page)
    tabs.first.click()
    boxes = page.locator('input[type=checkbox][\\:checked^="selected.has"]:visible')
    try:
        boxes.first.wait_for(timeout=5000)
    except Exception:
        pytest.skip('the first folder is empty')
    boxes.first.check()
    path = page.evaluate('() => [...Alpine.$data(document.querySelector("[x-data^=\'fileBrowser\']")).selected][0]')

    deleted = intercept(page, '**/qbt/files/delete/', {'ok': True})
    page.get_by_role('button', name='Delete', exact=True).click()
    page.wait_for_timeout(500)
    assert 'cannot be undone' in page.dialogs_seen[-1] and deleted.last()['form'] == {'path': path}

    boxes.first.check()
    imported = intercept(page, '**/qbt/files/import/', {'results': [{'path': path, 'app': 'sonarr', 'remote_path': path}]})
    page.get_by_role('button', name='Import', exact=True).click()
    page.locator('[x-show="showMoveModal"]').get_by_role('button', name=re.compile('TV')).first.click()
    with page.expect_navigation(url=url('/downloads/imports/')):
        page.locator('button[\\@click="moveSelected()"]').click()
    sent = imported.last()['form']
    assert sent['paths'] == path and sent['media_type'] == 'tv' and sent['import_mode'] in ('Auto', 'Move', 'Copy')


def test_add_a_folder_tab(page):
    file_browser(page)
    caught = intercept(page, '**/qbt/files/tabs/add/', {'ok': True, 'pk': 1})
    page.locator('button[\\@click="showAddTab = true"]').click()
    page.get_by_placeholder('e.g. Media Drive').fill('E2E Folder')
    page.get_by_placeholder('/media/downloads').fill('/media/downloads')
    with page.expect_navigation():
        page.locator('button[\\@click="addTab()"]').click()
    assert caught.last()['form'] == {'label': 'E2E Folder', 'path': '/media/downloads'}


def test_top_level_folders_and_outside_paths_are_protected(page):
    tabs = file_browser(page)
    root = page.evaluate('() => Alpine.$data(document.querySelector("[x-data^=\'fileBrowser\']")).tabs?.[0]?.path') or None
    tabs.first.click()
    page.wait_for_timeout(800)
    root = root or page.evaluate('() => Alpine.$data(document.querySelector("[x-data^=\'fileBrowser\']")).currentPath')
    token = next(c['value'] for c in page.context.cookies() if c['name'] == 'csrftoken')
    for path, why in ((root, 'a whole folder tab'), ('/etc/passwd', 'outside every tab'), ('/app/manage.py', 'Daredevil itself')):
        resp = page.request.post(url('/qbt/files/delete/'), form={'path': path}, headers={'X-CSRFToken': token, 'Referer': url('/')})
        assert resp.status == 403, f'deleting {path} ({why}) → {resp.status}'
    resp = page.request.get(url('/qbt/files/list/?path=/etc'))
    assert resp.status == 403
