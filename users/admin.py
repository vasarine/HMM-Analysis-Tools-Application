from django.contrib import admin
from .models import UserActionHistory


@admin.register(UserActionHistory)
class UserActionHistoryAdmin(admin.ModelAdmin):
    list_display = ['user', 'action_type', 'tool_type', 'project_name', 'status', 'timestamp']
    list_filter = ['action_type', 'tool_type', 'status', 'timestamp']
    search_fields = ['user__username', 'project_name', 'description']
    readonly_fields = ['timestamp', 'content_type', 'object_id']
    ordering = ['-timestamp']
