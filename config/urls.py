from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('', include('apps.media_tracker.urls')),
    path('downloads/', include('apps.downloads.urls')),
    path('qbt/', include('apps.qbt.urls')),
    path('plex/', include('apps.plex.urls')),
    path('notifications/', include('apps.notifications.urls')),
    path('activity/', include('apps.events.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
