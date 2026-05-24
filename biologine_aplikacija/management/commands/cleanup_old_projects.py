import os
import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from hmmbuild.models import HMMBuildProject
from hmmsearch.models import HMMSearchProject
from hmmemit.models import HMMEmitProject
from sequence_tools.models import FASTAValidationProject, SequenceCleanerProject
from msa_tools.models import ClustalOmegaProject, FormatConversionProject
from workflows.models import WorkflowRun

logger = logging.getLogger(__name__)

MODEL_CONFIG = [
    (HMMBuildProject,         'HMMBuild',      ['msa_file', 'hmm_file'], True,  True),
    (HMMSearchProject,        'HMMSearch',     ['fasta_file', 'hmm_file', 'out_file', 'tblout_file', 'domtbl_file'], True, True),
    (HMMEmitProject,          'HMMEmit',       ['hmm_file', 'output_file'], True,  True),
    (FASTAValidationProject,  'FASTAValidate', ['input_fasta'], False, True),
    (SequenceCleanerProject,  'SequenceClean', ['input_fasta', 'output_fasta'], False, True),
    (ClustalOmegaProject,     'MSA',           ['input_fasta', 'output_alignment'], True,  True),
    (FormatConversionProject, 'FormatConvert', ['input_file', 'output_file'], False, True),
    (WorkflowRun,             'WorkflowRun',   ['input_file', 'output_file'], False, False),
]


class Command(BaseCommand):
    help = 'Cleans up old temporary projects and their files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Shows what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        total_deleted = 0
        total_files_deleted = 0
        total_space_freed = 0
        total_orphaned = 0
        total_failed = 0

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - nothing will be deleted'))

        for model_class, model_name, file_fields, _has_status, _orphans in MODEL_CONFIG:
            deleted, files_deleted, space_freed = self._cleanup_model(
                model_class, model_name, file_fields, now, dry_run
            )
            total_deleted += deleted
            total_files_deleted += files_deleted
            total_space_freed += space_freed

        one_hour_ago = now - timedelta(hours=1)

        for model_class, model_name, file_fields, has_status, _orphans in MODEL_CONFIG:
            if not has_status:
                continue
            failed = self._cleanup_failed_projects(
                model_class, model_name, file_fields, one_hour_ago, dry_run
            )
            total_failed += failed

        for model_class, model_name, file_fields, _has_status, check_orphans in MODEL_CONFIG:
            if not check_orphans:
                continue
            orphaned = self._cleanup_orphaned_projects(
                model_class, model_name, file_fields, dry_run
            )
            total_orphaned += orphaned

        self.stdout.write(self.style.SUCCESS(f'\n=== CLEANUP RESULTS ==='))
        self.stdout.write(f'Expired projects deleted: {total_deleted}')
        self.stdout.write(f'Failed projects deleted: {total_failed}')
        self.stdout.write(f'Projects without files deleted: {total_orphaned}')
        self.stdout.write(f'Files deleted: {total_files_deleted}')
        self.stdout.write(f'Space freed: {self._format_bytes(total_space_freed)}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nThis was a DRY RUN - nothing was deleted!'))

    def _delete_files(self, project, file_fields, dry_run):
        files_deleted = 0
        space_freed = 0
        for field_name in file_fields:
            file_field = getattr(project, field_name, None)
            if not file_field:
                continue
            try:
                file_path = file_field.path
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    if not dry_run:
                        os.remove(file_path)
                        self.stdout.write(f'  Deleted: {file_path}')
                    else:
                        self.stdout.write(f'  [DRY RUN] Would delete: {file_path}')
                    files_deleted += 1
                    space_freed += file_size
            except Exception as e:
                logger.error(f'Error deleting file {field_name}: {e}')
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {e}'))
        return files_deleted, space_freed

    def _cleanup_model(self, model_class, model_name, file_fields, now, dry_run):
        expired_projects = model_class.objects.filter(
            expires_at__isnull=False,
            expires_at__lt=now,
        )

        count = expired_projects.count()

        if count == 0:
            self.stdout.write(f'{model_name}: No expired projects found')
            return 0, 0, 0

        self.stdout.write(f'\n{model_name}: Found {count} expired projects')

        files_deleted = 0
        space_freed = 0

        for project in expired_projects:
            fd, sf = self._delete_files(project, file_fields, dry_run)
            files_deleted += fd
            space_freed += sf

        if not dry_run:
            expired_projects.delete()
            self.stdout.write(self.style.SUCCESS(f'{model_name}: Deleted {count} projects'))
        else:
            self.stdout.write(self.style.WARNING(f'{model_name}: [DRY RUN] Would delete {count} projects'))

        return count, files_deleted, space_freed

    def _cleanup_failed_projects(self, model_class, model_name, file_fields, cutoff_time, dry_run):
        failed_projects = model_class.objects.filter(
            task_status__in=['FAILURE', 'PENDING'],
            created_at__lt=cutoff_time
        )

        count = failed_projects.count()

        if count == 0:
            return 0

        self.stdout.write(f'\n{model_name}: Found {count} failed/stuck projects')

        for project in failed_projects:
            self._delete_files(project, file_fields, dry_run)
            if not dry_run:
                self.stdout.write(f'  Deleted project: {project.name} (Status: {project.task_status})')
                project.delete()

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f'{model_name}: Deleted {count} failed projects'))
        else:
            self.stdout.write(self.style.WARNING(f'{model_name}: [DRY RUN] Would delete {count} projects'))

        return count

    def _cleanup_orphaned_projects(self, model_class, model_name, file_fields, dry_run):
        all_projects = model_class.objects.all()
        orphaned_projects = []

        for project in all_projects:
            has_any_file = False
            for field_name in file_fields:
                file_field = getattr(project, field_name, None)
                if file_field and file_field.name:
                    try:
                        if os.path.exists(file_field.path):
                            has_any_file = True
                            break
                    except Exception:
                        pass
            if not has_any_file:
                orphaned_projects.append(project)

        count = len(orphaned_projects)

        if count == 0:
            return 0

        self.stdout.write(f'\n{model_name}: Found {count} projects without files')

        if not dry_run:
            for project in orphaned_projects:
                project.delete()
                self.stdout.write(f'  Deleted DB record: {project.name} (ID={project.id})')
            self.stdout.write(self.style.SUCCESS(f'{model_name}: Deleted {count} orphaned projects'))
        else:
            for project in orphaned_projects:
                self.stdout.write(f'  [DRY RUN] Would delete: {project.name} (ID={project.id})')

        return count

    def _format_bytes(self, bytes_size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} TB"
