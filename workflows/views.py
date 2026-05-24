import io
import json
import os
import uuid
import zipfile

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, Http404, FileResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.conf import settings

from .models import Workflow, WorkflowStep, WorkflowRun, WorkflowRunInput, StepRun, TOOL_META
from .tasks import run_workflow_engine
from . import validation as wf_validation
from biologine_aplikacija.parameter_builder import build_step_parameter_overrides

PRESET_TEMPLATES = [
    {
        'name': 'Raw FASTA → HMM Profile',
        'description': (
            'Full pipeline for unaligned sequences: validates the file, '
            'removes stop characters and duplicate IDs, aligns sequences with MSA, '
            'and builds an HMM profile ready for searching.'
        ),
        'steps': [
            {'tool_type': 'fasta_validate', 'config': {}},
            {'tool_type': 'sequence_clean',  'config': {'remove_stop_chars': True, 'remove_duplicate_ids': True}},
            {'tool_type': 'clustal_omega',   'config': {'output_format': 'stockholm'}},
            {'tool_type': 'hmmbuild',        'config': {}},
        ],
    },
    {
        'name': 'Aligned FASTA → HMM Profile',
        'description': (
            'For sequences that are already aligned: validates, converts the '
            'alignment to Stockholm format, and builds an HMM profile.'
        ),
        'steps': [
            {'tool_type': 'fasta_validate',  'config': {}},
            {'tool_type': 'format_convert',  'config': {'output_format': 'stockholm'}},
            {'tool_type': 'hmmbuild',        'config': {}},
        ],
    },
    {
        'name': 'Clean & Align',
        'description': (
            'Prepares raw sequences for downstream analysis: validates, '
            'cleans (removes gaps, stop characters, duplicates), '
            'and produces a multiple sequence alignment.'
        ),
        'steps': [
            {'tool_type': 'fasta_validate', 'config': {}},
            {'tool_type': 'sequence_clean',  'config': {'remove_gaps': True, 'remove_stop_chars': True, 'remove_duplicate_ids': True}},
            {'tool_type': 'clustal_omega',   'config': {'output_format': 'stockholm'}},
        ],
    },
]


def _ensure_templates():
    if Workflow.objects.filter(is_template=True).exists():
        return
    for tmpl in PRESET_TEMPLATES:
        wf = Workflow.objects.create(
            user=None,
            name=tmpl['name'],
            description=tmpl['description'],
            is_template=True,
        )
        for i, step in enumerate(tmpl['steps']):
            WorkflowStep.objects.create(
                workflow=wf,
                order=i,
                tool_type=step['tool_type'],
                config=step['config'],
            )


TOOL_LABELS = {k: v['label'] for k, v in TOOL_META.items()}
TOOL_COLORS = {k: v['color'] for k, v in TOOL_META.items()}


class _SnapshotStep:
    def __init__(self, tool_type, order, config=None):
        self.id = None
        self.tool_type = tool_type
        self.order = order
        self.config = config or {}

    @property
    def label(self):
        return TOOL_META.get(self.tool_type, {}).get('label', self.tool_type)

    @property
    def color(self):
        return TOOL_META.get(self.tool_type, {}).get('color', '#94a3b8')

    def get_tool_type_display(self):
        from .models import TOOL_TYPES
        return dict(TOOL_TYPES).get(self.tool_type, self.tool_type)

_HMM_SOURCE_OPTIONS = [
    ('previous_step',       'Use HMM from previous step'),
    ('upload',              'Upload HMM file at run time'),
    ('external_accession',  'Pfam or InterPro ID (PF##### / IPR######)'),
]
_DB_MODE_OPTIONS = [
    ('previous_step', 'From previous step'),
    ('upload',        'Upload FASTA database'),
]
_MSA_TOOL_OPTIONS = [
    ('clustalo', 'Clustal Omega'),
    ('mafft',    'MAFFT'),
    ('muscle',   'MUSCLE'),
    ('kalign',   'Kalign'),
]

