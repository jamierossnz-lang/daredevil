from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('panel/',           views.panel,     name='panel'),
    path('count/',           views.count,     name='count'),
    path('<int:pk>/dismiss/', views.dismiss,  name='dismiss'),
    path('clear/',           views.clear_all,  name='clear_all'),
    path('prefs/',           views.prefs,      name='prefs'),
    path('prefs/save/',      views.save_prefs, name='save_prefs'),
]
