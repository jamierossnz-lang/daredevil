from django.contrib import admin
from .models import TVShow, Season, Episode, Movie


@admin.register(TVShow)
class TVShowAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'number_of_seasons', 'monitor_new_episodes', 'added_at')
    list_filter = ('status', 'monitor_new_episodes', 'is_favourite')
    search_fields = ('name',)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ('show', 'season_number', 'episode_count', 'air_date')
    list_filter = ('show',)


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'air_date', 'download_status')
    list_filter = ('download_status', 'season__show')
    search_fields = ('name', 'season__show__name')


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'release_date', 'digital_release_date', 'download_status')
    list_filter = ('status', 'download_status', 'is_favourite')
    search_fields = ('title',)
