from django.urls import path

from . import search_views, views, webhooks

urlpatterns = [
    # Search (Prowlarr) — replaces /qbt/search/
    path('search/', search_views.search_page, name='search'),
    path('search/run/', search_views.search_run, name='search_run'),
    path('search/grab/', search_views.search_grab, name='search_grab'),

    # Sonarr/Radarr → Daredevil (secret-checked, exempt from login — see accounts.middleware)
    path('arr/webhook/<str:kind>/<str:secret>/', webhooks.receive, name='arr_webhook'),

    # Settings tabs
    path('arr/prowlarr/apps/connect/', views.prowlarr_connect_apps, name='arr_prowlarr_connect_apps'),
    path('arr/prowlarr/apps/sync/', views.prowlarr_sync_apps, name='arr_prowlarr_sync_apps'),
    path('arr/prowlarr/catalogue/', views.prowlarr_catalogue, name='arr_prowlarr_catalogue'),
    path('arr/prowlarr/catalogue/form/', views.prowlarr_definition_form, name='arr_prowlarr_definition_form'),
    path('arr/prowlarr/indexers/save/', views.prowlarr_indexer_save, name='arr_prowlarr_indexer_save'),
    path('arr/prowlarr/indexers/test/', views.prowlarr_indexer_test, name='arr_prowlarr_indexer_test'),
    path('arr/prowlarr/indexers/testall/', views.prowlarr_indexer_test_all, name='arr_prowlarr_indexer_test_all'),
    path('arr/prowlarr/indexers/<int:indexer_id>/form/', views.prowlarr_indexer_form, name='arr_prowlarr_indexer_form'),
    path('arr/prowlarr/indexers/<int:indexer_id>/test/', views.prowlarr_indexer_test_existing, name='arr_prowlarr_indexer_test_existing'),
    path('arr/prowlarr/indexers/<int:indexer_id>/toggle/', views.prowlarr_indexer_toggle, name='arr_prowlarr_indexer_toggle'),
    path('arr/prowlarr/indexers/<int:indexer_id>/delete/', views.prowlarr_indexer_delete, name='arr_prowlarr_indexer_delete'),
    path('arr/import-library/', views.import_library, name='arr_import_library'),
    path('arr/browse/', views.browse_folders, name='arr_browse_folders'),

    path('arr/<str:kind>/overview/', views.overview, name='arr_overview'),
    path('arr/<str:kind>/config/', views.save_config, name='arr_save_config'),
    path('arr/<str:kind>/test/', views.test_connection, name='arr_test'),
    path('arr/<str:kind>/options/', views.options, name='arr_options'),
    path('arr/<str:kind>/lookup/', views.lookup, name='arr_lookup'),
    path('arr/<str:kind>/rootfolders/', views.root_folder_add, name='arr_root_folder_add'),
    path('arr/<str:kind>/rootfolders/<int:folder_id>/delete/', views.root_folder_delete, name='arr_root_folder_delete'),
    path('arr/<str:kind>/qbittorrent/', views.connect_qbittorrent, name='arr_connect_qbittorrent'),
    path('arr/<str:kind>/downloads/', views.save_downloads, name='arr_save_downloads'),
    path('arr/<str:kind>/webhook/', views.connect_webhook, name='arr_connect_webhook'),
    path('arr/<str:kind>/webhook/test/', views.test_webhook, name='arr_test_webhook'),
    path('arr/<str:kind>/naming/', views.save_naming, name='arr_save_naming'),
    path('arr/<str:kind>/quality/', views.quality, name='arr_quality'),
    path('arr/<str:kind>/quality/profiles/<int:profile_id>/', views.quality_profile_save, name='arr_quality_profile_save'),
    path('arr/<str:kind>/plex/', views.plex_link, name='arr_plex_link'),
    path('arr/<str:kind>/plex/test/', views.plex_link_test, name='arr_plex_link_test'),
]
