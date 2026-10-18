from django.urls import path
from . import views

urlpatterns = [
    path('', views.swipe_deck, name='swipe_deck'),
    path('data/', views.deck_data, name='swipe_deck_data'),
    path('<int:pk>/left/', views.swipe_left, name='swipe_left'),
    path('<int:pk>/right/', views.swipe_right, name='swipe_right'),
    path('<int:pk>/trailer/', views.trailer, name='swipe_trailer'),
]
