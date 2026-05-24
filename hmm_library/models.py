from django.db import models
from django.utils import timezone
from datetime import timedelta


class ExternalHMMModel(models.Model):
    SOURCE_CHOICES = [
        ('pfam', 'Pfam'),
        ('interpro', 'InterPro'),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, db_index=True)
    external_id = models.CharField(max_length=50, db_index=True)

    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=50, blank=True)

    has_pfam_model = models.BooleanField(default=True, db_index=True)
    pfam_members = models.JSONField(default=list, blank=True)

    hmm_file = models.FileField(upload_to='hmm_cache/%Y/%m/', null=True, blank=True)
    file_size = models.IntegerField(default=0)

    downloaded_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    api_url = models.URLField(blank=True)
    checksum = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = ['source', 'external_id']
        ordering = ['-downloaded_at']
        indexes = [
            models.Index(fields=['source', 'external_id']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"{self.get_source_display()}: {self.external_id} ({self.name or 'Unknown'})"

    def is_expired(self, days=90):
        if not self.expires_at:
            self.expires_at = self.downloaded_at + timedelta(days=days)
            self.save()
        return timezone.now() > self.expires_at

    def refresh_expiry(self, days=90):
        self.expires_at = timezone.now() + timedelta(days=days)
        self.save(update_fields=['expires_at'])

    @property
    def age_days(self):
        return (timezone.now() - self.downloaded_at).days


class HMMDownloadLog(models.Model):

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]

    source = models.CharField(max_length=20, choices=ExternalHMMModel.SOURCE_CHOICES)
    external_id = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    error_message = models.TextField(blank=True)
    hmm_model = models.ForeignKey(
        ExternalHMMModel,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='download_logs'
    )

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.source}:{self.external_id} - {self.status}"
