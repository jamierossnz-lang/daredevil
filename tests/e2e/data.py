"""Canned replies for intercepted requests."""
import json


def release(title, quality, size_fmt, seeders, leechers, rejections=(), guid=None, indexer_id=7):
    return {
        'guid': guid or f'e2e-{abs(hash(title))}', 'indexer_id': indexer_id, 'indexer': 'E2E Indexer', 'title': title,
        'size': 1, 'size_fmt': size_fmt, 'seeders': seeders, 'leechers': leechers, 'quality': quality,
        'age': 2, 'age_hours': 30, 'protocol': 'torrent', 'rejected': bool(rejections), 'rejections': list(rejections),
        'approved': not rejections, 'full_season': False, 'episodes': [], 'info_url': '', 'languages': ['English'],
        'custom_format_score': 0,
    }


RELEASES = {'ok': True, 'releases': [
    release('Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-E2E', 'WEBDL-1080p', '2.3 GB', 412, 31, guid='e2e-best'),
    release('Show.S01E01.2160p.WEB-DL.HDR.H.265-E2E', 'WEBDL-2160p', '6.8 GB', 97, 12,
            rejections=['Not wanted in profile: quality WEBDL-2160p is not allowed'], guid='e2e-rejected'),
    release('Show S01E01 720p WEB x264-E2E', 'WEBDL-720p', '540 MB', 55, 4, guid='e2e-small'),
]}


def search_result(title, guid, protocol='torrent', tvdb_id=0, tmdb_id=0):
    """A Prowlarr search result as /search/run/ returns it."""
    return {
        'guid': guid, 'indexer_id': 3, 'indexer': 'E2E Indexer', 'title': title, 'size': 1_500_000_000,
        'size_fmt': '1.4 GB', 'seeders': 120, 'leechers': 8, 'grabs': 40, 'age': 3, 'age_hours': 72,
        'publish_date': '2026-10-01T00:00:00Z', 'protocol': protocol, 'categories': ['TV/HD'], 'category_ids': [5040],
        'info_url': '', 'download_url': 'http://example.invalid/e2e.torrent', 'magnet_url': '',
        'imdb_id': 0, 'tmdb_id': tmdb_id, 'tvdb_id': tvdb_id, 'indexer_flags': [],
    }


def queue_item(**fields):
    """One download as the Queue page's stream sends it."""
    item = dict(key='', app='sonarr', ids=[1], title='', subtitle='', release='', poster=None, link=None,
                series_id=None, movie_id=None, episode_ids=[], phase='downloading', phase_label='Downloading',
                status='downloading', state='downloading', messages=[], size=0, size_fmt='', done_fmt='', progress=0,
                eta='—', eta_seconds=None, estimated=None, quality='', indexer='', protocol='torrent',
                download_client='qBittorrent', hash='', added=None, speed='—', speed_bps=0, seeds=None, peers=None,
                qbt_state=None, in_qbt=True)
    item.update(fields)
    return item


QUEUE = {'status': {'sonarr': True, 'radarr': True, 'qbittorrent': True}, 'items': [
    queue_item(key='sonarr:101', ids=[101, 102], title='E2E Show', subtitle='S01E01–E02 (2 episodes)', hash='e2ehash1',
               release='E2E.Show.S01.1080p.WEB-DL-E2E', size_fmt='4.1 GB', done_fmt='2.5 GB', progress=61.0,
               eta='14m 10s', speed='7.3 MB/s', seeds=88, peers=12, quality='WEBDL-1080p'),
    queue_item(key='radarr:201', app='radarr', ids=[201], title='E2E Movie', subtitle='2008', hash='e2ehash2',
               release='E2E.Movie.2008.2160p.BluRay-E2E', size_fmt='42 GB', done_fmt='42 GB', progress=100,
               phase='warning', phase_label='Import blocked', messages=['No files found are eligible for import']),
    queue_item(key='sonarr:301', ids=[301], title='E2E Delayed', subtitle='S02E03', phase='delayed',
               phase_label='Delayed — waiting for a better release', in_qbt=False),
    queue_item(key='sonarr:401', ids=[401], title='E2E Paused', subtitle='S03E01', phase='paused', phase_label='Paused',
               hash='e2ehash4'),
]}


def sse(payload):
    return 'data: ' + json.dumps(payload) + '\n\n'


TORRENTS = {'torrents': [
    dict(hash='e2ea1', name='E2E.Show.S08.1080p.BluRay-E2E', state='downloading', group='downloading', progress=62.4,
         dl=7_600_000, up=120_000, dlspeed='7.2 MB/s', upspeed='117 KB/s', size_bytes=10_500_000_000, size='9.8 GB',
         eta='14m 10s', category='sonarr', seeds=88, peers=12, ratio=0.04, added_on=1760830000, save_path='/downloads/sonarr'),
    dict(hash='e2eb2', name='E2E.Movie.2008.2160p.UHD.BluRay-E2E', state='stalledUP', group='seeding', progress=100,
         dl=0, up=0, dlspeed='—', upspeed='—', size_bytes=45_200_000_000, size='42.1 GB', eta='—', category='radarr',
         seeds=0, peers=3, ratio=1.21, added_on=1760700000, save_path='/downloads/radarr'),
    dict(hash='e2ec3', name='E2E.Other.S01E01.1080p-E2E', state='stoppedDL', group='paused', progress=12.0, dl=0, up=0,
         dlspeed='—', upspeed='—', size_bytes=1_170_000_000, size='1.1 GB', eta='—', category='', seeds=4, peers=1,
         ratio=0, added_on=1760600000, save_path='/downloads'),
    dict(hash='e2ed4', name='E2E.Stuck.S02E05.720p-E2E', state='stalledDL', group='stalled', progress=3.0, dl=0, up=0,
         dlspeed='—', upspeed='—', size_bytes=700_000_000, size='667 MB', eta='—', category='sonarr', seeds=0, peers=0,
         ratio=0, added_on=1760500000, save_path='/downloads/sonarr'),
    dict(hash='e2ee5', name='E2E.Broken.2019.1080p-E2E', state='missingFiles', group='error', progress=40.0, dl=0, up=0,
         dlspeed='—', upspeed='—', size_bytes=2_000_000_000, size='1.9 GB', eta='—', category='radarr', seeds=0, peers=0,
         ratio=0, added_on=1760400000, save_path='/downloads/radarr'),
]}
