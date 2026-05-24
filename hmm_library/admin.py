from django.contrib import admin
from django.utils.html import format_html
from .models import ExternalHMMModel, HMMDownloadLog


@admin.register(ExternalHMMModel)
class ExternalHMMModelAdmin(admin.ModelAdmin):
    list_display = [
        'external_id',
        'source',
        'name',
        'file_size_mb',
        'age_display',
        'downloaded_at',
    ]
    list_filter = ['source', 'downloaded_at']
    search_fields = ['external_id', 'name', 'description']
    readonly_fields = [
        'downloaded_at',
        'file_size',
        'checksum',
        'age_days',
    ]

    fieldsets = (
        ('Identification', {
            'fields': ('source', 'external_id', 'name', 'description')
        }),
        ('File', {
            'fields': ('hmm_file', 'file_size', 'checksum')
        }),
        ('Cache management', {
            'fields': (
                'downloaded_at',
                'expires_at',
                'age_days',
            )
        }),
        ('Metadata', {
            'fields': ('version', 'api_url'),
            'classes': ('collapse',)
        }),
    )

    def file_size_mb(self, obj):
        return f"{obj.file_size / (1024 * 1024):.2f} MB"
    file_size_mb.short_description = 'Size'

    def age_display(self, obj):
        days = obj.age_days
        if days == 0:
            return format_html('<span style="color: green;">Today</span>')
        elif days < 30:
            return format_html(f'<span style="color: green;">{days}d</span>')
        elif days < 90:
            return format_html(f'<span style="color: orange;">{days}d</span>')
        else:
            return format_html(f'<span style="color: red;">{days}d</span>')
    age_display.short_description = 'Age'

    actions = ['refresh_metadata', 'refresh_expiry']

    def refresh_metadata(self, request, queryset):
        from .tasks import update_cache_metadata

        count = 0
        for obj in queryset:
            update_cache_metadata.delay(obj.source, obj.external_id)
            count += 1

        self.message_user(request, f'Started metadata update for {count} models')
    refresh_metadata.short_description = 'Refresh metadata'

    def refresh_expiry(self, request, queryset):
        count = queryset.count()
        for obj in queryset:
            obj.refresh_expiry(days=90)

        self.message_user(request, f'Extended expiry for {count} models by 90 days')
    refresh_expiry.short_description = 'Extend expiry (90d)'


@admin.register(HMMDownloadLog)
class HMMDownloadLogAdmin(admin.ModelAdmin):
    list_display = [
        'source',
        'external_id',
        'status',
        'started_at',
        'completed_at',
        'duration',
    ]
    list_filter = ['status', 'source', 'started_at']
    search_fields = ['external_id', 'error_message']
    readonly_fields = ['started_at', 'completed_at', 'duration']

    def duration(self, obj):
        if obj.completed_at and obj.started_at:
            delta = obj.completed_at - obj.started_at
            seconds = delta.total_seconds()
            if seconds < 1:
                return f"{seconds * 1000:.0f}ms"
            else:
                return f"{seconds:.1f}s"
        return "-"
    duration.short_description = 'Duration'

    def has_add_permission(self, request):
        return False
