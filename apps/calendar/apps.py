from django.apps import AppConfig


class CalendarConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.calendar'
    label = 'media_calendar'  # "calendar" alone reads like the stdlib module
    verbose_name = 'Calendar'