TOOL_CONFIG_FIELDS = {
    'fasta_validate': [],
    'sequence_clean': [
        {'name': 'remove_gaps',           'label': 'Remove gap characters (–)',          'type': 'bool'},
        {'name': 'remove_stop_chars',     'label': 'Remove stop characters (*)',          'type': 'bool'},
        {'name': 'uppercase',             'label': 'Convert to uppercase',                'type': 'bool'},
        {'name': 'remove_duplicate_ids',  'label': 'Fix duplicate sequence names',        'type': 'bool'},
        {'name': 'remove_duplicate_seqs', 'label': 'Remove fully duplicate sequences',    'type': 'bool'},
        {'name': 'min_length',            'label': 'Minimum sequence length',             'type': 'int'},
        {'name': 'max_length',            'label': 'Maximum sequence length',             'type': 'int'},
    ],
    'clustal_omega': [
        {'name': 'msa_tool', 'label': 'MSA tool', 'type': 'select', 'options': _MSA_TOOL_OPTIONS},
        {'name': 'output_format', 'label': 'Output format', 'type': 'select',
         'options': [('stockholm', 'Stockholm (.sto)'), ('clustal', 'Clustal (.aln)'), ('fasta', 'Aligned FASTA (.fasta)')]},
    ],
    'format_convert': [
        {'name': 'output_format', 'label': 'Output format', 'type': 'select',
         'options': [('stockholm', 'Stockholm (.sto)'), ('fasta', 'FASTA (.fasta)'), ('clustal', 'Clustal (.aln)')]},
    ],
    'hmmbuild': [
        {'name': 'save_annotated_msa', 'label': 'Also save annotated source MSA (-O)', 'type': 'bool'},
    ],
    'hmmsearch': [
        {'name': 'db_mode',    'label': 'FASTA database', 'type': 'select', 'options': _DB_MODE_OPTIONS},
        {'name': 'save_hits_msa', 'label': 'Also save hits alignment (-A)', 'type': 'bool'},
        {'name': 'save_pfamtbl', 'label': 'Also save Pfam-format table (--pfamtblout)', 'type': 'bool'},
    ],
    'hmmemit': [
        {'name': 'hmm_source', 'label': 'HMM source', 'type': 'select', 'options': _HMM_SOURCE_OPTIONS},
        {'name': 'num_seqs',   'label': 'Number of sequences', 'type': 'int'},
    ],
}

TOOL_DEFAULTS = {
    'fasta_validate': {},
    'sequence_clean': {
        'remove_gaps': False, 'remove_stop_chars': False, 'uppercase': False,
        'remove_duplicate_ids': False, 'remove_duplicate_seqs': False,
        'min_length': None, 'max_length': None,
    },
    'clustal_omega':  {'msa_tool': 'clustalo', 'output_format': 'stockholm'},
    'format_convert': {'output_format': 'stockholm'},
    'hmmbuild':       {'save_annotated_msa': False},
    'hmmsearch':      {'hmm_source': 'external_accession', 'db_mode': 'upload',
                       'save_hits_msa': False, 'save_pfamtbl': False},
    'hmmemit':        {'hmm_source': 'external_accession', 'num_seqs': 10},
}


from .tool_registry import TOOL_SCHEMA_ID


def _schema_param_to_descriptor(param):
    ptype = param['type']
    if ptype == 'file':
        return None
    out = {
        'name': param['name'],
        'label': param.get('label', param['name']),
        'default': param.get('default'),
        'section': param.get('section'),
        'help_text': param.get('help_text', ''),
        'depends_on': param.get('depends_on'),
    }
    if ptype == 'boolean':
        out['type'] = 'bool'
    elif ptype in ('number', 'float'):
        out['type'] = 'number'
        out['number_kind'] = 'int' if ptype == 'number' else 'float'
        if 'min' in param:
            out['min'] = param['min']
        if 'max' in param:
            out['max'] = param['max']
    elif ptype == 'select':
        opts = param.get('options', [])
        out['type'] = 'select'
        out['options'] = [['', '—']] + [[o, o] for o in opts]
    elif ptype == 'radio_group':
        out['type'] = 'select'
        out['options'] = [[o['value'], o.get('label', o['value'])] for o in param.get('options', [])]
    elif ptype == 'string':
        out['type'] = 'text'
    else:
        return None
    return out


def _schema_to_descriptors(schema_id):
    from biologine_aplikacija.parameter_builder import load_schema
    try:
        schema = load_schema(schema_id)
    except Exception:
        return []
    descriptors = []
    for p in schema.get('parameters', []):
        if p.get('ui_hidden'):
            continue
        d = _schema_param_to_descriptor(p)
        if d:
            descriptors.append(d)
    return descriptors


def _build_tool_descriptors():
    from .tool_registry import MSA_TOOL_SCHEMAS

    descriptors = {}
    for tool_type in TOOL_META.keys():
        routing = TOOL_CONFIG_FIELDS.get(tool_type, [])

        if tool_type == 'clustal_omega':
            variants = {
                msa_tool: _schema_to_descriptors(schema_id)
                for msa_tool, schema_id in MSA_TOOL_SCHEMAS.items()
            }
            descriptors[tool_type] = {
                'routing': routing,
                'parameter_variants': variants,
                'variant_field': 'msa_tool',
                'parameters': variants.get('clustalo', []),
            }
            continue

        schema_id = TOOL_SCHEMA_ID.get(tool_type)
        params = _schema_to_descriptors(schema_id) if schema_id else []
        descriptors[tool_type] = {'routing': routing, 'parameters': params}
    return descriptors


def _workflow_to_json(workflow):
    steps = [
        {'order': s.order, 'tool_type': s.tool_type, 'config': dict(s.config or {})}
        for s in workflow.steps.order_by('order')
    ]
    _normalize_hmmsearch_hmm_source(steps)
    return {
        'id': workflow.id,
        'name': workflow.name,
        'description': workflow.description,
        'steps': steps,
    }


def workflow_list(request):
    _ensure_templates()
    templates = Workflow.objects.filter(is_template=True).prefetch_related('steps')
    user_workflows = []
    if request.user.is_authenticated:
        user_workflows = Workflow.objects.filter(
            user=request.user if request.user.is_authenticated else None, is_template=False
        ).prefetch_related('steps')

    return render(request, 'workflows/workflow_list.html', {
        'templates': templates,
        'user_workflows': user_workflows,
        'tool_meta': TOOL_META,
    })


