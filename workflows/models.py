from datetime import timedelta
from django.db import models
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
import uuid
from biologine_aplikacija.models import SharedProjectMixin

TOOL_TYPES = [
    ('fasta_validate', 'FASTA Validator'),
    ('sequence_clean', 'Sequence Cleaner'),
    ('clustal_omega', 'MSA'),
    ('format_convert', 'Format Converter'),
    ('hmmbuild', 'HMM Build'),
    ('hmmsearch', 'HMM Search'),
    ('hmmemit', 'HMM Emit'),
]

TOOL_META = {
    'fasta_validate': {'label': 'Validate',   'color': '#5c9f76'},
    'sequence_clean': {'label': 'Clean',      'color': '#c08a45'},
    'clustal_omega':  {'label': 'MSA Align',  'color': '#668fc2'},
    'format_convert': {'label': 'Convert',    'color': '#9278c4'},
    'hmmbuild':       {'label': 'Build HMM',  'color': '#c67368'},
    'hmmsearch':      {'label': 'HMM Search', 'color': '#c3769a'},
    'hmmemit':        {'label': 'HMM Emit',   'color': '#4da7a2'},
}


class Workflow(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='workflows',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_template = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def steps_display(self):
        return [
            TOOL_META.get(s.tool_type, {}).get('label', s.tool_type)
            for s in self.steps.order_by('order')
        ]


class WorkflowStep(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='steps')
    order = models.PositiveIntegerField(default=0)
    tool_type = models.CharField(max_length=50, choices=TOOL_TYPES)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.workflow.name} – Step {self.order}: {self.tool_type}"

    @property
    def label(self):
        return TOOL_META.get(self.tool_type, {}).get('label', self.tool_type)

    @property
    def color(self):
        return TOOL_META.get(self.tool_type, {}).get('color', '#94a3b8')


class WorkflowRun(SharedProjectMixin):
    STATUS_CHOICES = [
        ('PENDING',  'Pending'),
        ('RUNNING',  'Running'),
        ('SUCCESS',  'Success'),
        ('FAILURE',  'Failure'),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.SET_NULL, null=True, blank=True, related_name='runs')
    name = models.CharField(max_length=200, blank=True, default='')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='workflow_runs',
    )
    input_file = models.FileField(upload_to='workflows/input/', blank=True)
    output_file = models.FileField(upload_to='workflows/output/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    share_token = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    current_step_index = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    task_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    is_temporary = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

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

    @property
    def task_status(self):
        return self.status

    def __str__(self):
        wf_name = self.name or (self.workflow.name if self.workflow else 'deleted workflow')
        return f"Run of '{wf_name}' ({self.status})"


class WorkflowRunInput(models.Model):
    workflow_run = models.ForeignKey(
        'WorkflowRun', on_delete=models.CASCADE, related_name='inputs'
    )
    role = models.CharField(max_length=64)
    step_index = models.IntegerField(default=-1)
    data_type = models.CharField(max_length=32)
    file = models.FileField(upload_to='workflows/inputs/', null=True, blank=True)
    accession = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        unique_together = ('workflow_run', 'role')
        ordering = ['step_index', 'role']

    def __str__(self):
        return f'{self.workflow_run_id}/{self.role}'


class StepRun(models.Model):
    STATUS_CHOICES = [
        ('PENDING',  'Pending'),
        ('RUNNING',  'Running'),
        ('SUCCESS',  'Success'),
        ('FAILURE',  'Failure'),
    ]

    workflow_run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name='step_runs')
    step = models.ForeignKey(WorkflowStep, on_delete=models.SET_NULL, null=True, blank=True)
    tool_type_snapshot = models.CharField(max_length=50, blank=True, default='')
    step_order_snapshot = models.IntegerField(default=0)
    step_config_snapshot = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    output_file = models.FileField(upload_to='workflows/steps/', null=True, blank=True)
    error_message = models.TextField(blank=True, default='')

    content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    project = GenericForeignKey('content_type', 'object_id')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['step_order_snapshot']

    def __str__(self):
        label = self.step.tool_type if self.step else self.tool_type_snapshot
        return f"StepRun: {label} order={self.step_order_snapshot} ({self.status})"
