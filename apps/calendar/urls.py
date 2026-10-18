from django.urls import path

from . import views

urlpatterns = [
    path('', views.calendar_page, name='calendar'),
    path('events/', views.events_json, name='calendar_events'),
    path('event.ics', views.event_ics, name='calendar_event_ics'),
    # Exempt from login (calendar apps can't sign in) — see accounts.middleware
    path('feed/<str:token>.ics', views.feed, name='calendar_feed'),
    path('reset-feed/', views.feed_reset, name='calendar_feed_reset'),  # not under feed/: needs a login
]
