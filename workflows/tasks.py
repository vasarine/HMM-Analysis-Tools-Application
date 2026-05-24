"""
Celery tasks that run a workflow in the background so the browser
doesn't have to wait. Rebuilds the step inputs/outputs and calls the engine.
"""

import logging
import os

from celery import shared_task
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _rebuild_inputs_by_role(run):
    inputs_by_role = {}
    for ri in run.inputs.all():
        inputs_by_role[ri.role] = {
            'file': os.path.join(settings.MEDIA_ROOT, str(ri.file)) if ri.file else None,
            'accession': ri.accession or '',
            'data_type': ri.data_type,
        }
    return inputs_by_role


def _rebuild_prior_outputs(run):
    from .models import StepRun
    from .validation import infer_step_output

    prior_outputs = []
    prior_type = None
    completed = StepRun.objects.filter(
        workflow_run=run, status='SUCCESS',
    ).order_by('step_order_snapshot')

    for sr in completed:
        if sr.output_file:
            abs_path = os.path.join(settings.MEDIA_ROOT, str(sr.output_file))
            spec = {
                'tool_type': sr.tool_type_snapshot,
                'config': sr.step_config_snapshot or {},
            }
            out_type = infer_step_output(spec, prior_type)
            prior_type = out_type
            prior_outputs.append((out_type.value if out_type else '', abs_path, sr.step_order_snapshot))

    return prior_outputs, prior_type


def _resolve_project_output(project, tool_type_snapshot):
    field_map = {
        'hmmbuild':       'hmm_file',
        'hmmsearch':      'out_file',
        'hmmemit':        'output_file',
        'clustal_omega':  'output_alignment',
        'sequence_clean': 'output_fasta',
        'format_convert': 'output_file',
    }
    field = field_map.get(tool_type_snapshot)
    if not field:
        return None
    rel = getattr(project, field, None)
    if not rel:
        return None
    return os.path.join(settings.MEDIA_ROOT, str(rel))


def _get_project_error(project):
    err = (
        getattr(project, 'error_message', '') or
        getattr(project, 'result_text', '') or
        'Step failed.'
    )
    if err.startswith('ERROR:'):
        err = err[len('ERROR:'):].strip()
    return err or 'Step failed.'


def _fail_run(run, step_run, message):
    step_run.status = 'FAILURE'
    step_run.error_message = message
    step_run.save()
    run.status = 'FAILURE'
    run.error_message = message
    run.save(update_fields=['status', 'error_message'])


def _finish_run(run, output_file):
    if output_file and os.path.exists(output_file):
        run.output_file = os.path.relpath(output_file, settings.MEDIA_ROOT)
    run.status = 'SUCCESS'
    run.completed_at = timezone.now()
    run.save(update_fields=['status', 'completed_at', 'output_file'])


def _log_workflow_success(run):
    try:
        from users.models import UserActionHistory
        from users.history_utils import log_user_action
        ct = ContentType.objects.get_for_model(run)
        updated = UserActionHistory.objects.filter(
            action_type='workflow_run_started',
            content_type=ct, object_id=run.id,
        ).update(action_type='workflow_run_completed', status='success')
        if not updated and run.user:
            display_name = run.name or (run.workflow.name if run.workflow else 'Pipeline')
            log_user_action(run.user, 'workflow_run_completed', 'workflow', run,
                            display_name, status='success')
    except Exception as e:
        logger.error('History log error: %s', e)


def _log_workflow_failure(run, message):
    try:
        from users.models import UserActionHistory
        from users.history_utils import log_user_action
        ct = ContentType.objects.get_for_model(run)
        updated = UserActionHistory.objects.filter(
            action_type='workflow_run_started',
            content_type=ct, object_id=run.id,
        ).update(action_type='workflow_run_failed', status='failure',
                 error_message=message)
        if not updated and run.user:
            display_name = run.name or (run.workflow.name if run.workflow else 'Pipeline')
            log_user_action(run.user, 'workflow_run_failed', 'workflow', run,
                            display_name, status='failure', error_message=message)
    except Exception as e:
        logger.error('History log error: %s', e)


