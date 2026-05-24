"""
Celery task that runs the hmmsearch command in the background and
saves its output back to the project.
"""

from biologine_aplikacija.error_utils import parse_hmmsearch_error, sanitize_exception_message
from biologine_aplikacija.parameter_builder import CommandBuilder, SCHEMAS_DIR
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
import subprocess
import os
import logging
from users.history_utils import finalize_user_action

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(subprocess.SubprocessError, OSError),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
    soft_time_limit=600,
    time_limit=720,
)
def run_hmmsearch(self, project_id, hmm_path, fasta_path, out_path, tblout_path, domtbl_path,
                  workflow_run_id=None, step_index=None, parameters=None,
                  hits_msa_path=None, pfamtbl_path=None):
    from .models import HMMSearchProject

    def _mark_failed(message=None):
        update = {'task_status': 'FAILURE'}
        if message:
            update['result_text'] = f"ERROR:{message}"
        HMMSearchProject.objects.filter(id=project_id).update(**update)

    try:
        self.update_state(state='STARTED', meta={'progress': 50, 'message': 'Running HMM search...'})

        project = HMMSearchProject.objects.get(id=project_id)
        project.task_status = 'STARTED'
        project.save(update_fields=['task_status'])

        builder = CommandBuilder.from_file(SCHEMAS_DIR / "hmmsearch.json")
        if parameters:
            builder.set_many(parameters, ignore_unknown=True)
        io_flags = {
            "-o": out_path,
            "--domtblout": domtbl_path,
            "--tblout": tblout_path,
        }
        if hits_msa_path:
            io_flags["-A"] = hits_msa_path
        if pfamtbl_path:
            io_flags["--pfamtblout"] = pfamtbl_path
        command = builder.build(io_flags=io_flags, positional_args=[hmm_path, fasta_path], strict=True)
        logger.info(f"Executing command: {' '.join(command)}")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=580,
            cwd=os.path.dirname(fasta_path),
        )

        if result.returncode != 0:
            friendly = parse_hmmsearch_error(result.stderr or result.stdout)
            logger.error(f"hmmsearch error (rc={result.returncode}): stderr={result.stderr!r} stdout={result.stdout!r}")

            project.task_status = 'FAILURE'
            project.result_text = f"ERROR:{friendly}"
            project.save(update_fields=['task_status', 'result_text'])

            finalize_user_action(
                user=project.user,
                tool_type='hmmsearch',
                project=project,
                action_type='project_failed',
                status='failure',
                description='HMMSEARCH project failed',
                error_message=friendly,
            )

            if workflow_run_id is not None:
                from workflows.tasks import run_next_workflow_step
                run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

            raise RuntimeError(friendly)

        with open(out_path, 'r') as f:
            result_text = f.read()
        with open(tblout_path, 'r') as f:
            tblout_text = f.read()
        with open(domtbl_path, 'r') as f:
            domtbl_text = f.read()

        project.result_text = result_text
        project.tblout_text = tblout_text
        project.domtbl_text = domtbl_text
        project.task_status = 'SUCCESS'

        update_fields = ['result_text', 'tblout_text', 'domtbl_text', 'task_status']
        if hits_msa_path and not os.path.exists(hits_msa_path):
            project.hits_msa_file = None
            update_fields.append('hits_msa_file')
        if pfamtbl_path and not os.path.exists(pfamtbl_path):
            project.pfamtbl_file = None
            update_fields.append('pfamtbl_file')

        project.save(update_fields=update_fields)

        finalize_user_action(
            user=project.user,
            tool_type='hmmsearch',
            project=project,
            action_type='project_completed',
            status='success',
            description='HMMSEARCH project completed successfully',
        )

        if workflow_run_id is not None:
            from workflows.tasks import run_next_workflow_step
            run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

        self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'Search completed successfully'})
        return {
            'status': 'success',
            'stdout': result.stdout,
            'stderr': result.stderr,
        }

    except SoftTimeLimitExceeded:
        logger.warning(f"hmmsearch task {self.request.id} exceeded time limit")
        _mark_failed("HMMSEARCH ran longer than the allowed time and was stopped. "
                     "Try a smaller sequence database or stricter thresholds.")
        raise

    except Exception as e:
        logger.error(f"hmmsearch task error: {e}")
        _mark_failed(sanitize_exception_message(
            e, fallback="HMMSEARCH ran into an unexpected error."
        ))
        raise
