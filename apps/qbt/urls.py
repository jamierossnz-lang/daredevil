from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='qbt_dashboard'),
    path('torrents.json', views.torrents_json, name='torrents_json'),
    path('transfer.json', views.transfer_info_json, name='transfer_info'),
    path('torrent/<str:torrent_hash>/pause/', views.torrent_pause, name='torrent_pause'),
    path('torrent/<str:torrent_hash>/resume/', views.torrent_resume, name='torrent_resume'),
    path('torrent/<str:torrent_hash>/delete/', views.torrent_delete, name='torrent_delete'),
    path('add-magnet/', views.torrent_add_magnet, name='torrent_add_magnet'),
    path('search/', views.search_page, name='qbt_search'),
    path('search/run/', views.search_run, name='qbt_search_run'),
    path('settings/', views.settings_view, name='qbt_settings'),
    path('settings/save/', views.settings_save, name='qbt_settings_save'),
    path('settings/connection/', views.connection_save, name='qbt_connection_save'),
    path('categories/', views.categories_page, name='qbt_categories'),
    path('categories/create/', views.category_create, name='qbt_category_create'),
    path('categories/<str:name>/edit/', views.category_edit, name='qbt_category_edit'),
    path('categories/<str:name>/delete/', views.category_delete, name='qbt_category_delete'),
    path('categories/<str:name>/paths/', views.category_paths_save, name='qbt_category_paths'),
    path('categories/defaults/', views.category_defaults_save, name='qbt_category_defaults'),
    path('files/', views.file_browser_page, name='qbt_files'),
    path('files/list/', views.file_browser_list, name='qbt_files_list'),
    path('files/delete/', views.file_delete, name='qbt_file_delete'),
    path('files/move-completed/', views.file_move_completed, name='qbt_file_move_completed'),
    path('files/tabs/add/', views.file_tabs_add, name='qbt_file_tabs_add'),
    path('files/tabs/<int:pk>/delete/', views.file_tabs_delete, name='qbt_file_tabs_delete'),
]
