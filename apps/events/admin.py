from django.contrib import admin
from .models import DomainEvent


@admin.register(DomainEvent)
class DomainEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'created_at')
    list_filter = ('event_type',)
    readonly_fields = ('event_type', 'payload', 'created_at')

    def has_add_permission(self, request):
        return False
