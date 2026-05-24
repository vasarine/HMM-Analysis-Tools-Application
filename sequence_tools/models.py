from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import uuid
from biologine_aplikacija.models import SharedProjectMixin


class FASTAValidationProject(SharedProjectMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='fasta_validation_projects'
    )
    name = models.CharField(max_length=255, blank=True, default='')
    input_fasta = models.FileField(upload_to='sequence_tools/fasta/')
    stats = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    is_temporary = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    share_token = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.pk and self.expires_at is None:
            if self.user is None:
                self.is_temporary = True
                self.expires_at = timezone.now() + timedelta(days=7)
            else:
                self.is_temporary = False
                self.expires_at = timezone.now() + timedelta(days=90)
        super().save(*args, **kwargs)

    def __str__(self):
        username = self.user.username if self.user else 'Anonymous'
        return f"FASTA Validation: {self.name or 'Untitled'} ({username})"


class SequenceCleanerProject(SharedProjectMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sequence_cleaner_projects'
    )
    name = models.CharField(max_length=255, blank=True, default='')
    input_fasta = models.FileField(upload_to='sequence_tools/cleaner/input/')
    output_fasta = models.FileField(
        upload_to='sequence_tools/cleaner/output/',
        null=True,
        blank=True,
    )
    options = models.JSONField(null=True, blank=True)
    stats = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    is_temporary = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    share_token = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.pk and self.expires_at is None:
            if self.user is None:
                self.is_temporary = True
                self.expires_at = timezone.now() + timedelta(days=7)
            else:
                self.is_temporary = False
                self.expires_at = timezone.now() + timedelta(days=90)
        super().save(*args, **kwargs)

    def __str__(self):
        username = self.user.username if self.user else 'Anonymous'
        return f"Sequence Cleaner: {self.name or 'Untitled'} ({username})"
