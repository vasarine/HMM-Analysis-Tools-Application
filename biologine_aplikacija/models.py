from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import uuid


class SharedProjectMixin(models.Model):
    VISIBILITY_CHOICES = [
        ('private', 'Private'),
        ('link', 'Link'),
        ('public', 'Public'),
    ]

    visibility = models.CharField(
        max_length=10, choices=VISIBILITY_CHOICES, default='private', db_index=True
    )
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='shared_%(class)s_projects',
        blank=True,
    )
    share_outputs = models.BooleanField(default=True)
    share_inputs = models.BooleanField(default=False)

    class Meta:
        abstract = True

    @property
    def task_status(self):
        return 'SUCCESS'

    def save(self, *args, **kwargs):
        if not self.visibility:
            self.visibility = 'private'
        if hasattr(self, 'share_token') and not self.share_token:
            self.share_token = uuid.uuid4()
        super().save(*args, **kwargs)

    def can_view(self, user):
        if self.user is None:
            return True
        if self.user == user:
            return True
        if self.visibility in ('public', 'link'):
            return True
        if user.is_authenticated and self.shared_with.filter(id=user.id).exists():
            return True
        return False

    def can_edit(self, user):
        return self.user == user

    def can_download_input(self, user):
        if self.user == user:
            return True
        if self.can_view(user) and self.share_inputs:
            return True
        return False

    def can_download_output(self, user):
        if self.user == user:
            return True
        if self.can_view(user) and self.share_outputs:
            return True
        return False


class BaseHMMProject(models.Model):
    VISIBILITY_CHOICES = [
        ('private', 'Private'),
        ('link', 'Link'),
        ('public', 'Public'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=255)
    result_text = models.TextField(null=True, blank=True)

    task_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    task_status = models.CharField(max_length=50, default='PENDING', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    is_temporary = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default='private', db_index=True)
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='shared_%(class)s_projects',
        blank=True
    )
    share_token = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    share_outputs = models.BooleanField(default=True)
    share_inputs = models.BooleanField(default=False)

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['user', 'task_status', '-created_at']),
            models.Index(fields=['user', 'expires_at']),
            models.Index(fields=['visibility', 'expires_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.pk and self.expires_at is None:
            if self.user is None:
                self.is_temporary = True
                self.expires_at = timezone.now() + timedelta(days=7)
            else:
                self.is_temporary = False
                self.expires_at = timezone.now() + timedelta(days=90)
        super().save(*args, **kwargs)

    def can_view(self, user):
        if self.user is None:
            return True
        if self.user == user:
            return True
        if self.visibility == 'public':
            return True
        if self.visibility == 'link':
            return True
        if user.is_authenticated and self.shared_with.filter(id=user.id).exists():
            return True
        return False

    def can_edit(self, user):
        return self.user == user

    def can_download_input(self, user):
        if self.user == user:
            return True
        if self.can_view(user) and self.share_inputs:
            return True
        return False

    def can_download_output(self, user):
        if self.user == user:
            return True
        if self.can_view(user) and self.share_outputs:
            return True
        return False

    def __str__(self):
        username = self.user.username if self.user else "Anonymous"
        return f"{self.name} ({username})"
