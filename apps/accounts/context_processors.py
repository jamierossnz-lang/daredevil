from django.conf import settings


def google_login_enabled(request):
    """SOCIALACCOUNT_ENABLED (allauth's own flag) only means the app is
    installed, not that a provider is actually configured — rendering
    {% provider_login_url 'google' %} with no SocialApp configured raises
    SocialApp.DoesNotExist. This is the flag the login template actually
    needs: is there a real, usable Google app right now."""
    return {'GOOGLE_LOGIN_ENABLED': bool(settings.SOCIALACCOUNT_PROVIDERS.get('google'))}
