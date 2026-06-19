from django.urls import path
from . import views

app_name = 'plex'

urlpatterns = [
    path('', views.library, name='library'),
    path('thumb/', views.thumb, name='thumb'),
    path('delete/<str:rating_key>/', views.plex_delete, name='delete'),
    path('ignore/<str:rating_key>/', views.plex_ignore, name='ignore'),
]
