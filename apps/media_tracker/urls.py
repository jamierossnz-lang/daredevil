from django.urls import path
from . import views
from . import show_views
from . import movie_views
from . import task_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('discover/', views.dashboard_discover, name='dashboard_discover'),

    # TV Shows — stored in and managed by Sonarr (ids are Sonarr series ids)
    path('shows/', show_views.tv_shows, name='tv_shows'),
    path('shows/search/', show_views.show_search, name='tmdb_search_tv'),
    path('shows/watching/', show_views.shows_watching, name='shows_watching'),
    path('shows/add/', show_views.show_add, name='tv_show_add'),
    path('shows/tmdb/<int:tmdb_id>/', show_views.show_by_tmdb, name='show_by_tmdb'),
    path('shows/episodes/monitor/', show_views.episodes_monitor, name='episodes_monitor'),
    path('shows/episodefiles/<int:file_id>/delete/', show_views.episode_file_delete, name='episode_file_delete'),
    path('shows/releases/grab/', show_views.release_grab, name='show_release_grab'),
    path('shows/<int:series_id>/', show_views.show_detail, name='tv_show_detail'),
    path('shows/<int:series_id>/status/', show_views.show_status, name='tv_show_status'),
    path('shows/<int:series_id>/monitor/', show_views.show_monitor, name='tv_show_monitor'),
    path('shows/<int:series_id>/monitoring/', show_views.show_monitoring, name='tv_show_monitoring'),
    path('shows/<int:series_id>/seasons/<int:season_number>/monitor/', show_views.season_monitor, name='season_monitor'),
    path('shows/<int:series_id>/download/', show_views.show_download, name='tv_show_download'),
    path('shows/<int:series_id>/releases/', show_views.show_releases, name='tv_show_releases'),
    path('shows/<int:series_id>/refresh/', show_views.show_refresh, name='tv_show_refresh'),
    path('shows/<int:series_id>/edit/', show_views.show_edit, name='tv_show_edit'),
    path('shows/<int:series_id>/favourite/', show_views.show_favourite, name='tv_show_favourite'),
    path('shows/<int:series_id>/delete/', show_views.show_delete, name='tv_show_delete'),

    # Movies — stored in and managed by Radarr (ids are Radarr movie ids)
    path('movies/', movie_views.movies, name='movies'),
    path('movies/search/', movie_views.movie_search, name='tmdb_search_movie'),
    path('movies/add/', movie_views.movie_add, name='movie_add'),
    path('movies/tmdb/<int:tmdb_id>/', movie_views.movie_by_tmdb, name='movie_by_tmdb'),
    path('movies/moviefiles/<int:file_id>/delete/', movie_views.movie_file_delete, name='movie_file_delete'),
    path('movies/releases/grab/', movie_views.release_grab, name='movie_release_grab'),
    path('movies/<int:movie_id>/', movie_views.movie_detail, name='movie_detail'),
    path('movies/<int:movie_id>/status/', movie_views.movie_status, name='movie_status'),
    path('movies/<int:movie_id>/monitor/', movie_views.movie_monitor, name='movie_monitor'),
    path('movies/<int:movie_id>/download/', movie_views.movie_download, name='movie_download'),
    path('movies/<int:movie_id>/releases/', movie_views.movie_releases, name='movie_releases'),
    path('movies/<int:movie_id>/refresh/', movie_views.movie_refresh, name='movie_refresh'),
    path('movies/<int:movie_id>/edit/', movie_views.movie_edit, name='movie_edit'),
    path('movies/<int:movie_id>/favourite/', movie_views.movie_favourite, name='movie_favourite'),
    path('movies/<int:movie_id>/delete/', movie_views.movie_delete, name='movie_delete'),

    # App Settings
    path('settings/', views.app_settings_view, name='app_settings'),
    path('settings/save/', views.app_settings_save, name='app_settings_save'),
    path('settings/ping/', views.server_ping, name='server_ping'),
    path('settings/restart/', views.server_restart, name='server_restart'),
    path('settings/ntfy-test/', views.ntfy_test, name='ntfy_test'),

    # Streaming browse
    path('streaming/', views.streaming_browse, name='streaming_browse'),

    # Background Tasks
    path('tasks/', task_views.tasks_dashboard, name='tasks_dashboard'),
    path('tasks/panel/', task_views.tasks_panel, name='tasks_panel'),
    path('tasks/trigger/', task_views.trigger_task, name='trigger_task'),
    path('tasks/poll/<str:task_id>/', task_views.task_poll, name='task_poll'),
    path('tasks/recent/', task_views.recent_results_partial, name='tasks_recent'),
    # Schedule management
    path('tasks/schedule/<int:pk>/toggle/', task_views.schedule_toggle, name='schedule_toggle'),
    path('tasks/schedule/<int:pk>/update/', task_views.schedule_update, name='schedule_update'),
    path('tasks/schedule/<int:pk>/delete/', task_views.schedule_delete, name='schedule_delete'),
]
