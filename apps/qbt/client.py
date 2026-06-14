import logging
import qbittorrentapi
from django.conf import settings

log = logging.getLogger('daredevil.qbt')


def get_client():
    client = qbittorrentapi.Client(
        host=settings.QBITTORRENT_HOST,
        port=settings.QBITTORRENT_PORT,
        username=settings.QBITTORRENT_USERNAME,
        password=settings.QBITTORRENT_PASSWORD,
        REQUESTS_ARGS={'timeout': (5, 30)},
        VERIFY_WEBUI_CERTIFICATE=False,
    )
    client.auth_log_in()
    return client


def is_connected():
    try:
        c = get_client()
        c.app_version()
        return True
    except Exception:
        return False


def get_torrents(filter_status=None):
    try:
        c = get_client()
        kwargs = {}
        if filter_status:
            kwargs['filter'] = filter_status
        return list(c.torrents_info(**kwargs))
    except Exception as e:
        log.warning('get_torrents: failed — %s', e)
        return []


def get_torrent(torrent_hash):
    try:
        c = get_client()
        results = c.torrents_info(hashes=torrent_hash)
        return results[0] if results else None
    except Exception:
        return None


def add_magnet(magnet_link, save_path=None, category=None):
    c = get_client()
    kwargs = {'urls': magnet_link}
    if save_path:
        kwargs['save_path'] = save_path
    if category:
        kwargs['category'] = category
    c.torrents_add(**kwargs)


def pause_torrent(torrent_hash):
    c = get_client()
    c.torrents_pause(hashes=torrent_hash)


def resume_torrent(torrent_hash):
    c = get_client()
    c.torrents_resume(hashes=torrent_hash)


def delete_torrent(torrent_hash, delete_files=False):
    c = get_client()
    c.torrents_delete(hashes=torrent_hash, delete_files=delete_files)


def get_transfer_info():
    try:
        c = get_client()
        return c.transfer_info()
    except Exception:
        return {}


def get_preferences():
    try:
        c = get_client()
        return dict(c.app_preferences())
    except Exception:
        return {}


def set_preferences(prefs: dict):
    c = get_client()
    c.app_set_preferences(prefs)


def get_categories():
    try:
        c = get_client()
        return dict(c.torrents_categories())
    except Exception:
        return {}


def create_category(name, save_path=''):
    c = get_client()
    c.torrents_create_category(name=name, save_path=save_path)


def edit_category(name, save_path):
    c = get_client()
    c.torrents_edit_category(name=name, save_path=save_path)


def delete_category(name):
    c = get_client()
    c.torrents_remove_categories(categories=[name])


def set_torrent_location(torrent_hash, save_path):
    """Tell qBittorrent to move the torrent's data to a new path (qBT does the file move)."""
    c = get_client()
    c.torrents_set_location(location=save_path, hashes=torrent_hash)


def rename_torrent(torrent_hash, new_name):
    """Rename the torrent (renames root folder for multi-file torrents)."""
    c = get_client()
    c.torrents_rename(torrent_hash=torrent_hash, new_torrent_name=new_name)


def search_torrents(query, category='all', plugins='all'):
    """
    Use qBittorrent's built-in search API.
    Requires at least one search plugin installed in qBittorrent.
    """
    import time
    log.info('search_torrents: starting search for %r', query)
    c = get_client()

    try:
        job = c.search_start(pattern=query, plugins=plugins, category=category)
    except Exception as e:
        log.error('search_torrents: search_start failed — %s', e)
        raise

    job_id = job.id
    log.debug('search_torrents: job_id=%s', job_id)

    # Thresholds for early exit — stop waiting as soon as we have a good-enough set.
    _EARLY_SEEDED_COUNT = 5   # stop if this many results have enough seeds
    _EARLY_MIN_SEEDS    = 3   # "enough seeds" for the above count
    _EARLY_TOP_SEEDS    = 20  # stop immediately if any single result has this many seeds

    # Poll until qBT marks the job Stopped or we have enough good results.
    # Check actual results on each tick so we can exit early.
    delays = [1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]  # 49 s max
    raw = []
    for i, delay in enumerate(delays):
        time.sleep(delay)
        try:
            status = c.search_status(search_id=job_id)
            job_status = status[0].status if status else 'unknown'
            total_so_far = getattr(status[0], 'total', 0) if status else 0
            log.debug('search_torrents: poll #%d status=%s total=%d', i + 1, job_status, total_so_far)

            if not status or job_status == 'Stopped':
                log.info('search_torrents: job stopped after %d poll(s)', i + 1)
                break

            # After the first couple of seconds, peek at results and exit early if good enough
            if i >= 1 and total_so_far > 0:
                try:
                    peek = c.search_results(search_id=job_id, limit=200)
                    peek_list = list(peek.results)
                    seeded = [r for r in peek_list if (r.get('nbSeeders') or 0) >= _EARLY_MIN_SEEDS]
                    top_seeds = max((r.get('nbSeeders') or 0 for r in peek_list), default=0)
                    log.debug('search_torrents: poll #%d peek=%d results, %d seeded, top=%d seeds',
                              i + 1, len(peek_list), len(seeded), top_seeds)
                    if len(seeded) >= _EARLY_SEEDED_COUNT or top_seeds >= _EARLY_TOP_SEEDS:
                        raw = peek_list
                        log.info('search_torrents: early exit at poll #%d — %d seeded, top=%d seeds',
                                 i + 1, len(seeded), top_seeds)
                        break
                except Exception:
                    pass  # peek failed, keep waiting

        except Exception as e:
            log.warning('search_torrents: status poll #%d failed (%s) — assuming done', i + 1, e)
            break

    # If early-exit already populated raw, skip the final fetch
    if not raw:
        for attempt in range(3):
            try:
                results = c.search_results(search_id=job_id, limit=200)
                raw = list(results.results)
                log.info('search_torrents: got %d results (attempt %d)', len(raw), attempt + 1)
                break
            except Exception as e:
                log.warning('search_torrents: search_results attempt %d failed — %s', attempt + 1, e)
                if attempt < 2:
                    time.sleep(1)

    try:
        c.search_delete(search_id=job_id)
    except Exception:
        pass

    return raw


def get_app_version():
    try:
        c = get_client()
        return c.app_version()
    except Exception:
        return 'Not connected'


def get_torrents_stats():
    torrents = get_torrents()
    stats = {
        'total': len(torrents),
        'downloading': 0,
        'seeding': 0,
        'paused': 0,
        'completed': 0,
    }
    for t in torrents:
        state = t.state
        if 'download' in state:
            stats['downloading'] += 1
        elif 'upload' in state or state == 'stalledUP':
            stats['seeding'] += 1
        elif 'paused' in state:
            stats['paused'] += 1
        if t.progress >= 1.0:
            stats['completed'] += 1
    return stats
