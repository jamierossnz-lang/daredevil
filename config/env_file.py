"""
The .env file as the store for settings App Settings can change at runtime.

Saves are written here, and Plex / ntfy settings are read back from the file
on use rather than from django.conf.settings — that's per process, so a save
handled by one gunicorn worker would otherwise go unseen by the other worker
and by Celery until a restart.
"""
import logging
import os
import re
from pathlib import Path

from django.conf import settings
from dotenv import dotenv_values

log = logging.getLogger('daredevil.settings')


def _path():
    # Docker Compose mounts the project folder and points DAREDEVIL_ENV_FILE
    # at the .env inside it
    if os.environ.get('DAREDEVIL_ENV_FILE'):
        return Path(os.environ['DAREDEVIL_ENV_FILE'])
    return settings.BASE_DIR / '.env'


def read():
    path = _path()
    return dotenv_values(path) if path.exists() else {}


def setting(key, default=''):
    """The value in .env if the key is there — even blank, so clearing a field
    sticks — otherwise django.conf.settings."""
    env = read()
    if key in env:
        return env[key] or ''
    return getattr(settings, key, default)


def update(updates):
    """Write KEY=value lines into .env, appending keys it doesn't have yet."""
    path = _path()
    if not path.exists():
        return
    text = path.read_text()
    for key, value in updates.items():
        pattern = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
        line = f'{key}={value}'
        if pattern.search(text):
            text = pattern.sub(lambda _m: line, text)
        else:
            text = text.rstrip('\n') + f'\n{line}\n'
    path.write_text(text)
    log.info('.env updated: %s', ', '.join(updates))