def workflow_builder(request, workflow_id=None):
    workflow = None
    initial_json = None

    if workflow_id:
        workflow = get_object_or_404(Workflow, id=workflow_id)
        if workflow.user and workflow.user != request.user:
            raise Http404
        initial_json = json.dumps(_workflow_to_json(workflow))
    elif request.GET.get('template'):
        tmpl = get_object_or_404(Workflow, id=request.GET['template'], is_template=True)
        data = _workflow_to_json(tmpl)
        data['id'] = None
        data['name'] = tmpl.name
        initial_json = json.dumps(data)

    starting_tools = [
        wf_validation.serialize_tool_entry(t)
        for t in wf_validation.get_starting_tools()
    ]
    from biologine_aplikacija.parameter_builder import load_schema
    msa_supported_formats = {}
    for msa_id in ('clustalo', 'mafft', 'muscle', 'kalign'):
        try:
            s = load_schema(msa_id)
            msa_supported_formats[msa_id] = s.get('supported_output_formats', ['fasta'])
        except Exception:
            msa_supported_formats[msa_id] = ['fasta']

    return render(request, 'workflows/workflow_builder.html', {
        'workflow': workflow,
        'initial_json': initial_json or 'null',
        'tool_meta': TOOL_META,
        'tool_types': list(TOOL_META.keys()),
        'tool_config_fields': json.dumps(TOOL_CONFIG_FIELDS),
        'tool_descriptors': json.dumps(_build_tool_descriptors()),
        'tool_defaults': json.dumps(TOOL_DEFAULTS),
        'tool_labels': json.dumps(TOOL_LABELS),
        'tool_colors': json.dumps(TOOL_COLORS),
        'starting_tools_json': json.dumps(starting_tools),
        'msa_supported_formats': json.dumps(msa_supported_formats),
    })


_LEGACY_ACCESSION_SOURCES = {'pfam', 'interpro'}


def _normalize_hmmsearch_hmm_source(steps_data):
    prev_tool = None
    for s in steps_data:
        tool = s.get('tool_type')
        if tool in ('hmmsearch', 'hmmemit'):
            cfg = s.setdefault('config', {})
            if cfg.get('hmm_source') in _LEGACY_ACCESSION_SOURCES:
                cfg['hmm_source'] = 'external_accession'
        if tool == 'hmmsearch':
            cfg = s.setdefault('config', {})
            if prev_tool == 'hmmbuild':
                cfg['hmm_source'] = 'previous_step'
                cfg['db_mode'] = 'upload'
            elif cfg.get('hmm_source') == 'previous_step':
                cfg['hmm_source'] = 'external_accession'
        prev_tool = tool


@require_POST
def save_workflow(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (data.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'Workflow name is required.'}, status=400)

    steps_data = data.get('steps', [])
    if not steps_data:
        return JsonResponse({'error': 'Add at least one step.'}, status=400)

    _normalize_hmmsearch_hmm_source(steps_data)

    result = wf_validation.validate_workflow(steps_data)
    if not result.is_valid:
        return JsonResponse({
            'error': 'This workflow is not biologically valid.',
            'state': wf_validation.builder_state_payload(steps_data),
        }, status=400)

    wf_id = data.get('id')
    if wf_id:
        try:
            workflow = Workflow.objects.get(id=wf_id, user=request.user if request.user.is_authenticated else None)
            workflow.name = name
            workflow.description = data.get('description', '')
            workflow.save(update_fields=['name', 'description', 'updated_at'])
            workflow.steps.all().delete()
        except Workflow.DoesNotExist:
            wf_id = None

    if not wf_id:
        workflow = Workflow.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=name,
            description=data.get('description', ''),
            is_template=False,
        )

    for i, step in enumerate(steps_data):
        WorkflowStep.objects.create(
            workflow=workflow,
            order=i,
            tool_type=step['tool_type'],
            config=step.get('config', {}),
        )

    from django.urls import reverse
    return JsonResponse({
        'id': workflow.id,
        'redirect_url': reverse('workflow_run_start', args=[workflow.id]),
    })


