import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-me-in-production')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',') if o.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_htmx',
    'django_celery_beat',
    'django_celery_results',
    'apps.media_tracker',
    'apps.downloads',
    'apps.qbt',
    'apps.plex',
    'apps.notifications',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.plex.context_processors.drive_usage',
                'apps.notifications.context_processors.notification_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

if os.environ.get('DB_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'daredevil'),
            'USER': os.environ.get('DB_USER', 'daredevil'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'daredevil'),
            'HOST': os.environ.get('DB_HOST', 'db'),
            'PORT': os.environ.get('DB_PORT', '5432'),
            'CONN_MAX_AGE': 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.environ.get('DB_PATH') or BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('TZ', 'Pacific/Auckland')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# TMDB API — https://www.themoviedb.org/settings/api
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')
TMDB_BASE_URL = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p'

# qBittorrent Web UI
QBITTORRENT_HOST = os.environ.get('QBITTORRENT_HOST', 'localhost')
QBITTORRENT_PORT = int(os.environ.get('QBITTORRENT_PORT', '8080'))
QBITTORRENT_USERNAME = os.environ.get('QBITTORRENT_USERNAME', 'admin')
QBITTORRENT_PASSWORD = os.environ.get('QBITTORRENT_PASSWORD', 'adminadmin')

DOWNLOAD_PATH = os.environ.get('DOWNLOAD_PATH', str(BASE_DIR / 'downloads'))

PLEX_CLAIM = os.environ.get('PLEX_CLAIM', '')
PLEX_URL = os.environ.get('PLEX_URL', 'http://localhost:32400')
PLEX_TOKEN = os.environ.get('PLEX_TOKEN', '')
PLEX_MOVIE_SECTION = os.environ.get('PLEX_MOVIE_SECTION', 'Movies')
PLEX_TV_SECTION = os.environ.get('PLEX_TV_SECTION', 'TV Shows')

# ntfy push notifications — https://ntfy.sh
NTFY_URL   = os.environ.get('NTFY_URL',   'https://ntfy.sh')
NTFY_TOPIC = os.environ.get('NTFY_TOPIC', '')   # leave blank to disable
NTFY_TOKEN = os.environ.get('NTFY_TOKEN', '')   # optional: for private topics
PUID = os.environ.get('PUID', '1000')
PGID = os.environ.get('PGID', '1000')

# Celery / Redis
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = 'django-db'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_RESULT_EXTENDED = True  # store task_name, task_args, task_kwargs in result backend
CELERY_TIMEZONE = TIME_ZONE
# Schedules are stored in the database (DatabaseScheduler) and managed via the
# Background Tasks UI.  Run `python manage.py setup_schedules` to seed defaults.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {name} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'daredevil': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}
