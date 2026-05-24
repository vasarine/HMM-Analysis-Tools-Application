from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class UserActionHistory(models.Model):
    ACTION_TYPES = [
        ('project_created', 'Project Created'),
        ('project_completed', 'Project Completed'),
        ('project_failed', 'Project Failed'),
        ('project_deleted', 'Project Deleted'),
        ('project_shared', 'Project Shared'),
        ('project_unshared', 'Project Unshared'),
        ('project_visibility_changed', 'Visibility Changed'),
        ('file_downloaded', 'File Downloaded'),
        ('workflow_run_started', 'Pipeline Started'),
        ('workflow_run_completed', 'Pipeline Completed'),
        ('workflow_run_failed', 'Pipeline Failed'),
        ('tool_completed', 'Completed'),
        ('tool_failed', 'Failed'),
    ]

    TOOL_TYPES = [
        ('hmmbuild', 'HMMBUILD'),
        ('hmmemit', 'HMMEMIT'),
        ('hmmsearch', 'HMMSEARCH'),
        ('workflow', 'Pipeline'),
        ('sequence_cleaner', 'Seq Cleaner'),
        ('fasta_validate', 'FASTA Validate'),
        ('clustalo', 'ClustalO'),
        ('mafft', 'MAFFT'),
        ('muscle', 'MUSCLE'),
        ('kalign', 'Kalign'),
        ('format_convert', 'Format Convert'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='action_history', null=True, blank=True)
    session_key = models.CharField(max_length=40, blank=True, default='')
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    tool_type = models.CharField(max_length=20, choices=TOOL_TYPES)
    timestamp = models.DateTimeField(auto_now_add=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    project = GenericForeignKey('content_type', 'object_id')

    project_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='success', choices=[('success', 'Success'), ('failure', 'Failure')])
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'User Action History'
        verbose_name_plural = 'User Action Histories'
        indexes = [
            models.Index(fields=['-timestamp', 'user']),
            models.Index(fields=['user', 'tool_type']),
            models.Index(fields=['session_key', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.user} - {self.action_type} - {self.project_name} at {self.timestamp}"

    def get_project_url(self, action_type_override=None):
        action_type = action_type_override or self.action_type

        if action_type in ('workflow_run_started', 'workflow_run_completed', 'workflow_run_failed') and self.project:
            return f"/workflows/runs/{self.project.id}/"

        if self.tool_type in ('hmmbuild', 'hmmemit', 'hmmsearch') and self.project:
            return f"/{self.tool_type}/status/{self.project.id}/"

        if self.tool_type in ('clustalo', 'mafft', 'muscle', 'kalign') and self.project:
            return f"/preprocessing/msa/align/{self.project.id}/status/"

        if self.project:
            if self.tool_type == 'fasta_validate':
                return f"/preprocessing/sequences/validate/{self.project.id}/"
            if self.tool_type in ('sequence_cleaner', 'sequence_clean'):
                return f"/preprocessing/sequences/clean/{self.project.id}/"
            if self.tool_type == 'format_convert':
                return f"/preprocessing/msa/convert/{self.project.id}/"

        if self.tool_type in ('fasta_validate', 'sequence_cleaner', 'sequence_clean', 'format_convert'):
            return "/users/my-projects/?tool=preprocessing"

        return None
