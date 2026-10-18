from django.conf import settings
from django.contrib.auth.views import redirect_to_login

# Daredevil has no other access control — anyone who can reach it over the
# network otherwise has full unauthenticated control (queue/delete media,
# touch qBittorrent/Prowlarr/Sonarr/Radarr settings, etc.), which matters once
# it's exposed beyond the LAN. Everything requires a login except: allauth's
# own login/logout/password-reset views, Django admin (has its own auth),
# static files (defensive — WhiteNoise already serves these before this
# middleware runs in the normal case), the Sonarr/Radarr webhook receiver,
# which authenticates with its own per-app secret in the URL, and the
# calendar feed, likewise (calendar apps can't log in).
EXEMPT_PREFIXES = ('/accounts/', '/admin/', '/static/', '/arr/webhook/', '/calendar/feed/')


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)
        return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)