@shared_task(bind=True, max_retries=0)
def run_next_workflow_step(self, workflow_run_id, completed_step_index, project_id=None):
    from biologine_aplikacija.error_utils import sanitize_exception_message
    from .models import WorkflowRun, StepRun
    from .engine import dispatch_step, RunContext

    with transaction.atomic():
        try:
            run = WorkflowRun.objects.select_for_update().select_related('workflow').get(
                id=workflow_run_id
            )
        except WorkflowRun.DoesNotExist:
            return

        if run.status in ('SUCCESS', 'FAILURE'):
            return

        try:
            step_run = StepRun.objects.select_for_update().get(
                workflow_run=run,
                step_order_snapshot=completed_step_index,
            )
        except StepRun.DoesNotExist:
            logger.error(
                'run_next_workflow_step: StepRun not found for run=%s step=%s',
                workflow_run_id, completed_step_index,
            )
            return

        if step_run.status in ('SUCCESS', 'FAILURE'):
            return

    project = step_run.project
    if project is None:
        _fail_run(run, step_run, 'Internal error: tool project reference missing on step.')
        _log_workflow_failure(run, run.error_message)
        return

    task_status = getattr(project, 'task_status', None)

    if task_status == 'FAILURE':
        err = _get_project_error(project)
        _fail_run(run, step_run, err)
        _log_workflow_failure(run, err)
        return

    if task_status != 'SUCCESS':
        logger.warning(
            'run_next_workflow_step: project status is %s for run=%s step=%s; ignoring',
            task_status, workflow_run_id, completed_step_index,
        )
        return

    output_file = _resolve_project_output(project, step_run.tool_type_snapshot)

    with transaction.atomic():
        step_run = StepRun.objects.select_for_update().get(pk=step_run.pk)
        if step_run.status in ('SUCCESS', 'FAILURE'):
            return
        if output_file and os.path.exists(output_file):
            step_run.output_file = os.path.relpath(output_file, settings.MEDIA_ROOT)
        step_run.status = 'SUCCESS'
        step_run.save()

    if run.workflow:
        steps = list(run.workflow.steps.order_by('order'))
    else:
        steps = None

    if steps is not None:
        total_steps = len(steps)
    else:
        total_steps = StepRun.objects.filter(workflow_run=run).count()

    next_index = completed_step_index + 1

    if next_index >= total_steps:
        _finish_run(run, output_file)
        _log_workflow_success(run)
        return

    inputs_by_role = _rebuild_inputs_by_role(run)
    prior_outputs, prior_type = _rebuild_prior_outputs(run)
    current_file = output_file
    wf_name = run.name or (run.workflow.name if run.workflow else 'Pipeline')

    for idx in range(next_index, total_steps):
        if steps is not None:
            step = steps[idx]
            tool_type = step.tool_type
            config = step.config or {}
            try:
                step_run_next = StepRun.objects.get(workflow_run=run, step=step)
            except StepRun.DoesNotExist:
                logger.error('run_next_workflow_step: missing StepRun for step %s', idx)
                _fail_run(run, run, 'Internal error: missing StepRun record.')
                return
        else:
            try:
                step_run_next = StepRun.objects.get(
                    workflow_run=run, step_order_snapshot=idx
                )
            except StepRun.DoesNotExist:
                logger.error('run_next_workflow_step: missing snapshot StepRun at idx=%s', idx)
                return
            tool_type = step_run_next.tool_type_snapshot
            config = step_run_next.step_config_snapshot or {}
            step = None

        step_run_next.status = 'RUNNING'
        step_run_next.save(update_fields=['status'])
        run.current_step_index = idx
        run.save(update_fields=['current_step_index'])

        ctx = RunContext(
            step_index=idx,
            inputs_by_role=inputs_by_role,
            prior_outputs=list(prior_outputs),
        )

        try:
            next_output_file, next_project = dispatch_step(
                tool_type, current_file, run.user, config,
                wf_name=wf_name, ctx=ctx,
                workflow_run_id=workflow_run_id, step_index=idx,
            )
        except Exception as exc:
            friendly = sanitize_exception_message(
                exc,
                fallback=f"Step '{tool_type}' failed. Please check your inputs and try again.",
            )
            step_run_next.status = 'FAILURE'
            step_run_next.error_message = friendly
            step_run_next.save()
            run.status = 'FAILURE'
            run.error_message = friendly
            run.save(update_fields=['status', 'error_message'])
            _log_workflow_failure(run, friendly)
            return

        if next_project:
            ct = ContentType.objects.get_for_model(next_project)
            step_run_next.content_type = ct
            step_run_next.object_id = next_project.pk
            step_run_next.save(update_fields=['content_type', 'object_id'])

        if next_output_file is None:
            return

        if next_output_file and os.path.exists(next_output_file):
            step_run_next.output_file = os.path.relpath(next_output_file, settings.MEDIA_ROOT)
            current_file = next_output_file
            spec = {'tool_type': tool_type, 'config': config}
            from .validation import infer_step_output
            out_type = infer_step_output(spec, prior_type)
            prior_type = out_type
            prior_outputs.append((out_type.value if out_type else '', next_output_file, idx))

        step_run_next.status = 'SUCCESS'
        step_run_next.save()

    _finish_run(run, current_file)
    _log_workflow_success(run)


