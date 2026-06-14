from django.urls import path
from . import views

urlpatterns = [
    path('', views.queue, name='download_queue'),
    path('status/', views.queue_status_json, name='queue_status'),
    path('<int:pk>/pause/', views.item_pause, name='item_pause'),
    path('<int:pk>/resume/', views.item_resume, name='item_resume'),
    path('<int:pk>/delete/', views.item_delete, name='item_delete'),
    path('<int:pk>/retry/', views.item_retry, name='item_retry'),
    path('<int:pk>/begin-download/', views.item_begin_download, name='item_begin_download'),
    path('<int:pk>/search-failed/', views.item_search_failed, name='item_search_failed'),
    path('moves/', views.moves_page, name='moves_page'),
    path('moves/<int:pk>/retry/', views.move_retry, name='move_retry'),
    path('moves/<int:pk>/delete/', views.move_delete, name='move_delete'),
]
