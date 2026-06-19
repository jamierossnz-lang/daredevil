from django.urls import path
from . import views
from . import task_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('discover/', views.dashboard_discover, name='dashboard_discover'),

    # TV Shows
    path('shows/', views.tv_shows, name='tv_shows'),
    path('shows/search/', views.tmdb_search_tv, name='tmdb_search_tv'),
    path('shows/add/', views.tv_show_add, name='tv_show_add'),
    path('shows/<int:pk>/', views.tv_show_detail, name='tv_show_detail'),
    path('shows/<int:pk>/favourite/', views.tv_show_toggle_favourite, name='tv_show_favourite'),
    path('shows/<int:pk>/monitor/', views.tv_show_toggle_monitor, name='tv_show_monitor'),
    path('shows/<int:pk>/delete/', views.tv_show_delete, name='tv_show_delete'),
    path('shows/<int:pk>/queue/', views.tv_show_queue_download, name='tv_show_queue'),

    # Movies
    path('movies/', views.movies, name='movies'),
    path('movies/search/', views.tmdb_search_movie, name='tmdb_search_movie'),
    path('movies/add/', views.movie_add, name='movie_add'),
    path('movies/<int:pk>/', views.movie_detail, name='movie_detail'),
    path('movies/<int:pk>/favourite/', views.movie_toggle_favourite, name='movie_favourite'),
    path('movies/<int:pk>/queue/', views.movie_queue_download, name='movie_queue'),
    path('movies/<int:pk>/delete/', views.movie_delete, name='movie_delete'),
    path('movies/<int:pk>/reset/', views.movie_reset_download, name='movie_reset'),
    path('shows/<int:pk>/reset/', views.tv_show_reset_download, name='tv_show_reset'),

    # App Settings
    path('settings/', views.app_settings_view, name='app_settings'),
    path('settings/save/', views.app_settings_save, name='app_settings_save'),
    path('settings/ping/', views.server_ping, name='server_ping'),
    path('settings/restart/', views.server_restart, name='server_restart'),
    path('settings/celery-restart/', views.celery_restart, name='celery_restart'),
    path('settings/ntfy-test/', views.ntfy_test, name='ntfy_test'),

    # Background Tasks
    path('tasks/', task_views.tasks_dashboard, name='tasks_dashboard'),
    path('tasks/trigger/', task_views.trigger_task, name='trigger_task'),
    path('tasks/poll/<str:task_id>/', task_views.task_poll, name='task_poll'),
    path('tasks/recent/', task_views.recent_results_partial, name='tasks_recent'),
    # Schedule management
    path('tasks/schedule/<int:pk>/toggle/', task_views.schedule_toggle, name='schedule_toggle'),
    path('tasks/schedule/<int:pk>/update/', task_views.schedule_update, name='schedule_update'),
    path('tasks/schedule/<int:pk>/delete/', task_views.schedule_delete, name='schedule_delete'),
]