@shared_task(bind=True)
def run_workflow_engine(self, workflow_run_id):
    from biologine_aplikacija.error_utils import sanitize_exception_message
    from .models import WorkflowRun, StepRun
    from .engine import dispatch_step, RunContext
    from .validation import infer_step_output

    try:
        run = WorkflowRun.objects.select_related('workflow').get(id=workflow_run_id)
    except WorkflowRun.DoesNotExist:
        return

    run.status = 'RUNNING'
    run.save(update_fields=['status'])

    if not run.workflow:
        run.status = 'FAILURE'
        run.error_message = 'Workflow was deleted before run could start.'
        run.save(update_fields=['status', 'error_message'])
        return

    steps = list(run.workflow.steps.order_by('order'))

    for step in steps:
        StepRun.objects.get_or_create(
            workflow_run=run,
            step=step,
            defaults={
                'status': 'PENDING',
                'tool_type_snapshot': step.tool_type,
                'step_order_snapshot': step.order,
                'step_config_snapshot': step.config or {},
            },
        )

    inputs_by_role = _rebuild_inputs_by_role(run)

    primary = inputs_by_role.get('primary') or {}
    if primary.get('file') and os.path.exists(primary['file']):
        current_file = primary['file']
    elif run.input_file:
        legacy = os.path.join(settings.MEDIA_ROOT, str(run.input_file))
        current_file = legacy if os.path.exists(legacy) else None
    else:
        current_file = None

    prior_outputs = []
    prior_type = None

    for idx, step in enumerate(steps):
        step_run = StepRun.objects.get(workflow_run=run, step=step)
        step_run.status = 'RUNNING'
        step_run.save(update_fields=['status'])
        run.current_step_index = idx
        run.save(update_fields=['current_step_index'])

        ctx = RunContext(
            step_index=idx,
            inputs_by_role=inputs_by_role,
            prior_outputs=list(prior_outputs),
        )

        try:
            output_file, project = dispatch_step(
                step.tool_type, current_file, run.user, step.config or {},
                wf_name=run.workflow.name,
                ctx=ctx,
                workflow_run_id=run.id,
                step_index=idx,
            )
        except Exception as exc:
            friendly = sanitize_exception_message(
                exc,
                fallback=f"Step '{step.tool_type}' failed. Please check your inputs and try again.",
            )
            step_run.status = 'FAILURE'
            step_run.error_message = friendly
            step_run.save()
            run.status = 'FAILURE'
            run.error_message = friendly
            run.save(update_fields=['status', 'error_message'])
            _log_workflow_failure(run, friendly)
            return

        if project:
            ct = ContentType.objects.get_for_model(project)
            step_run.content_type = ct
            step_run.object_id = project.pk
            step_run.save(update_fields=['content_type', 'object_id'])

        if output_file is None:
            return

        if output_file and os.path.exists(output_file):
            step_run.output_file = os.path.relpath(output_file, settings.MEDIA_ROOT)
            current_file = output_file
            spec = {'tool_type': step.tool_type, 'config': step.config or {}}
            out_type = infer_step_output(spec, prior_type)
            prior_type = out_type
            prior_outputs.append((out_type.value if out_type else '', output_file, idx))

        step_run.status = 'SUCCESS'
        step_run.save()

    if current_file and os.path.exists(current_file):
        run.output_file = os.path.relpath(current_file, settings.MEDIA_ROOT)
    run.status = 'SUCCESS'
    run.completed_at = timezone.now()
    run.save(update_fields=['status', 'completed_at', 'output_file'])
    _log_workflow_success(run)