@require_POST
def builder_state(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    steps = data.get('steps') or []
    return JsonResponse(wf_validation.builder_state_payload(steps))


def _save_uploaded(file_obj, subdir, role) -> str:
    dest_dir = os.path.join(settings.MEDIA_ROOT, subdir)
    os.makedirs(dest_dir, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    ext = os.path.splitext(file_obj.name)[1].lower() or '.fasta'
    safe = ''.join(c for c in os.path.splitext(file_obj.name)[0] if c.isalnum() or c in '-_ ')[:40].strip().replace(' ', '_')
    name = f"{safe}_{uid}{ext}" if safe else f"{role}_{uid}{ext}"
    rel_path = f'{subdir}/{name}'
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    with open(abs_path, 'wb+') as dest:
        for chunk in file_obj.chunks():
            dest.write(chunk)
    return rel_path


def _save_pasted_fasta(text, subdir, role) -> str:
    dest_dir = os.path.join(settings.MEDIA_ROOT, subdir)
    os.makedirs(dest_dir, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    rel_path = f'{subdir}/{role}_pasted_{uid}.fasta'
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    content = text if text.endswith('\n') else text + '\n'
    with open(abs_path, 'w', encoding='utf-8') as dest:
        dest.write(content)
    return rel_path


def workflow_run_start(request, workflow_id):
    workflow = get_object_or_404(Workflow, id=workflow_id)

    steps_data = [
        {'tool_type': s.tool_type, 'config': s.config}
        for s in workflow.steps.order_by('order')
    ]
    external_inputs = wf_validation.external_inputs_for_workflow(steps_data)
    serialized_inputs = [wf_validation.serialize_external_input(e) for e in external_inputs]

    if request.method == 'POST':
        result = wf_validation.validate_workflow(steps_data)
        if not result.is_valid:
            return render(request, 'workflows/run_start.html', {
                'workflow': workflow,
                'external_inputs': serialized_inputs,
                'error': 'This workflow is no longer valid. Please open the builder to fix it.',
            })

        run_name = (request.POST.get('run_name') or '').strip()
        run = WorkflowRun.objects.create(
            workflow=workflow,
            name=run_name,
            user=request.user if request.user.is_authenticated else None,
            status='PENDING',
        )

        for ext in external_inputs:
            field_name = f'input__{ext.role}'
            paste_name = f'paste__{ext.role}'

            if ext.data_type == wf_validation.DataType.HMM_PROFILE:
                hmm_file = request.FILES.get(f'hmm_file__{ext.role}')
                accession = (request.POST.get(field_name) or '').strip()
                if hmm_file:
                    rel_path = _save_uploaded(hmm_file, 'workflows/inputs', ext.role)
                    WorkflowRunInput.objects.create(
                        workflow_run=run,
                        role=ext.role,
                        step_index=ext.step_index,
                        data_type=ext.data_type.value,
                        file=rel_path,
                    )
                elif accession:
                    WorkflowRunInput.objects.create(
                        workflow_run=run,
                        role=ext.role,
                        step_index=ext.step_index,
                        data_type=ext.data_type.value,
                        accession=accession.upper(),
                    )
                else:
                    run.delete()
                    return render(request, 'workflows/run_start.html', {
                        'workflow': workflow,
                        'external_inputs': serialized_inputs,
                        'error': f'{ext.label} is required - enter a Pfam/InterPro ID or upload a .hmm file.',
                    })
                continue

            uploaded = request.FILES.get(field_name)
            pasted = (request.POST.get(paste_name) or '').strip()

            if not uploaded and not pasted:
                run.delete()
                return render(request, 'workflows/run_start.html', {
                    'workflow': workflow,
                    'external_inputs': serialized_inputs,
                    'error': f'{ext.label} is required.',
                })

            if pasted and not uploaded:
                if not pasted.lstrip().startswith('>'):
                    run.delete()
                    return render(request, 'workflows/run_start.html', {
                        'workflow': workflow,
                        'external_inputs': serialized_inputs,
                        'error': f'{ext.label}: pasted content must start with a > header line.',
                    })
                rel_path = _save_pasted_fasta(pasted, 'workflows/inputs', ext.role)
            else:
                rel_path = _save_uploaded(uploaded, 'workflows/inputs', ext.role)

            WorkflowRunInput.objects.create(
                workflow_run=run,
                role=ext.role,
                step_index=ext.step_index,
                data_type=ext.data_type.value,
                file=rel_path,
            )

            if ext.role == 'primary':
                run.input_file = rel_path
                run.save(update_fields=['input_file'])

        task = run_workflow_engine.delay(run.id)
        run.task_id = task.id
        run.save(update_fields=['task_id'])

        from users.history_utils import log_user_action
        display_name = run_name or workflow.name
        log_user_action(
            request.user if request.user.is_authenticated else None,
            'workflow_run_started', 'workflow', run,
            display_name, status='success', request=request,
        )

        return redirect('workflow_run_status', run_id=run.id)

    return render(request, 'workflows/run_start.html', {
        'workflow': workflow,
        'external_inputs': serialized_inputs,
    })


@require_POST
def workflow_run_retry(request, run_id):
    original = get_object_or_404(WorkflowRun, id=run_id)

    if request.user.is_authenticated and original.user and original.user != request.user:
        raise Http404("Run not found")

    if not original.workflow:
        return redirect('workflow_run_status', run_id=run_id)

    import shutil
    upload_dir = os.path.join(settings.MEDIA_ROOT, 'workflows', 'inputs')
    os.makedirs(upload_dir, exist_ok=True)

    retry_name = (original.name or original.workflow.name) + ' (retry)'
    new_run = WorkflowRun.objects.create(
        workflow=original.workflow,
        name=retry_name,
        user=request.user if request.user.is_authenticated else None,
        status='PENDING',
    )

    original_inputs = list(original.inputs.all())
    if original_inputs:
        for ri in original_inputs:
            if ri.file:
                src = os.path.join(settings.MEDIA_ROOT, str(ri.file))
                if not os.path.exists(src):
                    new_run.delete()
                    return render(request, 'workflows/run_status.html', {
                        'run': original,
                        'error': 'An original input file is no longer available. Please start a new run.',
                    })
                uid = uuid.uuid4().hex[:8]
                base_no_ext, ext = os.path.splitext(os.path.basename(src))
                retry_fn = f'{base_no_ext}_retry_{uid}{ext or ".dat"}'
                shutil.copy2(src, os.path.join(upload_dir, retry_fn))
                WorkflowRunInput.objects.create(
                    workflow_run=new_run, role=ri.role, step_index=ri.step_index,
                    data_type=ri.data_type, file=f'workflows/inputs/{retry_fn}',
                )
                if ri.role == 'primary':
                    new_run.input_file = f'workflows/inputs/{retry_fn}'
                    new_run.save(update_fields=['input_file'])
            else:
                WorkflowRunInput.objects.create(
                    workflow_run=new_run, role=ri.role, step_index=ri.step_index,
                    data_type=ri.data_type, accession=ri.accession,
                )
    elif original.input_file:
        src = os.path.join(settings.MEDIA_ROOT, str(original.input_file))
        if not os.path.exists(src):
            new_run.delete()
            return render(request, 'workflows/run_status.html', {
                'run': original,
                'error': 'The original input file is no longer available. Please start a new run.',
            })
        uid = uuid.uuid4().hex[:8]
        base_no_ext, ext = os.path.splitext(os.path.basename(src))
        retry_fn = f'{base_no_ext}_retry_{uid}{ext or ".fasta"}'
        legacy_dir = os.path.join(settings.MEDIA_ROOT, 'workflows', 'input')
        os.makedirs(legacy_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(legacy_dir, retry_fn))
        new_run.input_file = f'workflows/input/{retry_fn}'
        new_run.save(update_fields=['input_file'])
    else:
        new_run.delete()
        return render(request, 'workflows/run_status.html', {
            'run': original,
            'error': 'No inputs from the original run could be located.',
        })

    task = run_workflow_engine.delay(new_run.id)
    new_run.task_id = task.id
    new_run.save(update_fields=['task_id'])

    from users.history_utils import log_user_action
    log_user_action(
        request.user if request.user.is_authenticated else None,
        'workflow_run_started', 'workflow', new_run,
        retry_name, status='success', request=request,
    )

    return redirect('workflow_run_status', run_id=new_run.id)


def _read_preview(filepath, max_lines=50, max_chars=10000):
    from biologine_aplikacija.preview_utils import read_file_preview
    preview = read_file_preview(filepath, max_lines=max_lines, max_chars=max_chars)
    if not preview.text:
        return None
    return f"{preview.text}\n\n{preview.note}" if preview.note else preview.text


def _read_full(filepath):
    from biologine_aplikacija.preview_utils import read_file_full
    preview = read_file_full(filepath)
    return preview.text if preview.text else None


_STEP_DESCRIPTIONS = {
    'fasta_validate': 'Validated sequences',
    'sequence_clean': 'Cleaned sequences',
    'clustal_omega':  'Multiple sequence alignment',
    'format_convert': 'Converted alignment',
    'hmmbuild':       'HMM profile',
    'hmmsearch':      'HMMSEARCH results',
    'hmmemit':        'Emitted FASTA sequences',
}


def _file_preview_for_field(file_field, max_lines=500, max_chars=100000):
    if not file_field:
        return None, '', ''
    fpath = os.path.join(settings.MEDIA_ROOT, str(file_field))
    if not os.path.exists(fpath):
        return None, '', ''
    return _read_preview(fpath, max_lines=max_lines, max_chars=max_chars), os.path.basename(str(file_field)), fpath


def _hmmsearch_output_items(project):
    if not project:
        return []
    files = [
        ('results.out', 'Full HMMSEARCH report', getattr(project, 'out_file', None)),
        ('results.tblout', 'Target table', getattr(project, 'tblout_file', None)),
        ('results.domtbl', 'Domain table', getattr(project, 'domtbl_file', None)),
    ]
    items = []
    for label, description, file_field in files:
        preview, filename, fpath = _file_preview_for_field(file_field)
        if not fpath:
            continue
        try:
            download_url = file_field.url
        except Exception:
            download_url = ''
        items.append({
            'label': label,
            'description': description,
            'filename': filename or label,
            'preview': preview,
            'download_url': download_url,
            'path': fpath,
        })
    return items


def _optional_output_items(project, specs):
    if not project:
        return []
    items = []
    for field_name, label in specs:
        file_field = getattr(project, field_name, None)
        preview, filename, fpath = _file_preview_for_field(file_field)
        if not fpath:
            continue
        try:
            download_url = file_field.url
        except Exception:
            download_url = ''
        items.append({
            'label': label,
            'filename': filename or label,
            'preview': preview,
            'download_url': download_url,
            'path': fpath,
        })
    return items


def _build_hmmsearch_data(project, step_order):
    if not project:
        return None

    def _text_preview(text, max_lines=50):
        if not text:
            return ''
        from biologine_aplikacija.preview_utils import read_text_preview
        p = read_text_preview(text, max_lines=max_lines, max_chars=500000)
        note = f'\n\n{p.note}' if p.note else ''
        return f'{p.text}{note}' if p.text else ''

    def _field_url(field):
        try:
            return field.url if field else ''
        except Exception:
            return ''

    result_text = getattr(project, 'result_text', None) or ''
    tblout_text = getattr(project, 'tblout_text', None) or ''
    domtbl_text = getattr(project, 'domtbl_text', None) or ''

    target_hits = sum(
        1 for ln in tblout_text.splitlines()
        if ln.strip() and not ln.startswith('#')
    )

    return {
        'hmm_source':       getattr(project, 'hmm_source', None),
        'external_hmm_id':  getattr(project, 'external_hmm_id', None),
        'target_hits':      target_hits,
        'out_preview':      _text_preview(result_text, 50),
        'tblout_preview':   _text_preview(tblout_text, 30),
        'domtbl_preview':   _text_preview(domtbl_text, 30),
        'out_download_url':    _field_url(getattr(project, 'out_file', None)),
        'tblout_download_url': _field_url(getattr(project, 'tblout_file', None)),
        'domtbl_download_url': _field_url(getattr(project, 'domtbl_file', None)),
        'card_id_out': f'wf-out-{step_order}',
        'card_id_tbl': f'wf-tbl-{step_order}',
        'card_id_dom': f'wf-dom-{step_order}',
    }


def workflow_run_status(request, run_id):
    run = get_object_or_404(WorkflowRun, id=run_id)
    if not run.can_view(request.user):
        raise Http404('Workflow run not found.')

    if run.workflow:
        steps = list(run.workflow.steps.order_by('order'))
        step_run_by_step_id = {sr.step_id: sr for sr in run.step_runs.select_related('step').all()}
        step_pairs = [(step, step_run_by_step_id.get(step.id)) for step in steps]
    else:
        ordered_srs = list(run.step_runs.order_by('step_order_snapshot'))
        step_pairs = [
            (_SnapshotStep(sr.tool_type_snapshot, sr.step_order_snapshot), sr)
            for sr in ordered_srs
        ]
        steps = [pair[0] for pair in step_pairs]

    step_data = []
    step_files = []
    for step, sr in step_pairs:
        status = sr.status if sr else 'PENDING'
        error = sr.error_message if sr else ''

        preview = None
        fname = ''
        primary_download_url = ''
        extra_previews = []
        hmmsearch_items = []
        fasta_stats = None
        hmmsearch_data = None
        if sr and step.tool_type == 'hmmsearch':
            hmmsearch_items = _hmmsearch_output_items(getattr(sr, 'project', None))
            if hmmsearch_items:
                primary = hmmsearch_items[0]
                preview = primary['preview']
                fname = primary['filename']
                primary_download_url = primary['download_url']
                extra_previews = hmmsearch_items[1:]
            hmmsearch_data = _build_hmmsearch_data(getattr(sr, 'project', None), step.order)
            if hmmsearch_data:
                preview = None
                extra_previews = []
            extra_previews += _optional_output_items(
                getattr(sr, 'project', None),
                [
                    ('hits_msa_file', 'Hits alignment (-A)'),
                    ('pfamtbl_file', 'Pfam-format table (--pfamtblout)'),
                ],
            )
        if sr and step.tool_type == 'hmmbuild':
            extra_previews += _optional_output_items(
                getattr(sr, 'project', None),
                [
                    ('annotated_msa_file', 'Annotated MSA (-O)'),
                ],
            )
        if sr and step.tool_type == 'fasta_validate':
            fasta_project = getattr(sr, 'project', None)
            if fasta_project is not None:
                fasta_stats = getattr(fasta_project, 'stats', None)
        if sr and sr.output_file and not preview:
            preview, fname, _ = _file_preview_for_field(sr.output_file)
        if fasta_stats:
            preview = None

        desc = _STEP_DESCRIPTIONS.get(step.tool_type, step.label)
        if step.tool_type == 'format_convert' and step.config.get('output_format'):
            desc = f"Converted alignment ({step.config['output_format'].title()})"
        elif step.tool_type == 'clustal_omega' and step.config.get('output_format'):
            msa_tool = step.config.get('msa_tool') or 'clustalo'
            from biologine_aplikacija.parameter_builder import load_schema
            try:
                supported = load_schema(msa_tool).get('supported_output_formats', ['fasta'])
            except Exception:
                supported = ['fasta']
            actual_fmt = step.config['output_format'] if step.config['output_format'] in supported else supported[0]
            desc = f"Multiple sequence alignment ({actual_fmt.title()})"

        item = {
            'step': step,
            'step_run': sr,
            'status': status,
            'error': error,
            'output_filename': fname,
            'preview': preview,
            'fasta_stats': fasta_stats,
            'hmmsearch_data': hmmsearch_data,
            'description': desc,
        }
        step_data.append(item)

        step_overrides = build_step_parameter_overrides(sr)
        if sr and sr.output_file and os.path.exists(os.path.join(settings.MEDIA_ROOT, str(sr.output_file))):
            step_files.append({
                'label': desc,
                'short_label': step.label,
                'color': step.color,
                'order': step.order,
                'filename': fname,
                'preview': preview,
                'fasta_stats': fasta_stats,
                'hmmsearch_data': hmmsearch_data,
                'download_url': primary_download_url,
                'extra_previews': extra_previews,
                'parameter_overrides': step_overrides,
            })
        elif hmmsearch_items:
            step_files.append({
                'label': desc,
                'short_label': step.label,
                'color': step.color,
                'order': step.order,
                'filename': fname,
                'preview': preview,
                'fasta_stats': fasta_stats,
                'hmmsearch_data': hmmsearch_data,
                'download_url': primary_download_url,
                'extra_previews': extra_previews,
                'parameter_overrides': step_overrides,
            })

    final_filename = None
    output_preview = None
    last_step, last_sr = step_pairs[-1] if step_pairs else (None, None)
    if last_step and last_step.tool_type == 'hmmsearch' and last_sr:
        hmmsearch_items = _hmmsearch_output_items(getattr(last_sr, 'project', None))
        if hmmsearch_items:
            primary = hmmsearch_items[0]
            final_filename = primary['filename']
            fpath = primary.get('path', '')
            output_preview = _read_full(fpath) if fpath else primary['preview']
    if run.output_file and not output_preview:
        fpath = os.path.join(settings.MEDIA_ROOT, str(run.output_file))
        if os.path.exists(fpath):
            final_filename = os.path.basename(str(run.output_file))
            output_preview = _read_full(fpath)

    duration = None
    if run.completed_at and run.created_at:
        secs = int((run.completed_at - run.created_at).total_seconds())
        duration = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"

    success_count = sum(1 for d in step_data if d['status'] == 'SUCCESS')

    hmmbuild_project_id = None
    for item in step_data:
        sr = item['step_run']
        if sr and item['step'].tool_type == 'hmmbuild' and item['status'] == 'SUCCESS' and sr.object_id:
            hmmbuild_project_id = sr.object_id
            break

    failed_step = None
    if run.status == 'FAILURE':
        for idx, item in enumerate(step_data, start=1):
            if item['status'] == 'FAILURE':
                failed_step = {
                    'number': idx,
                    'label': item['step'].label,
                    'error': item['error'] or run.error_message or 'Step failed.',
                }
                break
        if not failed_step and run.error_message:
            failed_step = {'number': None, 'label': None, 'error': run.error_message}

    return render(request, 'workflows/run_status.html', {
        'run': run,
        'step_data': step_data,
        'step_files': step_files,
        'steps': steps,
        'final_filename': final_filename,
        'output_preview': output_preview,
        'duration': duration,
        'success_count': success_count,
        'total_steps': len(step_pairs),
        'hmmbuild_project_id': hmmbuild_project_id,
        'failed_step': failed_step,
        'tool_meta': TOOL_META,
        'can_download_input': _is_run_owner(request, run),
        'can_download_output': run.can_download_output(request.user),
        'workflow_deleted': not run.workflow,
    })


def workflow_run_result(request, run_id):
    return redirect('workflow_run_status', run_id=run_id)


def workflow_run_poll(request, run_id):
    run = get_object_or_404(WorkflowRun, id=run_id)
    if not run.can_view(request.user):
        raise Http404('Workflow run not found.')

    if run.workflow:
        steps = list(run.workflow.steps.order_by('order'))
        step_run_by_step_id = {sr.step_id: sr for sr in run.step_runs.select_related('step').all()}
        step_pairs = [(step, step_run_by_step_id.get(step.id)) for step in steps]
    else:
        ordered_srs = list(run.step_runs.order_by('step_order_snapshot'))
        step_pairs = [
            (_SnapshotStep(sr.tool_type_snapshot, sr.step_order_snapshot), sr)
            for sr in ordered_srs
        ]

    steps_payload = []
    for step, sr in step_pairs:
        output_url = None
        if sr and sr.output_file:
            from django.conf import settings as djsettings
            output_url = djsettings.MEDIA_URL + str(sr.output_file)
        steps_payload.append({
            'tool_type': step.tool_type,
            'label': step.label,
            'color': step.color,
            'status': sr.status if sr else 'PENDING',
            'error': sr.error_message if sr else '',
            'output_url': output_url,
        })

    return JsonResponse({
        'status': run.status,
        'current_step_index': run.current_step_index,
        'error_message': run.error_message,
        'has_output': bool(run.output_file),
        'steps': steps_payload,
    })


def _safe_name(text):
    import re
    text = text.replace('→', '-').replace('&', 'and')
    safe = ''.join(c if c.isalnum() or c in '-_ ' else '' for c in text).strip()
    safe = re.sub(r'[\s_]+', '_', safe)
    return safe[:60]


def _is_run_owner(request, run):
    return request.user.is_authenticated and run.user_id == request.user.id


_DOWNLOAD_NAMES = {
    'fasta_validate': 'validated_sequences',
    'sequence_clean': 'cleaned_sequences',
    'clustal_omega':  'alignment',
    'format_convert': 'converted_alignment',
    'hmmbuild':       'hmm_profile',
    'hmmsearch':      'hmmsearch_results',
    'hmmemit':        'emitted_sequences',
}


def workflow_run_download(request, run_id):
    run = get_object_or_404(WorkflowRun, id=run_id)
    if not run.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    run_label = run.name or (run.workflow.name if run.workflow else 'pipeline')
    safe = _safe_name(run_label)

    files_to_zip = []

    step_runs = run.step_runs.select_related('step').order_by('step_order_snapshot')
    for sr in step_runs:
        tool_type = sr.step.tool_type if sr.step else sr.tool_type_snapshot
        order = (sr.step.order + 1) if sr.step else (sr.step_order_snapshot + 1)
        if tool_type == 'hmmsearch':
            for item in _hmmsearch_output_items(getattr(sr, 'project', None)):
                files_to_zip.append((f"step{order}_{item['filename']}", item['path']))
            continue
        if not sr.output_file:
            continue
        fpath = os.path.join(settings.MEDIA_ROOT, str(sr.output_file))
        if os.path.exists(fpath):
            base = _DOWNLOAD_NAMES.get(tool_type, f'step_{order}_output')
            ext = os.path.splitext(fpath)[1].lower()
            arcname = f'step{order}_{base}{ext}'
            files_to_zip.append((arcname, fpath))

    final_path = None
    if run.output_file:
        final_path = os.path.join(settings.MEDIA_ROOT, str(run.output_file))
        if os.path.exists(final_path):
            already_included = any(p == final_path for _, p in files_to_zip)
            if not already_included:
                ext = os.path.splitext(final_path)[1].lower()
                files_to_zip.append((f'{safe}_final{ext}', final_path))

    if not files_to_zip:
        raise Http404('No output files available.')

    if len(files_to_zip) == 1:
        arcname, fpath = files_to_zip[0]
        response = FileResponse(open(fpath, 'rb'), content_type='application/octet-stream')
        response['Content-Disposition'] = f'attachment; filename="{arcname}"'
        return response

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for arcname, fpath in files_to_zip:
            zf.write(fpath, arcname)
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{safe}_results.zip"'
    return response


def workflow_run_input_download(request, run_id):
    run = get_object_or_404(WorkflowRun, id=run_id)

    if not run.can_download_input(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    if not run.input_file:
        raise Http404('No input file available.')

    file_path = os.path.join(settings.MEDIA_ROOT, str(run.input_file))
    if not os.path.exists(file_path):
        raise Http404('Input file not found on server.')

    run_label = run.name or (run.workflow.name if run.workflow else 'pipeline')
    ext = os.path.splitext(file_path)[1].lower() or '.fasta'
    filename = f"{_safe_name(run_label) or 'workflow'}_input{ext}"

    response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def workflow_step_download(request, run_id, step_order):
    run = get_object_or_404(WorkflowRun, id=run_id)
    if not run.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    step_runs = run.step_runs.select_related('step').all()
    sr = None
    for s in step_runs:
        effective_order = s.step.order if s.step else s.step_order_snapshot
        if effective_order == step_order:
            sr = s
            break
    if not sr or not sr.output_file:
        raise Http404('Step output not found.')
    file_path = os.path.join(settings.MEDIA_ROOT, str(sr.output_file))
    if not os.path.exists(file_path):
        raise Http404('Step output file not found on server.')

    ext = os.path.splitext(file_path)[1].lower()
    tool_type = sr.step.tool_type if sr.step else sr.tool_type_snapshot
    base = _DOWNLOAD_NAMES.get(tool_type, f'step_{step_order + 1}_output')

    run_label = run.name or (run.workflow.name if run.workflow else '')
    if run_label:
        filename = f"{_safe_name(run_label)}_{base}{ext}"
    else:
        filename = f"{base}{ext}"

    response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _delete_file(field):
    if field and field.name:
        try:
            path = os.path.join(settings.MEDIA_ROOT, str(field))
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


@login_required
@require_POST
def delete_workflow(request, workflow_id):
    workflow = get_object_or_404(Workflow, id=workflow_id, user=request.user if request.user.is_authenticated else None, is_template=False)

    workflow.runs.filter(name='').update(name=workflow.name)

    workflow.delete()
    return redirect('workflow_list')


@login_required
@require_POST
def delete_workflow_run(request, run_id):
    run = get_object_or_404(WorkflowRun, id=run_id, user=request.user if request.user.is_authenticated else None)

    _delete_file(run.input_file)
    _delete_file(run.output_file)
    for sr in run.step_runs.all():
        _delete_file(sr.output_file)

    try:
        from django.contrib.contenttypes.models import ContentType
        from users.models import UserActionHistory
        ct = ContentType.objects.get_for_model(run)
        UserActionHistory.objects.filter(content_type=ct, object_id=run.id).delete()
    except Exception:
        pass

    run.delete()
    from django.urls import reverse
    return redirect(reverse('my-projects') + '?tool=workflows')
