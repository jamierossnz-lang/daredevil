import os
from pathlib import Path
from dotenv import load_dotenv

# override=False (the default): real process environment variables — the ones
# docker-compose injects via `environment:`, which are the correct
# Docker-network-aware values (e.g. REDIS_URL=redis://redis:6379/0) — win over
# whatever's in the .env file. .env only fills in what isn't already set,
# which is what makes bare-metal setups (no compose environment injection)
# work unchanged. Previously override=True let a bind-mounted .env silently
# clobber correct container env vars with .env.example's bare-metal defaults
# (e.g. REDIS_URL=redis://localhost:6379/0), breaking Celery's broker
# connection for any service with .env mounted.
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
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.mfa',
    'rest_framework',
    'django_htmx',
    'django_celery_beat',
    'django_celery_results',
    'apps.accounts',
    'apps.events',
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
    'allauth.account.middleware.AccountMiddleware',
    'apps.accounts.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Single-user home server: log in with the superuser account (username +
# password) created via `createsuperuser` — signup is hard-disabled in
# apps/accounts/adapter.py regardless of these settings.
ACCOUNT_ADAPTER = 'apps.accounts.adapter.NoSignupAccountAdapter'
ACCOUNT_LOGIN_METHODS = {'username'}
ACCOUNT_SIGNUP_FIELDS = ['username*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_LOGOUT_ON_GET = False  # logout requires POST (CSRF-safe)
ACCOUNT_SESSION_REMEMBER = True  # always persist — see SESSION_COOKIE_AGE above

LOGIN_URL = 'account_login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# Long-lived, sliding session — this is a personal always-trusted device, not
# a shared/public terminal, so re-prompting for a password every couple of
# weeks would just be friction. Extends on every request, so it only expires
# after 90 days of no activity at all.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
SESSION_SAVE_EVERY_REQUEST = True

# Off by default so LAN-only HTTP access (e.g. http://192.168.1.x:8000) still
# works out of the box. Flip both to true once HTTPS is actually terminated
# in front of the app (reverse proxy / tunnel) — sending the session/CSRF
# cookie over plain HTTP once it's exposed beyond the LAN defeats having a
# login at all.
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False') == 'True'
CSRF_COOKIE_SECURE = os.environ.get('CSRF_COOKIE_SECURE', 'False') == 'True'

# Google sign-in — only actually offered once real credentials exist. Signup
# via Google is blocked by the same adapter as regular signup (allauth's
# social adapter delegates to it by default) — Google can only ever log in
# as the existing superuser, never create a new account. To use it: sign in
# normally once, then visit /accounts/3rdparty/ to connect your Google
# account to this login.
_google_client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
_google_client_secret = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '')
SOCIALACCOUNT_PROVIDERS = {}
if _google_client_id and _google_client_secret:
    SOCIALACCOUNT_PROVIDERS['google'] = {
        'APPS': [{'client_id': _google_client_id, 'secret': _google_client_secret, 'key': ''}],
        'SCOPE': ['profile', 'email'],
    }

# Passkeys (WebAuthn / Face ID / Touch ID / Windows Hello) as an alternative
# to password login, not a second factor on top of it. Needs HTTPS (or
# localhost) — browsers refuse the WebAuthn API otherwise. Register one at
# /accounts/2fa/webauthn/add/ while logged in with your password first.
MFA_SUPPORTED_TYPES = ['webauthn']
MFA_PASSKEY_LOGIN_ENABLED = True

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
                'apps.accounts.context_processors.google_login_enabled',
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
