from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import uuid
from biologine_aplikacija.models import SharedProjectMixin


class ClustalOmegaProject(SharedProjectMixin):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('STARTED', 'Started'),
        ('SUCCESS', 'Success'),
        ('FAILURE', 'Failure'),
    ]

    TOOL_CHOICES = [
        ('clustalo', 'Clustal Omega'),
        ('mafft', 'MAFFT'),
        ('muscle', 'MUSCLE'),
        ('kalign', 'Kalign'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='clustalo_projects',
    )
    name = models.CharField(max_length=255, blank=True, default='')

    tool = models.CharField(
        max_length=20,
        choices=TOOL_CHOICES,
        default='clustalo',
    )

    input_fasta = models.FileField(upload_to='msa_tools/clustalo/input/')
    output_alignment = models.FileField(
        upload_to='msa_tools/clustalo/output/',
        null=True,
        blank=True,
    )
    output_format = models.CharField(
        max_length=20,
        default='stockholm',
        choices=[
            ('stockholm', 'Stockholm (.sto)'),
            ('clustal', 'Clustal (.aln)'),
            ('fasta', 'Aligned FASTA (.fasta)'),
            ('phylip', 'Phylip (.phy)'),
            ('vienna', 'Vienna (.vie)'),
        ],
    )
    threads = models.IntegerField(default=1)
    parameters = models.JSONField(default=dict, blank=True)

    task_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    task_id = models.CharField(max_length=255, null=True, blank=True)
    error_message = models.TextField(blank=True, default='')

    sequence_count = models.IntegerField(null=True, blank=True)
    alignment_length = models.IntegerField(null=True, blank=True)

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
        tool_label = dict(self.TOOL_CHOICES).get(self.tool or 'clustalo', 'MSA')
        return f"{tool_label}: {self.name or 'Untitled'} ({username})"


class FormatConversionProject(SharedProjectMixin):
    FORMAT_CHOICES = [
        ('fasta', 'Aligned FASTA'),
        ('clustal', 'Clustal (.aln)'),
        ('stockholm', 'Stockholm (.sto)'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='format_conversion_projects',
    )
    name = models.CharField(max_length=255, blank=True, default='')

    input_file = models.FileField(upload_to='msa_tools/converter/input/')
    input_format = models.CharField(max_length=20, choices=FORMAT_CHOICES)
    output_format = models.CharField(max_length=20, choices=FORMAT_CHOICES)
    output_file = models.FileField(
        upload_to='msa_tools/converter/output/',
        null=True,
        blank=True,
    )

    sequence_count = models.IntegerField(null=True, blank=True)
    alignment_length = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')

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
        return f"Format Converter: {self.name or 'Untitled'} ({username})"
