from django.contrib import admin

from .models import ArrService


@admin.register(ArrService)
class ArrServiceAdmin(admin.ModelAdmin):
    list_display = ('kind', 'url', 'public_url', 'root_folder', 'quality_profile_id', 'updated_at')
