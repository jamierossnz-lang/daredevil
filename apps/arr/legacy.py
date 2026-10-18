"""
One-off migration of Daredevil's old local library (TVShow / Movie rows from
before Sonarr/Radarr took over) into Sonarr and Radarr.

Nothing is re-downloaded: shows/movies are added with searching turned off,
pointed at their existing folder when one is found, and Sonarr/Radarr's own
disk scan then picks up the files already there. Safe to run repeatedly —
anything already in Sonarr/Radarr is skipped.
"""
import logging
import re

from .clients import ArrError
from .models import ArrService
from .services import add_movie, add_series, favourite_tag_id, is_configured, radarr, sonarr

log = logging.getLogger('daredevil.arr.legacy')

# Daredevil's old per-show quality preference → stock Sonarr/Radarr profile names
_QUALITY_PROFILE_NAMES = {'1080p': ('HD-1080p', 'HD - 720p/1080p'), '2160p': ('Ultra-HD',)}


def _norm(name):
    name = re.sub(r'\(\d{4}\)', '', name or '')
    return re.sub(r'[^a-z0-9]+', ' ', name.lower()).strip()


def _unmapped_folders(c, root):
    for f in c.root_folders():
        if f['path'].rstrip('/') == (root or '').rstrip('/'):
            return {_norm(u.get('name')): u.get('path') for u in f.get('unmappedFolders') or []}
    return {}


def _profile_for(profiles, quality):
    for name in _QUALITY_PROFILE_NAMES.get(quality, ()):
        if name in profiles:
            return profiles[name]
    return None


def pending_counts():
    """How many legacy shows/movies aren't in Sonarr/Radarr yet (for Settings)."""
    from apps.media_tracker.models import Movie, TVShow
    from .services import library_index

    out = {'shows': None, 'movies': None}
    if is_configured('sonarr'):
        idx = library_index('sonarr')
        out['shows'] = sum(1 for t in TVShow.objects.values_list('tmdb_id', flat=True) if t not in idx)
    if is_configured('radarr'):
        idx = library_index('radarr')
        out['movies'] = sum(1 for t in Movie.objects.values_list('tmdb_id', flat=True) if t not in idx)
    return out


def import_legacy_library(dry_run=False):
    from apps.media_tracker.models import Movie, TVShow

    summary = {
        'shows_added': [], 'shows_existing': 0, 'shows_failed': [],
        'movies_added': [], 'movies_existing': 0, 'movies_failed': [],
    }

    if is_configured('sonarr') and TVShow.objects.exists():
        c = sonarr()
        svc = ArrService.get('sonarr')
        in_sonarr = {s['tmdbId'] for s in c.series_list() if s.get('tmdbId')}
        roots = c.root_folders()
        root = svc.root_folder or (roots[0]['path'] if roots else '')
        unmapped = _unmapped_folders(c, root)
        profiles = {p['name']: p['id'] for p in c.quality_profiles()}
        for show in TVShow.objects.all():
            if show.tmdb_id in in_sonarr:
                summary['shows_existing'] += 1
                continue
            if dry_run:
                summary['shows_added'].append(show.name)
                continue
            try:
                tags = [favourite_tag_id(c, create=True)] if show.is_favourite else []
                series, created = add_series(
                    tmdb_id=show.tmdb_id,
                    quality_profile_id=_profile_for(profiles, show.preferred_quality),
                    root_folder=root or None,
                    # Same meaning as the old toggle: only episodes airing
                    # from now on are wanted; nothing already aired is fetched.
                    monitor='future' if show.monitor_new_episodes else 'none',
                    search=False,
                    tags=tags,
                    path=unmapped.get(_norm(show.name)),
                )
                if created:
                    summary['shows_added'].append(series.get('title') or show.name)
                else:
                    summary['shows_existing'] += 1
            except ArrError as e:
                log.warning('legacy import: show %r failed — %s', show.name, e)
                summary['shows_failed'].append(f'{show.name}: {e}')

    if is_configured('radarr') and Movie.objects.exists():
        c = radarr()
        svc = ArrService.get('radarr')
        in_radarr = {m['tmdbId'] for m in c.movies() if m.get('tmdbId')}
        roots = c.root_folders()
        root = svc.root_folder or (roots[0]['path'] if roots else '')
        unmapped = _unmapped_folders(c, root)
        for movie in Movie.objects.all():
            if movie.tmdb_id in in_radarr:
                summary['movies_existing'] += 1
                continue
            if dry_run:
                summary['movies_added'].append(movie.title)
                continue
            # Anything the old queue still wanted (waiting for release, queued,
            # downloading) stays wanted — Radarr takes over watching for it.
            wanted = movie.download_status in ('waiting_release', 'queued', 'downloading')
            tracked = wanted or movie.download_status == 'downloaded'
            try:
                tags = [favourite_tag_id(c, create=True)] if movie.is_favourite else []
                added, created = add_movie(
                    tmdb_id=movie.tmdb_id,
                    root_folder=root or None,
                    monitor='movieOnly' if tracked else 'none',
                    search=wanted,
                    tags=tags,
                    path=unmapped.get(_norm(movie.title)),
                )
                if created:
                    summary['movies_added'].append(added.get('title') or movie.title)
                else:
                    summary['movies_existing'] += 1
            except ArrError as e:
                log.warning('legacy import: movie %r failed — %s', movie.title, e)
                summary['movies_failed'].append(f'{movie.title}: {e}')

    return summary


def describe(summary):
    parts = [
        f'{len(summary["shows_added"])} show(s) added to Sonarr',
        f'{len(summary["movies_added"])} movie(s) added to Radarr',
        f'{summary["shows_existing"] + summary["movies_existing"]} already there',
    ]
    failed = summary['shows_failed'] + summary['movies_failed']
    if failed:
        parts.append(f'{len(failed)} failed ({"; ".join(failed[:3])})')
    return ', '.join(parts)
