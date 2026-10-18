from django.conf import settings

# Compiled into the Docker image (see Dockerfile). A bare-metal checkout won't
# have it unless built by hand — base.html then falls back to the Play CDN.
_TAILWIND_CSS = (settings.BASE_DIR / 'static' / 'css' / 'tailwind.css').exists()


def tailwind(request):
    return {'TAILWIND_CSS': _TAILWIND_CSS}
