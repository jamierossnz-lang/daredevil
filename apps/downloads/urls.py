from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path('', views.queue, name='download_queue'),
    path('status/', views.queue_status_json, name='queue_status'),
    path('status/stream/', views.queue_status_stream, name='queue_status_stream'),
    path('action/', views.queue_action, name='queue_action'),
    path('search/', views.search_again, name='queue_search_again'),
    path('history/<str:app>/<int:history_id>/failed/', views.history_failed, name='history_failed'),
    path('tab/<str:tab>/', views.queue_tab, name='queue_tab'),
    path('imports/', views.imports_page, name='moves_page'),
    # Old "File Moves" page — Sonarr/Radarr do the moving now; this shows their imports
    path('moves/', RedirectView.as_view(pattern_name='moves_page', permanent=False)),
]
