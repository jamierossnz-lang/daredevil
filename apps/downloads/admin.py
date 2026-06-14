from django.contrib import admin
from .models import DownloadItem


@admin.register(DownloadItem)
class DownloadItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'media_type', 'status', 'progress', 'added_at')
    list_filter = ('media_type', 'status')
    search_fields = ('title',)
