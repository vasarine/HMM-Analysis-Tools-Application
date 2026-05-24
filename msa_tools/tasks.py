import logging
import os
import re
import subprocess

from Bio import AlignIO, SeqIO
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from biologine_aplikacija.error_utils import parse_clustalo_error, sanitize_exception_message
from biologine_aplikacija.parameter_builder import CommandBuilder, SCHEMAS_DIR

logger = logging.getLogger(__name__)


def _fix_kalign_clustal(output_path):
    try:
        with open(output_path, 'r') as f:
            lines = f.readlines()
    except OSError:
        return

    if not lines or 'kalign' not in lines[0].lower():
        return

    fixed = [lines[0]]
    seen_ids = set()
    max_id_len = 0

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            fixed.append(line)
            continue
        parts = stripped.split()
        if len(parts) >= 2 and re.match(r'^[A-Za-z0-9_.|]+$', parts[0]):
            seq_data = parts[-1]
            if re.match(r'^[A-Za-z\-.*]+$', seq_data):
                acc = parts[0]
                seen_ids.add(acc)
                if len(acc) > max_id_len:
                    max_id_len = len(acc)

    pad = max_id_len + 6

    out_lines = [lines[0]]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            out_lines.append('\n')
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0] in seen_ids:
            seq_data = parts[-1]
            out_lines.append(f'{parts[0]:<{pad}}{seq_data}\n')
        else:
            out_lines.append(line)

    with open(output_path, 'w') as f:
        f.writelines(out_lines)


def _alignment_length(output_path, output_format):
    try:
        return AlignIO.read(output_path, output_format).get_alignment_length()
    except Exception as e:
        logger.warning(f"AlignIO could not parse alignment length: {e}")

    try:
        if output_format == 'fasta':
            try:
                first = next(SeqIO.parse(output_path, 'fasta'), None)
            except ValueError:
                first = next(SeqIO.parse(output_path, 'fasta-pearson'), None)
            return len(first.seq) if first else None
        with open(output_path) as f:
            for line in f:
                line = line.strip()
                if (line and not line.startswith('#') and not line.startswith('//')
                        and not line.startswith('CLUSTAL')):
                    parts = line.split()
                    if len(parts) == 2:
                        return len(parts[1])
    except OSError as e:
        logger.warning(f"Fallback alignment length also failed: {e}")
    return None


def _safe_log_history(tool_type, action, status, project, error_message=''):
    try:
        from users.history_utils import finalize_user_action
        finalize_user_action(project.user, tool_type, project, action,
                             status=status, error_message=error_message)
    except Exception as e:
        logger.warning(f"history log failed: {e}")


def _finalize_success(project, output_path, output_format, input_path):
    try:
        try:
            project.sequence_count = sum(1 for _ in SeqIO.parse(input_path, 'fasta'))
        except ValueError:
            project.sequence_count = sum(1 for _ in SeqIO.parse(input_path, 'fasta-pearson'))
    except Exception as e:
        logger.warning(f"Could not count input sequences: {e}")

    project.alignment_length = _alignment_length(output_path, output_format)

    from django.conf import settings
    project.output_alignment = os.path.relpath(output_path, settings.MEDIA_ROOT)
    project.task_status = 'SUCCESS'
    project.save(update_fields=['output_alignment', 'task_status',
                                'sequence_count', 'alignment_length'])


@shared_task(
    bind=True,
    soft_time_limit=300,
    time_limit=360,
)
def run_clustalo(self, project_id, input_path, output_path, output_format, threads, skip_history=False,
                 workflow_run_id=None, step_index=None, parameters=None):
    from .models import ClustalOmegaProject

    project = None
    try:
        project = ClustalOmegaProject.objects.get(id=project_id)
        project.task_status = 'STARTED'
        if project.tool != 'clustalo':
            project.tool = 'clustalo'
            project.save(update_fields=['task_status', 'tool'])
        else:
            project.save(update_fields=['task_status'])

        self.update_state(state='STARTED', meta={'progress': 20, 'message': 'Running alignment…'})

        builder = CommandBuilder.from_file(SCHEMAS_DIR / "clustalo.json")
        builder.set("outfmt", output_format)
        if parameters:
            builder.set_many(parameters, ignore_unknown=True)
        io_flags = {"-i": input_path, "-o": output_path}
        command = builder.build(io_flags=io_flags)
        if threads and threads > 1:
            command.extend(["--threads", str(threads)])
        logger.info(f"Running: {' '.join(command)}")

        result = subprocess.run(command, capture_output=True, text=True, timeout=280)

        if result.returncode != 0:
            friendly = parse_clustalo_error(result.stderr or result.stdout)
            project.task_status = 'FAILURE'
            project.error_message = friendly
            project.save(update_fields=['task_status', 'error_message'])
            if not skip_history:
                _safe_log_history('clustalo', 'tool_failed', 'failure', project, error_message=friendly)
            if workflow_run_id is not None:
                from workflows.tasks import run_next_workflow_step
                run_next_workflow_step.delay(workflow_run_id, step_index, project.id)
            raise RuntimeError(friendly)

        _finalize_success(project, output_path, output_format, input_path)

        if not skip_history:
            _safe_log_history('clustalo', 'tool_completed', 'success', project)

        if workflow_run_id is not None:
            from workflows.tasks import run_next_workflow_step
            run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

        self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'Alignment complete!'})
        return {'status': 'success', 'sequence_count': project.sequence_count}

    except SoftTimeLimitExceeded:
        logger.warning(f"clustalo task {self.request.id} timed out")
        if project:
            project.task_status = 'FAILURE'
            project.error_message = "Alignment timed out. Try with fewer sequences."
            project.save(update_fields=['task_status', 'error_message'])
        raise

    except Exception as e:
        logger.error(f"clustalo task error: {e}")
        if project:
            project.task_status = 'FAILURE'
            if not project.error_message:
                project.error_message = sanitize_exception_message(
                    e, fallback="Alignment failed. Please check your input file and try again.",
                )
            try:
                project.save(update_fields=['task_status', 'error_message'])
            except Exception as save_err:
                logger.warning(f"could not persist failure state: {save_err}")
        raise


