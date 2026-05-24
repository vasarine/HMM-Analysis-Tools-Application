from celery import shared_task, states
from celery.exceptions import SoftTimeLimitExceeded
import subprocess
import os
import logging
from users.history_utils import log_user_action, finalize_user_action
from biologine_aplikacija.error_utils import parse_hmmbuild_error, sanitize_exception_message
from biologine_aplikacija.parameter_builder import CommandBuilder, SCHEMAS_DIR

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    autoretry_for=(subprocess.SubprocessError, OSError),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
    soft_time_limit=300,
    time_limit=360,
)
def run_hmmbuild(self, project_id, input_fasta_path, output_hmm_path, skip_history=False,
                 workflow_run_id=None, step_index=None, parameters=None,
                 annotated_msa_path=None):
    from .models import HMMBuildProject

    def _mark_failed(message=None):
        update = {'task_status': 'FAILURE'}
        if message:
            update['result_text'] = f"ERROR:{message}"
        HMMBuildProject.objects.filter(id=project_id).update(**update)

    try:
        self.update_state(state='STARTED', meta={'progress': 50, 'message': 'Building HMM profile...'})

        project = HMMBuildProject.objects.get(id=project_id)
        project.task_status = 'STARTED'
        project.save(update_fields=['task_status'])

        builder = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        if parameters:
            builder.set_many(parameters, ignore_unknown=True)
        positional_args = [output_hmm_path, input_fasta_path]
        io_flags = {"-O": annotated_msa_path} if annotated_msa_path else None
        command = builder.build(io_flags=io_flags, positional_args=positional_args, strict=True)
        logger.info(f"Executing command: {' '.join(command)}")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=280,
            cwd=os.path.dirname(input_fasta_path),
        )

        if result.returncode != 0:
            friendly = parse_hmmbuild_error(result.stderr or result.stdout)
            logger.error(f"hmmbuild error (rc={result.returncode}): stderr={result.stderr!r} stdout={result.stdout!r}")

            project.task_status = 'FAILURE'
            project.result_text = f"ERROR:{friendly}"
            project.save(update_fields=['task_status', 'result_text'])

            if not skip_history:
                finalize_user_action(
                    user=project.user,
                    tool_type='hmmbuild',
                    project=project,
                    action_type='project_failed',
                    status='failure',
                    description='HMMBUILD project failed',
                    error_message=friendly,
                )

            if workflow_run_id is not None:
                from workflows.tasks import run_next_workflow_step
                run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

            raise RuntimeError(friendly)

        with open(output_hmm_path, 'r') as f:
            hmm_content = f.read()

        project.result_text = hmm_content
        project.task_status = 'SUCCESS'

        update_fields = ['result_text', 'task_status']
        if annotated_msa_path and not os.path.exists(annotated_msa_path):
            project.annotated_msa_file = None
            update_fields.append('annotated_msa_file')

        project.save(update_fields=update_fields)

        if not skip_history:
            finalize_user_action(
                user=project.user,
                tool_type='hmmbuild',
                project=project,
                action_type='project_completed',
                status='success',
                description='HMMBUILD project completed successfully',
            )

        if workflow_run_id is not None:
            from workflows.tasks import run_next_workflow_step
            run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

        self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'HMM build completed successfully'})
        return {
            'status': 'success',
            'stdout': result.stdout,
            'stderr': result.stderr,
            'hmm_content': hmm_content,
        }

    except SoftTimeLimitExceeded:
        logger.warning(f"hmmbuild task {self.request.id} exceeded time limit")
        _mark_failed("HMMBUILD ran longer than the allowed time and was stopped. "
                     "Try a smaller alignment or fewer expert calibration iterations.")
        raise

    except Exception as e:
        logger.error(f"hmmbuild task error: {e}")
        _mark_failed(sanitize_exception_message(
            e, fallback="HMMBUILD ran into an unexpected error."
        ))
        raise
