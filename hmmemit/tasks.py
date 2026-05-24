from biologine_aplikacija.error_utils import parse_hmmemit_error, sanitize_exception_message
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
    soft_time_limit=300,
    time_limit=360,
)
def run_hmmemit(self, project_id, hmm_path, out_path, num_seqs, seed=None,
                workflow_run_id=None, step_index=None, parameters=None):
    from .models import HMMEmitProject

    def _mark_failed(message=None):
        update = {'task_status': 'FAILURE'}
        if message:
            update['result_text'] = f"ERROR:{message}"
        HMMEmitProject.objects.filter(id=project_id).update(**update)

    try:
        self.update_state(state='STARTED', meta={'progress': 50, 'message': 'Generating sequences...'})

        project = HMMEmitProject.objects.get(id=project_id)
        project.task_status = 'STARTED'
        project.save(update_fields=['task_status'])

        builder = CommandBuilder.from_file(SCHEMAS_DIR / "hmmemit.json")
        builder.set("num_seqs", num_seqs)
        if seed is not None:
            builder.set("seed", seed)
        if parameters:
            builder.set_many(parameters, ignore_unknown=True)
        io_flags = {"-o": out_path}
        positional_args = [hmm_path]
        command = builder.build(io_flags=io_flags, positional_args=positional_args, strict=True)
        logger.info(f"Executing command: {' '.join(command)}")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=280,
            cwd=os.path.dirname(out_path),
        )

        if result.returncode != 0:
            friendly = parse_hmmemit_error(result.stderr or result.stdout)
            logger.error(f"hmmemit error (rc={result.returncode}): stderr={result.stderr!r} stdout={result.stdout!r}")

            project.task_status = 'FAILURE'
            project.result_text = f"ERROR:{friendly}"
            project.save(update_fields=['task_status', 'result_text'])

            finalize_user_action(
                user=project.user,
                tool_type='hmmemit',
                project=project,
                action_type='project_failed',
                status='failure',
                description='HMMEMIT project failed',
                error_message=friendly,
            )

            if workflow_run_id is not None:
                from workflows.tasks import run_next_workflow_step
                run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

            raise RuntimeError(friendly)

        with open(out_path, 'r', encoding='utf-8', errors='replace') as f:
            output_text = f.read()

        project.result_text = output_text
        project.task_status = 'SUCCESS'
        project.save(update_fields=['result_text', 'task_status'])

        if output_text.lstrip().startswith('# STOCKHOLM'):
            seq_names = set()
            for line in output_text.splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line == '//':
                    continue
                first = line.split(None, 1)
                if first:
                    seq_names.add(first[0])
            actual_count = len(seq_names)
        else:
            actual_count = output_text.count('\n>') + (
                1 if output_text.lstrip().startswith('>') else 0
            )

        finalize_user_action(
            user=project.user,
            tool_type='hmmemit',
            project=project,
            action_type='project_completed',
            status='success',
            description=f'HMMEMIT project completed successfully - generated {actual_count} sequence(s)',
        )

        if workflow_run_id is not None:
            from workflows.tasks import run_next_workflow_step
            run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

        self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'Sequences generated successfully'})
        return {
            'status': 'success',
            'stdout': result.stdout,
            'stderr': result.stderr,
            'output_text': output_text,
        }

    except SoftTimeLimitExceeded:
        logger.warning(f"hmmemit task {self.request.id} exceeded time limit")
        _mark_failed("HMMEMIT ran longer than the allowed time and was stopped. "
                     "Try fewer sequences (-N) or a smaller HMM profile.")
        raise

    except Exception as e:
        logger.error(f"hmmemit task error: {e}")
        _mark_failed(sanitize_exception_message(
            e, fallback="HMMEMIT ran into an unexpected error."
        ))
        raise
