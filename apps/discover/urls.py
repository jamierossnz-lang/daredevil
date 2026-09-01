from django.urls import path
from . import views

urlpatterns = [
    path('', views.swipe_deck, name='swipe_deck'),
    path('data/', views.deck_data, name='swipe_deck_data'),
    path('<int:pk>/left/', views.swipe_left, name='swipe_left'),
    path('<int:pk>/add/', views.swipe_right_add, name='swipe_right_add'),
    path('<int:pk>/add-download/', views.swipe_right_download, name='swipe_right_download'),
]
