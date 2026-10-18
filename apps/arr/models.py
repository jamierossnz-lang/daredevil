import os
import secrets

from django.db import models


# Container-side ports, used for the default URL when nothing is configured.
DEFAULT_PORTS = {'prowlarr': 9696, 'sonarr': 8989, 'radarr': 7878}

# How each app's container paths line up with Daredevil's own mounts in
# docker-compose.yml — used to confirm imported files on disk and to hand
# Daredevil-side paths (file browser) to Sonarr/Radarr for import.
_DEFAULT_PATH_MAPPINGS = {
    'prowlarr': '',
    'sonarr': '/tv => /media/tv\n/downloads => /media/downloads',
    'radarr': '/movies => /media/movies\n/downloads => /media/downloads',
}

# Sonarr 'auto': future episodes for shows still airing, none for ended ones
_DEFAULT_MONITOR = {'prowlarr': '', 'sonarr': 'auto', 'radarr': 'movieOnly'}


class ArrService(models.Model):
    """
    Connection + defaults for one of the *arr apps. One row per kind — always
    use ArrService.get(kind).

    Stored in the DB rather than .env so a change on the Settings page is
    seen immediately by every gunicorn worker and the Celery worker, instead
    of only by whichever process happened to handle the save.
    """

    class Kind(models.TextChoices):
        PROWLARR = 'prowlarr', 'Prowlarr'
        SONARR = 'sonarr', 'Sonarr'
        RADARR = 'radarr', 'Radarr'

    kind = models.CharField(max_length=20, choices=Kind.choices, unique=True)
    url = models.CharField(max_length=500, help_text='How Daredevil reaches it, e.g. http://sonarr:8989')
    api_key = models.CharField(max_length=100, blank=True)
    public_url = models.CharField(
        max_length=500, blank=True,
        help_text='Browser-facing address used for "Open in …" links, e.g. http://192.168.1.10:8989',
    )

    # Defaults applied when Daredevil adds a new show/movie (Sonarr/Radarr only).
    root_folder = models.CharField(max_length=500, blank=True)
    quality_profile_id = models.PositiveIntegerField(null=True, blank=True)
    monitor = models.CharField(max_length=30, blank=True,
                               help_text="Sonarr: auto/all/future/missing/…  Radarr: movieOnly/movieAndCollection/none")
    minimum_availability = models.CharField(max_length=20, default='released', help_text='Radarr only')
    series_type = models.CharField(max_length=20, default='standard', help_text='Sonarr only')
    season_folder = models.BooleanField(default=True, help_text='Sonarr only')
    search_on_add = models.BooleanField(default=True)

    path_mappings = models.TextField(
        blank=True,
        help_text='One per line: "<path inside this app> => <same folder inside Daredevil>"',
    )
    webhook_secret = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Arr service'

    def __str__(self):
        return self.get_kind_display()

    @classmethod
    def get(cls, kind):
        obj = cls.objects.filter(kind=kind).first()
        if obj is None:
            obj, _ = cls.objects.get_or_create(kind=kind, defaults=cls._env_defaults(kind))
        return obj

    @staticmethod
    def _env_defaults(kind):
        prefix = kind.upper()
        port = DEFAULT_PORTS[kind]
        # DB_HOST is only set by docker-compose — the same signal settings.py
        # uses to pick Postgres — so the compose service name is the right
        # default there and localhost is the right default bare-metal.
        host = kind if os.environ.get('DB_HOST') else 'localhost'
        quality = os.environ.get(f'{prefix}_QUALITY_PROFILE_ID', '')
        return {
            'url': os.environ.get(f'{prefix}_URL') or f'http://{host}:{port}',
            'api_key': os.environ.get(f'{prefix}_API_KEY', ''),
            'public_url': os.environ.get(f'{prefix}_PUBLIC_URL', ''),
            'root_folder': os.environ.get(f'{prefix}_ROOT_FOLDER', ''),
            'quality_profile_id': int(quality) if quality.isdigit() else None,
            'monitor': _DEFAULT_MONITOR[kind],
            'path_mappings': _DEFAULT_PATH_MAPPINGS[kind],
            'webhook_secret': secrets.token_hex(16),
        }

    @property
    def is_configured(self):
        return bool(self.url and self.api_key)

    def ensure_webhook_secret(self):
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_hex(16)
            self.save(update_fields=['webhook_secret'])
        return self.webhook_secret

    # ── Path translation ────────────────────────────────────────────────────

    def mappings(self):
        """[(path inside the app, same path inside Daredevil)], longest first."""
        pairs = []
        for line in (self.path_mappings or '').splitlines():
            sep = '=>' if '=>' in line else ('=' if '=' in line else None)
            if not sep:
                continue
            remote, local = (p.strip().rstrip('/') for p in line.split(sep, 1))
            if remote and local:
                pairs.append((remote, local))
        # Longest prefix first so /tv/anime wins over /tv
        return sorted(pairs, key=lambda p: len(p[0]), reverse=True)

    def to_local(self, remote_path):
        """A path as this app reports it → the same file as Daredevil sees it."""
        if not remote_path:
            return remote_path
        for remote, local in self.mappings():
            if remote_path == remote or remote_path.startswith(remote + '/'):
                return local + remote_path[len(remote):]
        return remote_path

    def to_remote(self, local_path):
        """A Daredevil-side path → the same file as this app sees it."""
        if not local_path:
            return local_path
        for remote, local in sorted(self.mappings(), key=lambda p: len(p[1]), reverse=True):
            if local_path == local or local_path.startswith(local + '/'):
                return remote + local_path[len(local):]
        return local_path
