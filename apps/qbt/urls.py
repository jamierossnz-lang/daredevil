from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path('', views.dashboard, name='qbt_dashboard'),
    path('torrents.json', views.torrents_json, name='torrents_json'),
    path('transfer.json', views.transfer_info_json, name='transfer_info'),
    path('torrents/action/', views.torrents_action, name='torrents_action'),
    path('add-magnet/', views.torrent_add_magnet, name='torrent_add_magnet'),
    # Search moved to Prowlarr (/search/)
    path('search/', RedirectView.as_view(pattern_name='search', query_string=True, permanent=False), name='qbt_search'),
    # Settings and categories moved to App Settings → qBittorrent
    path('settings/', views.settings_view, name='qbt_settings'),
    path('settings/data/', views.settings_data, name='qbt_settings_data'),
    path('settings/save/', views.settings_save, name='qbt_settings_save'),
    path('settings/connection/', views.connection_save, name='qbt_connection_save'),
    path('categories/', views.categories_page, name='qbt_categories'),
    path('categories/save/', views.category_save, name='qbt_category_save'),
    path('categories/remove/', views.category_remove, name='qbt_category_remove'),
    path('files/', views.file_browser_page, name='qbt_files'),
    path('files/list/', views.file_browser_list, name='qbt_files_list'),
    path('files/delete/', views.file_delete, name='qbt_file_delete'),
    path('files/import/', views.file_import, name='qbt_file_import'),
    path('files/tabs/add/', views.file_tabs_add, name='qbt_file_tabs_add'),
    path('files/tabs/<int:pk>/delete/', views.file_tabs_delete, name='qbt_file_tabs_delete'),
]