def _run_msa_task(self, tool_id, project_id, output_path, output_format,
                  build_command, parameters, input_path,
                  skip_history=False, workflow_run_id=None, step_index=None,
                  capture_stdout_to_file=False, error_parser=None,
                  post_process=None):
    from .models import ClustalOmegaProject

    project = None
    try:
        project = ClustalOmegaProject.objects.get(id=project_id)
        project.task_status = 'STARTED'
        if project.tool != tool_id:
            project.tool = tool_id
            project.save(update_fields=['task_status', 'tool'])
        else:
            project.save(update_fields=['task_status'])

        self.update_state(state='STARTED', meta={'progress': 20, 'message': f'Running {tool_id}…'})

        builder = CommandBuilder.from_file(SCHEMAS_DIR / f"{tool_id}.json")
        if parameters:
            builder.set_many(parameters, ignore_unknown=True)
        command = build_command(builder)
        logger.info(f"Running: {' '.join(command)}")

        result = subprocess.run(command, capture_output=True, text=True, timeout=280)

        if result.returncode != 0:
            friendly = (
                error_parser(result.stderr)
                if error_parser
                else sanitize_exception_message(
                    RuntimeError(result.stderr or 'Unknown error'),
                    fallback="Alignment failed. Please check your input file and try again.",
                )
            )
            project.task_status = 'FAILURE'
            project.error_message = friendly
            project.save(update_fields=['task_status', 'error_message'])
            if not skip_history:
                _safe_log_history(tool_id, 'tool_failed', 'failure', project, error_message=friendly)
            if workflow_run_id is not None:
                from workflows.tasks import run_next_workflow_step
                run_next_workflow_step.delay(workflow_run_id, step_index, project.id)
            raise RuntimeError(friendly)

        if capture_stdout_to_file:
            with open(output_path, 'w') as f:
                f.write(result.stdout)

        if post_process:
            post_process(output_path)

        _finalize_success(project, output_path, output_format, input_path)

        if not skip_history:
            _safe_log_history(tool_id, 'tool_completed', 'success', project)

        if workflow_run_id is not None:
            from workflows.tasks import run_next_workflow_step
            run_next_workflow_step.delay(workflow_run_id, step_index, project.id)

        self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'Alignment complete!'})
        return {'status': 'success', 'sequence_count': project.sequence_count}

    except SoftTimeLimitExceeded:
        logger.warning(f"{tool_id} task {self.request.id} timed out")
        if project:
            project.task_status = 'FAILURE'
            project.error_message = "Alignment timed out. Try with fewer sequences."
            project.save(update_fields=['task_status', 'error_message'])
        raise

    except Exception as e:
        logger.error(f"{tool_id} task error: {e}")
        if project:
            project.task_status = 'FAILURE'
            if not project.error_message:
                project.error_message = sanitize_exception_message(
                    e, fallback="Alignment failed. Please check your input file and try again.",
                )
            try:
                project.save(update_fields=['task_status', 'error_message'])
            except Exception as save_err:
                logger.warning(f"could not persist failure state: {save_err}")
        raise


@shared_task(bind=True, soft_time_limit=300, time_limit=360)
def run_mafft(self, project_id, input_path, output_path, output_format,
              parameters=None, skip_history=False,
              workflow_run_id=None, step_index=None):
    params = dict(parameters or {})
    if output_format == 'clustal':
        params['clustalout'] = True

    def _build(builder):
        return builder.build(positional_args=[input_path])

    return _run_msa_task(
        self, 'mafft', project_id, output_path, output_format,
        _build, params, input_path,
        skip_history=skip_history,
        workflow_run_id=workflow_run_id,
        step_index=step_index,
        capture_stdout_to_file=True,
    )


@shared_task(bind=True, soft_time_limit=300, time_limit=360)
def run_muscle(self, project_id, input_path, output_path, output_format,
               parameters=None, skip_history=False,
               workflow_run_id=None, step_index=None):
    params = dict(parameters or {})
    use_super5 = params.pop('super5', False)
    input_flag = '-super5' if use_super5 else '-align'

    def _build(builder):
        return builder.build(io_flags={input_flag: input_path, "-output": output_path})

    return _run_msa_task(
        self, 'muscle', project_id, output_path, output_format,
        _build, params, input_path,
        skip_history=skip_history,
        workflow_run_id=workflow_run_id,
        step_index=step_index,
    )


@shared_task(bind=True, soft_time_limit=300, time_limit=360)
def run_kalign(self, project_id, input_path, output_path, output_format,
               parameters=None, skip_history=False,
               workflow_run_id=None, step_index=None):
    params = dict(parameters or {})
    kalign_format = {'fasta': 'fasta', 'clustal': 'clu', 'msf': 'msf'}.get(output_format)
    if kalign_format:
        params['format'] = kalign_format

    def _build(builder):
        return builder.build(io_flags={"-i": input_path, "-o": output_path})

    return _run_msa_task(
        self, 'kalign', project_id, output_path, output_format,
        _build, params, input_path,
        skip_history=skip_history,
        workflow_run_id=workflow_run_id,
        step_index=step_index,
        post_process=_fix_kalign_clustal if output_format == 'clustal' else None,
    )
