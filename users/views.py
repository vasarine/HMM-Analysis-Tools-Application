"""
User account pages: registration, profile, project history,
sharing projects and deleting them.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from .forms import RegisterForm, ProjectSharingForm
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from hmmsearch.models import HMMSearchProject
from hmmbuild.models import HMMBuildProject
from hmmemit.models import HMMEmitProject
from workflows.models import WorkflowRun, StepRun, TOOL_META, TOOL_TYPES
from sequence_tools.models import FASTAValidationProject, SequenceCleanerProject
from msa_tools.models import ClustalOmegaProject, FormatConversionProject
from .models import UserActionHistory
from .history_utils import log_user_action
import os

from biologine_aplikacija.utils import delete_project_files, delete_filefield
from biologine_aplikacija.preview_utils import read_file_preview, read_text_preview, stats_preview


_TOOL_TYPES_DICT = dict(TOOL_TYPES)


def _snapshot_steps_from_run(run):
    import types
    result = []
    for sr in sorted(run.step_runs.all(), key=lambda s: s.step_order_snapshot):
        tt = sr.tool_type_snapshot
        obj = types.SimpleNamespace(
            tool_type=tt,
            order=sr.step_order_snapshot,
            label=TOOL_META.get(tt, {}).get('label', tt),
            color=TOOL_META.get(tt, {}).get('color', '#94a3b8'),
        )
        result.append(obj)
    return result


def _file_ext(file_obj, default=''):
    if not file_obj:
        return default
    name = getattr(file_obj, 'name', '') or str(file_obj)
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    return ext or default


def _hmm_source_display(project):
    source = (getattr(project, 'hmm_source', '') or 'upload').lower()
    external_id = getattr(project, 'external_hmm_id', '') or ''
    external_name = getattr(project, 'external_hmm_name', '') or ''
    if source in ('pfam', 'interpro'):
        if external_id and external_name:
            return f"{external_id} ({external_name})"
        if external_id:
            return external_id
    return 'Uploaded HMM profile'


def _parse_hmm_header(text):
    stats = {}
    if not text:
        return stats
    for line in str(text).splitlines()[:60]:
        if line.startswith('//'):
            break
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0], parts[1].strip()
        if key in ('NAME', 'LENG', 'NSEQ', 'ALPH', 'EFFN'):
            stats[key.lower()] = value
    return stats


def _count_fasta_records(text):
    if not text:
        return None
    return sum(1 for line in str(text).splitlines() if line.startswith('>'))


def _count_tblout_hits(text):
    if not text:
        return 0
    return sum(1 for line in str(text).splitlines() if line.strip() and not line.startswith('#'))


def _parse_target_sequences(text):
    import re
    if not text:
        return None
    m = re.search(r'Target sequences:\s+(\d+)', text)
    return int(m.group(1)) if m else None


def _parse_tblout_hits(text, limit=6):
    hits = []
    if not text:
        return hits
    for line in str(text).splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        parts = line.split(maxsplit=18)
        if len(parts) < 6:
            continue
        hits.append({
            'target': parts[0],
            'accession': parts[1] if len(parts) > 1 and parts[1] != '-' else '',
            'query': parts[2] if len(parts) > 2 else '',
            'evalue': parts[4] if len(parts) > 4 else '',
            'score': parts[5] if len(parts) > 5 else '',
            'description': parts[18] if len(parts) > 18 else '',
        })
        if len(hits) >= limit:
            break
    return hits


def _workflow_output_label(project):
    outputs = _workflow_hmmsearch_outputs(project)
    if outputs:
        return outputs[0]['name']
    ext = _file_ext(getattr(project, 'output_file', None))
    if ext == 'hmm':
        return 'Final HMM profile'
    if ext in ('fa', 'fasta', 'faa', 'fna'):
        return 'Final FASTA output'
    if ext in ('sto', 'stockholm'):
        return 'Final Stockholm alignment'
    if ext:
        return f"Final {ext.upper()} output"
    return 'Workflow final output'


def _workflow_hmmsearch_outputs(project):
    if not isinstance(project, WorkflowRun):
        return []
    step_runs = list(project.step_runs.select_related('step').all())
    if not step_runs:
        return []

    def _tool(sr):
        return sr.step.tool_type if sr.step else sr.tool_type_snapshot

    def _order(sr):
        return sr.step.order if sr.step else sr.step_order_snapshot

    last_step_run = None
    for step_run in step_runs:
        if _tool(step_run) == 'hmmsearch':
            if last_step_run is None or _order(step_run) > _order(last_step_run):
                last_step_run = step_run
    step_orders = [_order(sr) for sr in step_runs]
    if not step_orders:
        return []
    if not last_step_run or _order(last_step_run) != max(step_orders):
        return []
    hmm_project = last_step_run.project
    if not isinstance(hmm_project, HMMSearchProject):
        return []

    files = [
        ('results.out', 'Full HMMSEARCH report', hmm_project.out_file),
        ('results.tblout', 'Target table output', hmm_project.tblout_file),
        ('results.domtbl', 'Domain table output', hmm_project.domtbl_file),
    ]
    outputs = []
    for name, description, file_field in files:
        if not file_field:
            continue
        try:
            url = file_field.url
        except Exception:
            url = ''
        outputs.append({
            'name': name,
            'description': description,
            'url': url,
            'filename': os.path.basename(str(file_field)),
        })
    return outputs


def _step_hmmsearch_items(hmm_project):
    if hmm_project is None:
        return []
    files = [
        ('results.out', getattr(hmm_project, 'out_file', None)),
        ('results.tblout', getattr(hmm_project, 'tblout_file', None)),
        ('results.domtbl', getattr(hmm_project, 'domtbl_file', None)),
        ('Hits alignment (-A)', getattr(hmm_project, 'hits_msa_file', None)),
        ('Pfam-format table (--pfamtblout)', getattr(hmm_project, 'pfamtbl_file', None)),
    ]
    items = []
    for label, ff in files:
        if not ff or not getattr(ff, 'name', ''):
            continue
        try:
            if not os.path.exists(ff.path):
                continue
        except (NotImplementedError, ValueError):
            pass
        try:
            url = ff.url
        except Exception:
            continue
        try:
            preview = read_file_preview(ff, max_lines=30, max_chars=5000).text or ''
        except Exception:
            preview = ''
        items.append({'label': label, 'url': url, 'preview': preview})
    return items


def _step_hmmbuild_extra_items(hmm_project):
    if hmm_project is None:
        return []
    ff = getattr(hmm_project, 'annotated_msa_file', None)
    if not ff or not getattr(ff, 'name', ''):
        return []
    try:
        if not os.path.exists(ff.path):
            return []
    except (NotImplementedError, ValueError):
        pass
    try:
        url = ff.url
    except Exception:
        return []
    return [{'label': 'Annotated MSA (-O)', 'url': url}]


MODEL_FIELDS = {
    "hmmbuild":         (HMMBuildProject,        ("msa_file", "hmm_file", "annotated_msa_file")),
    "hmmemit":          (HMMEmitProject,         ("hmm_file", "output_file")),
    "hmmsearch":        (HMMSearchProject,       ("fasta_file", "hmm_file", "out_file", "tblout_file", "domtbl_file", "hits_msa_file", "pfamtbl_file")),
    "fasta_validate":   (FASTAValidationProject,  ("input_fasta",)),
    "sequence_cleaner": (SequenceCleanerProject,  ("input_fasta", "output_fasta")),
    "clustalo":         (ClustalOmegaProject,     ("input_fasta", "output_alignment")),
    "format_convert":   (FormatConversionProject, ("input_file",  "output_file")),
    "workflow_run":     (WorkflowRun,             ("input_file",  "output_file")),
}

PREPROC_MODEL_FIELDS = {
    "fasta_validate":   (FASTAValidationProject,  ("input_fasta",)),
    "sequence_cleaner": (SequenceCleanerProject,  ("input_fasta", "output_fasta")),
    "clustalo":         (ClustalOmegaProject,     ("input_fasta", "output_alignment")),
    "format_convert":   (FormatConversionProject, ("input_file",  "output_file")),
}

ALL_MODEL_FIELDS = {**MODEL_FIELDS, **PREPROC_MODEL_FIELDS}


def build_project_preview(project, tool, public_context=False, large_preview=False):
    ml = 2000 if large_preview else 50
    mc = 500000 if large_preview else 10000

    if tool == 'hmmbuild':
        if getattr(project, 'hmm_file', None):
            return read_file_preview(project.hmm_file, max_lines=ml, max_chars=mc, label='HMM profile')
        return read_text_preview(getattr(project, 'result_text', ''), max_lines=ml, max_chars=mc, label='HMM profile')

    if tool == 'hmmemit':
        if getattr(project, 'output_file', None):
            return read_file_preview(project.output_file, max_lines=ml, max_chars=mc, label='Generated sequences')
        return read_text_preview(getattr(project, 'result_text', ''), max_lines=ml, max_chars=mc, label='Generated sequences')

    if tool == 'hmmsearch':
        if getattr(project, 'out_file', None):
            return read_file_preview(project.out_file, max_lines=ml, max_chars=mc, label='Search results')
        if getattr(project, 'tblout_file', None):
            return read_file_preview(project.tblout_file, max_lines=ml, max_chars=mc, label='Search results')
        if getattr(project, 'domtbl_file', None):
            return read_file_preview(project.domtbl_file, max_lines=ml, max_chars=mc, label='Search results')
        return read_text_preview(getattr(project, 'result_text', ''), max_lines=ml, max_chars=mc, label='Search results')

    if tool == 'fasta_validate':
        stats = getattr(project, 'stats', None) or {}
        return stats_preview([
            'Validation summary',
            f"Status: {'valid' if stats.get('valid') else 'issues found'}",
            f"Sequences: {stats.get('num_sequences') or stats.get('sequence_count') or 'unknown'}",
            f"Detected type: {stats.get('detected_type') or stats.get('detected_alphabet') or 'unknown'}",
            f"Errors: {len(stats.get('errors') or [])}",
            f"Warnings: {len(stats.get('warnings') or [])}",
        ], label='Validation summary')

    if tool == 'sequence_cleaner':
        stats = getattr(project, 'stats', None) or {}
        if getattr(project, 'output_fasta', None):
            preview = read_file_preview(project.output_fasta, max_lines=ml, max_chars=mc, label='Cleaned sequences')
            summary = [
                'Cleaning summary',
                f"Sequences in: {stats.get('original_count', 'unknown')}",
                f"Sequences out: {stats.get('final_count', 'unknown')}",
            ]
            preview.text = '\n'.join(summary) + ('\n\n' + preview.text if preview.text else '')
            preview.label = 'cleaning summary + cleaned output'
            if preview.is_truncated:
                preview.note = (
                    'Summary plus limited cleaned FASTA preview. '
                    'Download the output file to view all sequences.'
                )
            return preview
        return stats_preview([
            'Cleaning summary',
            f"Sequences in: {stats.get('original_count', 'unknown')}",
            f"Sequences out: {stats.get('final_count', 'unknown')}",
        ], label='Cleaning summary')

    if tool == 'clustalo':
        if getattr(project, 'output_alignment', None):
            return read_file_preview(project.output_alignment, max_lines=ml, max_chars=mc, label='Alignment')
        return None

    if tool == 'format_convert':
        if getattr(project, 'output_file', None):
            return read_file_preview(project.output_file, max_lines=ml, max_chars=mc, label='Converted output')
        return None

    if tool == 'workflow_run':
        if getattr(project, 'output_file', None):
            return read_file_preview(project.output_file, max_lines=2000, max_chars=400000, label='Workflow final output')
        return None

    return None


def build_preview_fallback(project, tool):
    summary = build_project_summary(project, tool)

    if tool == 'hmmbuild':
        return summary or 'Profile output available'
    if tool == 'hmmemit':
        return summary or 'Sequence output available'
    if tool == 'hmmsearch':
        return summary or 'Search results available'
    if tool == 'fasta_validate':
        return summary or 'Validation summary available'
    if tool == 'sequence_cleaner':
        return summary or 'Cleaning summary available'
    if tool == 'clustalo':
        return summary or 'Alignment output available'
    if tool == 'format_convert':
        return summary or 'Converted output available'
    if tool == 'workflow_run':
        return summary or 'Completed workflow'
    return summary or 'Project result available'


def attach_project_preview(project, tool, public_context=False, large_preview=False):
    project.preview = build_project_preview(project, tool, public_context=public_context, large_preview=large_preview)
    project.summary_line = build_project_summary(project, tool)
    project.preview_fallback = build_preview_fallback(project, tool)
    eml = 2000 if large_preview else 30
    emc = 500000 if large_preview else 8000
    if tool == 'hmmsearch':
        project.extra_previews = []
        if getattr(project, 'tblout_file', None):
            project.extra_previews.append(
                read_file_preview(project.tblout_file, max_lines=eml, max_chars=emc, label='Per-sequence hits table')
            )
        elif getattr(project, 'tblout_text', None):
            project.extra_previews.append(
                read_text_preview(project.tblout_text, max_lines=eml, max_chars=emc, label='Per-sequence hits table')
            )
        if getattr(project, 'domtbl_file', None):
            project.extra_previews.append(
                read_file_preview(project.domtbl_file, max_lines=eml, max_chars=emc, label='Per-domain hits table')
            )
        elif getattr(project, 'domtbl_text', None):
            project.extra_previews.append(
                read_text_preview(project.domtbl_text, max_lines=eml, max_chars=emc, label='Per-domain hits table')
            )
        if getattr(project, 'hits_msa_file', None):
            project.extra_previews.append(
                read_file_preview(project.hits_msa_file, max_lines=eml, max_chars=emc, label='Hits alignment')
            )
        if getattr(project, 'pfamtbl_file', None):
            project.extra_previews.append(
                read_file_preview(project.pfamtbl_file, max_lines=eml, max_chars=emc, label='Pfam-format table')
            )
    elif tool == 'hmmbuild':
        project.extra_previews = []
        if getattr(project, 'annotated_msa_file', None):
            project.extra_previews.append(
                read_file_preview(project.annotated_msa_file, max_lines=eml, max_chars=emc, label='Annotated MSA')
            )
    else:
        project.extra_previews = []
    return project


def build_project_summary(project, tool):
    if tool == 'hmmbuild':
        hmm_stats = _parse_hmm_header(getattr(project, 'result_text', '') or '')
        nseq = hmm_stats.get('nseq')
        return f"{nseq} sequences" if nseq else ''

    if tool == 'hmmemit':
        output_file = getattr(project, 'output_file', None)
        if output_file:
            try:
                path = output_file.path
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                        count = sum(1 for line in fh if line.startswith('>'))
                    if count:
                        return f"{count} sequence{'s' if count != 1 else ''} emitted"
            except Exception:
                pass
        return ''

    if tool == 'hmmsearch':
        tblout = getattr(project, 'tblout_text', '') or ''
        hits = _count_tblout_hits(tblout)
        if hits:
            return f"{hits} hit{'s' if hits != 1 else ''} found"
        return 'No hits found'

    if tool == 'fasta_validate':
        stats = getattr(project, 'stats', None) or {}
        count = stats.get('num_sequences') or stats.get('sequence_count')
        status = 'valid' if stats.get('valid') else 'validated with issues'
        return f"{count or 'FASTA'} sequences - {status}"

    if tool == 'sequence_cleaner':
        stats = getattr(project, 'stats', None) or {}
        count = stats.get('final_count')
        return f"{count} sequences" if count is not None else ''

    if tool == 'clustalo':
        parts = []
        if getattr(project, 'sequence_count', None):
            parts.append(f"{project.sequence_count} sequences")
        if getattr(project, 'alignment_length', None):
            parts.append(f"{project.alignment_length} columns")
        return ' - '.join(parts) or 'alignment output'

    if tool == 'format_convert':
        in_fmt = project.get_input_format_display() if hasattr(project, 'get_input_format_display') else getattr(project, 'input_format', 'input')
        out_fmt = project.get_output_format_display() if hasattr(project, 'get_output_format_display') else getattr(project, 'output_format', 'output')
        return f"{in_fmt} -> {out_fmt}"

    if tool == 'workflow_run':
        steps = list(getattr(project, 'workflow_steps', []) or [])
        count = len(steps)
        return f"{count} step{'' if count == 1 else 's'}" if count else ''

    return 'Project result'


def attach_project_summary(project, tool):
    project.summary_line = build_project_summary(project, tool)
    return project


def has_files(project, file_fields):
    for field_name in file_fields:
        file_field = getattr(project, field_name, None)
        if file_field and file_field.name:
            try:
                if os.path.exists(file_field.path):
                    return True
            except:
                pass
    return False


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("home")
    else:
        form = RegisterForm()
    return render(request, "registration/register.html", {"form": form})

@login_required
def my_projects(request):
    user = request.user
    active_tool = request.GET.get('tool')
    now = timezone.now()

    context = {'active_tool': active_tool}

    hidden_step_project_refs = set(
        StepRun.objects.filter(
            workflow_run__status__in=['PENDING', 'RUNNING', 'FAILURE', 'SUCCESS'],
            content_type__isnull=False,
            object_id__isnull=False,
        ).values_list('content_type_id', 'object_id')
    )

    _ct_id_cache = {}

    def is_hidden_workflow_step_project(project):
        cls = project.__class__
        ct_id = _ct_id_cache.get(cls)
        if ct_id is None:
            ct_id = ContentType.objects.get_for_model(cls).id
            _ct_id_cache[cls] = ct_id
        return (ct_id, project.pk) in hidden_step_project_refs

    def workflow_has_result(run):
        if not run.output_file or not run.output_file.name:
            return False
        try:
            return os.path.exists(run.output_file.path)
        except Exception:
            return True

    def get_active_projects(Model, file_fields):
        my_projects = Model.objects.filter(user=user).select_related('user').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).filter(task_status='SUCCESS')

        shared_projects = Model.objects.filter(shared_with=user).select_related('user').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).filter(task_status='SUCCESS')

        all_projects = (my_projects | shared_projects).distinct().order_by('-created_at')

        valid_projects = []
        for p in all_projects:
            if not is_hidden_workflow_step_project(p) and has_files(p, file_fields):
                p.is_mine = (p.user == user)
                valid_projects.append(p)

        return valid_projects

    def get_active_preproc_projects(Model, file_fields, tool_type, extra_filter=None):
        my_projects = Model.objects.filter(user=user).select_related('user').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

        shared_projects = Model.objects.filter(shared_with=user).select_related('user').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

        all_projects = (my_projects | shared_projects).distinct().order_by('-created_at')
        if extra_filter:
            all_projects = all_projects.filter(**extra_filter)

        valid_projects = []
        for p in all_projects:
            if not is_hidden_workflow_step_project(p) and has_files(p, file_fields):
                p.is_mine = (p.user == user)
                p.tool_type = tool_type
                valid_projects.append(p)

        return valid_projects

    if active_tool == 'workflows':
        workflow_runs = WorkflowRun.objects.filter(
            Q(user=user) | Q(shared_with=user),
            status='SUCCESS',
        ).select_related('workflow', 'user').prefetch_related(
            'step_runs__step', 'workflow__steps'
        ).distinct().order_by('-created_at')

        run_data = []
        for run in workflow_runs:
            if workflow_has_result(run):
                try:
                    from django.conf import settings as djsettings
                    fpath = os.path.join(djsettings.MEDIA_ROOT, str(run.output_file))
                    size = os.path.getsize(fpath) if os.path.exists(fpath) else 0
                except Exception:
                    size = 0
                if run.workflow:
                    steps = list(run.workflow.steps.order_by('order'))
                else:
                    steps = _snapshot_steps_from_run(run)
                run.workflow_steps = steps
                run.is_mine = (run.user == user)
                attach_project_preview(run, 'workflow_run')
                run_data.append({'run': run, 'size': size, 'steps': steps})

        paginator = Paginator(run_data, 10)
        page_obj = paginator.get_page(request.GET.get('page', 1))
        context['workflow_runs'] = page_obj.object_list
        context['wf_page_obj'] = page_obj
        return render(request, 'users/my_projects.html', context)

    if active_tool == 'preprocessing':
        preproc_items = []

        for p in FASTAValidationProject.objects.filter(
            user=user
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).order_by('-created_at'):
            if not is_hidden_workflow_step_project(p) and p.input_fasta:
                stats = p.stats or {}
                summary = ''
                if stats.get('num_sequences'):
                    summary = f"{stats['num_sequences']} sequences"
                    if stats.get('valid'):
                        summary += ' - valid'
                    elif stats.get('errors'):
                        summary += f" - {len(stats['errors'])} errors"
                preproc_items.append({
                    'id': p.id,
                    'tool_type': 'fasta_validate',
                    'tool_label': 'FASTA Validate',
                    'name': p.name or 'Untitled',
                    'created_at': p.created_at,
                    'stats_summary': summary,
                    'input_url': p.input_fasta.url,
                    'input_download_url': reverse('fasta_validate_input_download', args=[p.id]),
                    'input_label': 'sequences.fasta',
                    'preview': build_project_preview(p, 'fasta_validate'),
                    'preview_fallback': build_preview_fallback(p, 'fasta_validate'),
                    'detail_url': f"{reverse('shared-project', args=['fasta_validate', p.share_token])}?from=my-projects",
                    'download_url': reverse('fasta_validate_input_download', args=[p.id]),
                })

        for p in SequenceCleanerProject.objects.filter(
            user=user
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).order_by('-created_at'):
            if not is_hidden_workflow_step_project(p) and p.output_fasta:
                stats = p.stats or {}
                summary = ''
                if stats.get('original_count') is not None:
                    removed = (stats.get('original_count', 0) - stats.get('final_count', 0))
                    summary = f"{stats.get('final_count', '?')} sequences"
                    if removed > 0:
                        summary += f" - {removed} removed"
                preproc_items.append({
                    'id': p.id,
                    'tool_type': 'sequence_cleaner',
                    'tool_label': 'Seq Cleaner',
                    'name': p.name or 'Untitled',
                    'created_at': p.created_at,
                    'stats_summary': summary,
                    'input_url': p.input_fasta.url if p.input_fasta else '',
                    'input_download_url': reverse('fasta_clean_input_download', args=[p.id]) if p.input_fasta else '',
                    'input_label': 'input.fasta',
                    'output_url': p.output_fasta.url,
                    'output_label': 'cleaned.fasta',
                    'preview': build_project_preview(p, 'sequence_cleaner'),
                    'preview_fallback': build_preview_fallback(p, 'sequence_cleaner'),
                    'detail_url': f"{reverse('shared-project', args=['sequence_cleaner', p.share_token])}?from=my-projects",
                    'download_url': f'/preprocessing/sequences/clean/{p.id}/download/',
                })

        for p in ClustalOmegaProject.objects.filter(
            user=user, task_status='SUCCESS'
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).order_by('-created_at'):
            if not is_hidden_workflow_step_project(p) and p.output_alignment:
                summary_parts = []
                if p.sequence_count:
                    summary_parts.append(f"{p.sequence_count} sequences")
                if p.alignment_length:
                    summary_parts.append(f"{p.alignment_length} columns")
                preproc_items.append({
                    'id': p.id,
                    'tool_type': 'clustalo',
                    'msa_tool': p.tool,
                    'tool_label': p.get_tool_display(),
                    'name': p.name or 'Untitled',
                    'created_at': p.created_at,
                    'stats_summary': ' - '.join(summary_parts),
                    'input_url': p.input_fasta.url if p.input_fasta else '',
                    'input_download_url': reverse('clustalo_input_download', args=[p.id]) if p.input_fasta else '',
                    'input_label': 'sequences.fasta',
                    'output_url': p.output_alignment.url,
                    'output_label': f'alignment.{p.output_format}',
                    'preview': build_project_preview(p, 'clustalo'),
                    'preview_fallback': build_preview_fallback(p, 'clustalo'),
                    'detail_url': f"{reverse('shared-project', args=['clustalo', p.share_token])}?from=my-projects",
                    'download_url': f'/preprocessing/msa/align/{p.id}/download/',
                })

        for p in FormatConversionProject.objects.filter(
            user=user
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).order_by('-created_at'):
            if not is_hidden_workflow_step_project(p) and p.output_file:
                summary = f"{p.get_input_format_display()} → {p.get_output_format_display()}"
                if p.sequence_count:
                    summary += f" - {p.sequence_count} sequences"
                preproc_items.append({
                    'id': p.id,
                    'tool_type': 'format_convert',
                    'tool_label': 'Format Convert',
                    'name': p.name or 'Untitled',
                    'created_at': p.created_at,
                    'stats_summary': summary,
                    'input_url': p.input_file.url if p.input_file else '',
                    'input_download_url': reverse('format_convert_input_download', args=[p.id]) if p.input_file else '',
                    'input_label': f'{p.input_format} alignment',
                    'output_url': p.output_file.url,
                    'output_label': f'{p.output_format} alignment',
                    'preview': build_project_preview(p, 'format_convert'),
                    'preview_fallback': build_preview_fallback(p, 'format_convert'),
                    'detail_url': f"{reverse('shared-project', args=['format_convert', p.share_token])}?from=my-projects",
                    'download_url': f'/preprocessing/msa/convert/{p.id}/download/',
                })

        preproc_items.sort(key=lambda x: x['created_at'], reverse=True)

        PREPROC_TYPE_MAP = {
            'fasta_validator':  'fasta_validate',
            'sequence_cleaner': 'sequence_cleaner',
            'msa':              'clustalo',
            'format_converter': 'format_convert',
        }
        active_preproc_type = request.GET.get('type', '')
        if active_preproc_type in PREPROC_TYPE_MAP:
            internal = PREPROC_TYPE_MAP[active_preproc_type]
            preproc_items = [it for it in preproc_items if it['tool_type'] == internal]
        else:
            active_preproc_type = ''

        paginator = Paginator(preproc_items, 10)
        page_obj = paginator.get_page(request.GET.get('page', 1))
        context['preproc_items'] = page_obj.object_list
        context['preproc_page_obj'] = page_obj
        context['active_preproc_type'] = active_preproc_type
        return render(request, 'users/my_projects.html', context)

    projects = []
    if active_tool == 'hmmbuild':
        projects = get_active_projects(HMMBuildProject, ('msa_file', 'hmm_file'))
        for p in projects:
            p.tool_type = 'hmmbuild'
            attach_project_summary(p, 'hmmbuild')
            attach_project_preview(p, 'hmmbuild')
    elif active_tool == 'hmmsearch':
        projects = get_active_projects(HMMSearchProject, ('fasta_file', 'hmm_file', 'out_file', 'tblout_file', 'domtbl_file'))
        for p in projects:
            p.tool_type = 'hmmsearch'
            attach_project_summary(p, 'hmmsearch')
            attach_project_preview(p, 'hmmsearch')
    elif active_tool == 'hmmemit':
        projects = get_active_projects(HMMEmitProject, ('hmm_file', 'output_file'))
        for p in projects:
            p.tool_type = 'hmmemit'
            attach_project_summary(p, 'hmmemit')
            attach_project_preview(p, 'hmmemit')
    else:
        hmmbuild_projects = get_active_projects(HMMBuildProject, ('msa_file', 'hmm_file'))
        hmmsearch_projects = get_active_projects(HMMSearchProject, ('fasta_file', 'hmm_file', 'out_file', 'tblout_file', 'domtbl_file'))
        hmmemit_projects = get_active_projects(HMMEmitProject, ('hmm_file', 'output_file'))
        fasta_validation_projects = get_active_preproc_projects(
            FASTAValidationProject,
            ('input_fasta',),
            'fasta_validate',
            {'stats__isnull': False},
        )
        sequence_cleaner_projects = get_active_preproc_projects(
            SequenceCleanerProject,
            ('input_fasta', 'output_fasta'),
            'sequence_cleaner',
        )
        clustalo_projects = get_active_preproc_projects(
            ClustalOmegaProject,
            ('input_fasta', 'output_alignment'),
            'clustalo',
            {'task_status': 'SUCCESS'},
        )
        format_conversion_projects = get_active_preproc_projects(
            FormatConversionProject,
            ('input_file', 'output_file'),
            'format_convert',
        )
        workflow_runs = WorkflowRun.objects.filter(
            Q(user=user) | Q(shared_with=user),
            status='SUCCESS',
        ).select_related('workflow', 'user').distinct().order_by('-created_at')
        workflow_run_projects = []
        for p in workflow_runs:
            if workflow_has_result(p):
                p.is_mine = (p.user == user)
                p.tool_type = 'workflow_run'
                p.name = p.name or (p.workflow.name if p.workflow else 'Workflow run')
                p.workflow_steps = (
                    list(p.workflow.steps.order_by('order')) if p.workflow
                    else _snapshot_steps_from_run(p)
                )
                attach_project_summary(p, 'workflow_run')
                attach_project_preview(p, 'workflow_run')
                workflow_run_projects.append(p)

        for p in hmmbuild_projects:
            p.tool_type = 'hmmbuild'
            attach_project_summary(p, 'hmmbuild')
            attach_project_preview(p, 'hmmbuild')
        for p in hmmsearch_projects:
            p.tool_type = 'hmmsearch'
            attach_project_summary(p, 'hmmsearch')
            attach_project_preview(p, 'hmmsearch')
        for p in hmmemit_projects:
            p.tool_type = 'hmmemit'
            attach_project_summary(p, 'hmmemit')
            attach_project_preview(p, 'hmmemit')
        for p in fasta_validation_projects:
            attach_project_summary(p, 'fasta_validate')
            attach_project_preview(p, 'fasta_validate')
        for p in sequence_cleaner_projects:
            attach_project_summary(p, 'sequence_cleaner')
            attach_project_preview(p, 'sequence_cleaner')
        for p in clustalo_projects:
            attach_project_summary(p, 'clustalo')
            attach_project_preview(p, 'clustalo')
        for p in format_conversion_projects:
            attach_project_summary(p, 'format_convert')
            attach_project_preview(p, 'format_convert')

        projects = (
            hmmbuild_projects
            + hmmsearch_projects
            + hmmemit_projects
            + fasta_validation_projects
            + sequence_cleaner_projects
            + clustalo_projects
            + format_conversion_projects
            + workflow_run_projects
        )
        projects.sort(key=lambda x: x.created_at, reverse=True)

    paginator = Paginator(projects, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context['projects'] = page_obj.object_list
    context['page_obj'] = page_obj

    return render(request, 'users/my_projects.html', context)

@login_required
def delete_project(request, pk):
    if request.method != "POST":
        return redirect("my-projects")

    tool = request.GET.get("tool", "")

    if tool == "workflow_run":
        from workflows.models import WorkflowRun
        run = get_object_or_404(WorkflowRun, pk=pk, user=request.user)
        delete_filefield(run.input_file)
        delete_filefield(run.output_file)
        for sr in run.step_runs.all():
            delete_filefield(sr.output_file)
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(run)
        UserActionHistory.objects.filter(content_type=ct, object_id=run.id).delete()
        run.delete()
        original_tool_param = request.POST.get("from_tool", "workflows")
        return redirect(f"{reverse('my-projects')}?tool={original_tool_param}")

    if tool not in ALL_MODEL_FIELDS:
        return redirect("my-projects")

    Model, fields = ALL_MODEL_FIELDS[tool]
    project = get_object_or_404(Model, pk=pk, user=request.user)

    project_name = project.name
    project_id = project.id

    original_tool_param = request.POST.get("from_tool", "")

    with transaction.atomic():
        from django.contrib.contenttypes.models import ContentType
        content_type = ContentType.objects.get_for_model(project)
        UserActionHistory.objects.filter(
            content_type=content_type,
            object_id=project.id,
        ).delete()
        delete_project_files(project, fields)

    if original_tool_param:
        return redirect(f"{reverse('my-projects')}?tool={original_tool_param}")
    else:
        return redirect("my-projects")


@login_required
def delete_selected_projects(request):
    if request.method != "POST":
        return redirect("my-projects")

    import json

    projects_by_tool_list = request.POST.getlist("projects_by_tool")

    with transaction.atomic():
        from django.contrib.contenttypes.models import ContentType
        for json_str in projects_by_tool_list:
            data = json.loads(json_str)
            tool = data['tool']
            ids = [int(id_val) for id_val in data['ids']]

            if tool == 'workflow_run':
                from workflows.models import WorkflowRun
                runs = list(WorkflowRun.objects.filter(id__in=ids, user=request.user))
                if runs:
                    ct = ContentType.objects.get_for_model(WorkflowRun)
                    UserActionHistory.objects.filter(
                        content_type=ct,
                        object_id__in=[r.id for r in runs],
                    ).delete()
                for run in runs:
                    delete_filefield(run.input_file)
                    delete_filefield(run.output_file)
                    for sr in run.step_runs.all():
                        delete_filefield(sr.output_file)
                    run.delete()
                continue

            if tool not in ALL_MODEL_FIELDS:
                continue

            Model, fields = ALL_MODEL_FIELDS[tool]
            projects = list(Model.objects.filter(id__in=ids, user=request.user))
            if projects:
                ct = ContentType.objects.get_for_model(Model)
                UserActionHistory.objects.filter(
                    content_type=ct,
                    object_id__in=[p.id for p in projects],
                ).delete()
            for project in projects:
                delete_project_files(project, fields)

    from_tool = request.POST.get("from_tool", "")
    if from_tool:
        return redirect(f"{reverse('my-projects')}?tool={from_tool}")
    return redirect("my-projects")


_TOOL_TO_TAB = {
    'fasta_validate':   'preprocessing',
    'sequence_cleaner': 'preprocessing',
    'clustalo':         'preprocessing',
    'format_convert':   'preprocessing',
    'workflow_run':     'workflows',
}
_TOOL_TO_LOG_TYPE = {
    'workflow_run': 'workflow',
}

_TOOL_LABELS = {
    'hmmbuild': 'HMMBUILD',
    'hmmemit': 'HMMEMIT',
    'hmmsearch': 'HMMSEARCH',
    'fasta_validate': 'FASTA VALIDATE',
    'sequence_cleaner': 'SEQ CLEANER',
    'clustalo': 'CLUSTALO',
    'mafft': 'MAFFT',
    'muscle': 'MUSCLE',
    'kalign': 'KALIGN',
    'format_convert': 'FORMAT CONVERT',
    'workflow_run': 'WORKFLOW',
}


from biologine_aplikacija.parameter_builder import build_parameter_overrides, build_step_parameter_overrides


def build_project_report(project, tool):
    rows = []
    note = ''

    def add(label, value):
        if value not in (None, ''):
            rows.append({'label': label, 'value': value})

    add('Created', project.created_at.strftime('%Y-%m-%d %H:%M') if project.created_at else '')

    if tool == 'hmmbuild':
        hmm_stats = _parse_hmm_header(getattr(project, 'result_text', '') or '')
        add('Input type', f"Alignment ({_file_ext(getattr(project, 'msa_file', None), 'msa')})")
        add('Sequence type', (hmm_stats.get('alph') or '').upper())
        add('Sequences used', hmm_stats.get('nseq'))
    elif tool == 'hmmemit':
        seq_count = _count_fasta_records(getattr(project, 'result_text', '') or '')
        add('Profile source', _hmm_source_display(project))
        add('Sequences emitted', seq_count)
        add('Output file', 'sequences.fa')
    elif tool == 'hmmsearch':
        tblout = getattr(project, 'tblout_text', '') or ''
        target_hits = _count_tblout_hits(tblout)
        seq_searched = _parse_target_sequences(getattr(project, 'result_text', '') or '')
        add('Profile source', _hmm_source_display(project))
        add('Sequences searched', seq_searched)
        add('Target hits', target_hits)
    elif tool == 'fasta_validate':
        stats = getattr(project, 'stats', None) or {}
        add('Validation status', 'Valid' if stats.get('valid') else 'Issues found')
        add('Sequences', stats.get('num_sequences') or stats.get('sequence_count'))
        add('Detected type', stats.get('detected_type') or stats.get('detected_alphabet'))
        if stats.get('min_length') is not None and stats.get('max_length') is not None:
            add('Length', f"{stats.get('min_length')} - {stats.get('max_length')}")
        avg = stats.get('avg_length')
        add('Avg. length', f"{round(avg)} bp" if avg is not None else None)
        add('Errors', len(stats.get('errors') or []))
        add('Warnings', len(stats.get('warnings') or []))
        note = 'Report-only result. Input FASTA is unchanged.'
    elif tool == 'sequence_cleaner':
        stats = getattr(project, 'stats', None) or {}
        options = getattr(project, 'options', None) or {}
        original = stats.get('original_count')
        final = stats.get('final_count')
        removed = max(original - final, 0) if original is not None and final is not None else None
        add('Sequences in', original)
        add('Sequences out', final)
        add('Sequences removed', removed)
        add('Sequences modified', stats.get('modified_count'))
        add('Sequence type', (stats.get('detected_alphabet') or '').upper())
        add('Invalid handling', (options.get('invalid_char_strategy') or 'replace').title())
        add('Duplicates removed', stats.get('removed_dup_seqs', 0))
    elif tool == 'clustalo':
        add('Sequences', getattr(project, 'sequence_count', None))
        add('Alignment length', getattr(project, 'alignment_length', None))
        fmt = project.get_output_format_display() if hasattr(project, 'get_output_format_display') else getattr(project, 'output_format', '')
        add('Output format', fmt)
    elif tool == 'format_convert':
        in_fmt = project.get_input_format_display() if hasattr(project, 'get_input_format_display') else getattr(project, 'input_format', '')
        out_fmt = project.get_output_format_display() if hasattr(project, 'get_output_format_display') else getattr(project, 'output_format', '')
        add('Input format', in_fmt)
        add('Output format', out_fmt)
        add('Sequences', getattr(project, 'sequence_count', None))
    elif tool == 'workflow_run':
        step_runs = list(project.step_runs.select_related('step').all()) if hasattr(project, 'step_runs') else []
        if getattr(project, 'workflow', None):
            total_steps = project.workflow.steps.count()
            wf_name = project.workflow.name
        else:
            total_steps = len(step_runs)
            wf_name = ''
        completed = sum(1 for sr in step_runs if sr.status == 'SUCCESS')
        add('Workflow', wf_name or '(original workflow deleted)')
        add('Status', getattr(project, 'task_status', '') or getattr(project, 'status', ''))
        add('Steps', total_steps)
        add('Completed steps', f"{completed} / {total_steps}")
        add('Completed', project.completed_at.strftime('%Y-%m-%d %H:%M') if getattr(project, 'completed_at', None) else '')

    return {'rows': rows, 'note': note}


def visibility_context(project, is_owner):
    if not is_owner and project.visibility == 'private':
        return 'Shared', 'shared'
    labels = {'public': 'Public', 'link': 'Link only', 'private': 'Private'}
    return labels.get(project.visibility, project.visibility.title()), project.visibility or 'private'


@login_required
def share_project(request, tool, pk):
    if tool not in MODEL_FIELDS:
        return redirect('my-projects')

    Model, _ = MODEL_FIELDS[tool]
    project = get_object_or_404(Model, pk=pk, user=request.user)
    log_tool = _TOOL_TO_LOG_TYPE.get(tool, tool)
    back_tab = _TOOL_TO_TAB.get(tool, tool)

    if request.method == 'POST':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            email = request.POST.get('email')
            action = request.POST.get('action')

            if action == 'add' and email:
                try:
                    user_to_share = User.objects.filter(email=email).exclude(id=request.user.id).first()

                    if not user_to_share:
                        return JsonResponse({'success': False, 'error': 'User not found with this email address'})

                    if project.shared_with.filter(id=user_to_share.id).exists():
                        return JsonResponse({'success': False, 'error': 'Already shared with this user'})

                    project.shared_with.add(user_to_share)

                    log_user_action(
                        user=request.user,
                        action_type='project_shared',
                        tool_type=log_tool,
                        project=project,
                        project_name=project.name,
                        description=f'Shared project with: {user_to_share.username}',
                        metadata={'shared_with': user_to_share.username}
                    )

                    return JsonResponse({
                        'success': True,
                        'user': {
                            'id': user_to_share.id,
                            'email': user_to_share.email,
                            'username': user_to_share.username
                        }
                    })
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    return JsonResponse({'success': False, 'error': f'Error: {str(e)}'})

            elif action == 'remove':
                user_id = request.POST.get('user_id')
                try:
                    user_to_remove = User.objects.get(id=user_id)
                    project.shared_with.remove(user_to_remove)

                    log_user_action(
                        user=request.user,
                        action_type='project_unshared',
                        tool_type=log_tool,
                        project=project,
                        project_name=project.name,
                        description=f'Removed sharing with: {user_to_remove.username}',
                        metadata={'removed_user': user_to_remove.username}
                    )

                    return JsonResponse({'success': True})
                except User.DoesNotExist:
                    return JsonResponse({'success': False, 'error': 'User not found'})

        form = ProjectSharingForm(request.POST)
        if form.is_valid():
            new_visibility = form.cleaned_data['visibility']
            new_share_outputs = form.cleaned_data['share_outputs']
            new_share_inputs = form.cleaned_data['share_inputs']

            visibility_changed = project.visibility != new_visibility
            files_changed = (
                project.share_outputs != new_share_outputs
                or project.share_inputs != new_share_inputs
            )

            if visibility_changed or files_changed:
                old_visibility = project.visibility
                project.visibility = new_visibility
                project.share_outputs = new_share_outputs
                project.share_inputs = new_share_inputs
                project.save()

                if visibility_changed:
                    log_user_action(
                        user=request.user,
                        action_type='project_visibility_changed',
                        tool_type=log_tool,
                        project=project,
                        project_name=project.name,
                        description=f'Changed visibility from {old_visibility} to {project.visibility}',
                        metadata={'old_visibility': old_visibility, 'new_visibility': project.visibility}
                    )

            return redirect(f"{reverse('my-projects')}?tool={back_tab}")
    else:
        form = ProjectSharingForm(
            initial={
                'visibility': project.visibility,
                'share_outputs': project.share_outputs,
                'share_inputs': project.share_inputs,
            }
        )

    share_link = request.build_absolute_uri(
        reverse('shared-project', args=[tool, project.share_token])
    )

    context = {
        'project': project,
        'form': form,
        'tool': tool,
        'share_link': share_link,
        'shared_users': project.shared_with.all(),
    }

    return render(request, 'users/share_project.html', context)


def shared_project_view(request, tool, token):
    if tool not in MODEL_FIELDS:
        return redirect('home')

    Model, _ = MODEL_FIELDS[tool]

    try:
        project = Model.objects.select_related('user').get(share_token=token)
    except Model.DoesNotExist:
        messages.error(request, 'Project not found or link is no longer valid.')
        return redirect('home')

    if not project.can_view(request.user):
        return HttpResponseForbidden("You don't have permission to view this project.")

    is_owner = (project.user == request.user)
    can_edit = project.can_edit(request.user)
    attach_project_preview(project, tool, public_context=(project.visibility == 'public'), large_preview=True)

    source = request.GET.get('from', '')
    if source not in ('my-projects', 'public-projects'):
        source = ''

    requested_tab = request.GET.get('tab', '')
    back_tab = _TOOL_TO_TAB.get(tool, tool)
    if source == 'public-projects':
        back_label = 'Public Projects'
        back_url = reverse('public-projects')
        view_context = 'public'
    elif source == 'my-projects' or is_owner:
        back_label = 'My Projects'
        effective_tab = requested_tab if requested_tab else back_tab
        if effective_tab and effective_tab != 'all':
            back_url = f"{reverse('my-projects')}?tool={effective_tab}"
        else:
            back_url = reverse('my-projects')
        view_context = 'owner' if is_owner else 'shared'
    else:
        back_label = 'Back'
        back_url = 'javascript:history.back()'
        view_context = 'shared'

    if view_context in ('owner', 'shared'):
        sidebar_section = 'my_projects'
    elif view_context == 'public':
        sidebar_section = 'public_projects'
    else:
        sidebar_section = ''

    can_download_input = project.can_download_input(request.user)
    can_download_output = project.can_download_output(request.user)
    tool_label = _TOOL_LABELS.get(tool, tool.upper())
    visibility_label, visibility_key = visibility_context(project, is_owner)
    report = build_project_report(project, tool)
    project_title = project.name or 'Untitled project'
    workflow_steps = []
    workflow_step_rows = []
    workflow_hmmsearch_outputs = []
    if tool == 'workflow_run':
        workflow_hmmsearch_outputs = _workflow_hmmsearch_outputs(project)
        if project.workflow:
            workflow_steps = list(project.workflow.steps.order_by('order'))
            step_runs_by_id = {sr.step_id: sr for sr in project.step_runs.select_related('step').all()}
            for step in workflow_steps:
                step_run = step_runs_by_id.get(step.id)
                output_name = ''
                download_url = ''
                preview_text = None
                hmmsearch_items = []
                if step_run and step_run.status == 'SUCCESS':
                    if step.tool_type == 'hmmsearch':
                        hmm_proj = step_run.project
                        if isinstance(hmm_proj, HMMSearchProject):
                            hmmsearch_items = _step_hmmsearch_items(hmm_proj)
                        output_name = hmmsearch_items[0]['label'] if hmmsearch_items else ''
                    elif step_run.output_file:
                        output_name = os.path.basename(str(step_run.output_file))
                        try:
                            p = read_file_preview(step_run.output_file, max_lines=30, max_chars=5000)
                            if p.text:
                                preview_text = p.text
                                download_url = reverse('workflow_step_download', args=[project.id, step.order])
                        except Exception:
                            pass
                        if step.tool_type == 'hmmbuild':
                            hmmsearch_items += _step_hmmbuild_extra_items(step_run.project)
                workflow_step_rows.append({
                    'number': step.order + 1,
                    'label': step.label,
                    'tool': step.get_tool_type_display() if hasattr(step, 'get_tool_type_display') else step.tool_type,
                    'status': step_run.status if step_run else 'PENDING',
                    'output': output_name,
                    'error': step_run.error_message if step_run else '',
                    'download_url': download_url,
                    'preview_text': preview_text,
                    'hmmsearch_items': hmmsearch_items,
                    'parameter_overrides': build_step_parameter_overrides(step_run),
                })
        else:
            workflow_steps = []
            for sr in sorted(project.step_runs.all(), key=lambda s: s.step_order_snapshot):
                tt = sr.tool_type_snapshot
                output_name = ''
                download_url = ''
                preview_text = None
                hmmsearch_items = []
                step_order = sr.step_order_snapshot
                if sr.status == 'SUCCESS':
                    if tt == 'hmmsearch':
                        hmm_proj = sr.project
                        if isinstance(hmm_proj, HMMSearchProject):
                            hmmsearch_items = _step_hmmsearch_items(hmm_proj)
                        output_name = hmmsearch_items[0]['label'] if hmmsearch_items else ''
                    elif sr.output_file:
                        output_name = os.path.basename(str(sr.output_file))
                        try:
                            p = read_file_preview(sr.output_file, max_lines=30, max_chars=5000)
                            if p.text:
                                preview_text = p.text
                                download_url = reverse('workflow_step_download', args=[project.id, step_order])
                        except Exception:
                            pass
                        if tt == 'hmmbuild':
                            hmmsearch_items += _step_hmmbuild_extra_items(sr.project)
                workflow_step_rows.append({
                    'number': step_order + 1,
                    'label': TOOL_META.get(tt, {}).get('label', tt),
                    'tool': _TOOL_TYPES_DICT.get(tt, tt),
                    'status': sr.status,
                    'output': output_name,
                    'error': sr.error_message,
                    'download_url': download_url,
                    'preview_text': preview_text,
                    'hmmsearch_items': hmmsearch_items,
                    'parameter_overrides': build_step_parameter_overrides(sr),
                })
        project_title = project.name or (project.workflow.name if project.workflow else 'Workflow run')
    top_hits = _parse_tblout_hits(getattr(project, 'tblout_text', '') or '') if tool == 'hmmsearch' else []
    target_hits = _count_tblout_hits(getattr(project, 'tblout_text', '') or '') if tool == 'hmmsearch' else None
    has_next_actions = is_owner and (
        (tool == 'hmmbuild' and bool(getattr(project, 'hmm_file', None)))
        or tool == 'fasta_validate'
        or (tool == 'sequence_cleaner' and bool(getattr(project, 'output_fasta', None)))
        or (tool == 'clustalo' and bool(getattr(project, 'output_alignment', None)))
        or (tool == 'format_convert' and bool(getattr(project, 'output_file', None)) and getattr(project, 'output_format', '') == 'stockholm')
        or (tool == 'workflow_run' and bool(getattr(project, 'workflow', None)))
    )

    _format_ext = {'fasta': 'fasta', 'clustal': 'aln', 'stockholm': 'sto'}
    hmmbuild_input_filename = ''
    if tool == 'hmmbuild' and getattr(project, 'msa_file', None):
        ext = os.path.splitext(project.msa_file.name)[1].lstrip('.').lower() or 'msa'
        hmmbuild_input_filename = f'alignment.{ext}'
    format_convert_input_filename = ''
    format_convert_output_filename = ''
    if tool == 'format_convert':
        in_fmt = getattr(project, 'input_format', '') or ''
        out_fmt = getattr(project, 'output_format', '') or ''
        format_convert_input_filename = f'input.{_format_ext.get(in_fmt, in_fmt)}' if in_fmt else 'input'
        format_convert_output_filename = f'converted.{_format_ext.get(out_fmt, out_fmt)}' if out_fmt else 'converted'
    hmmemit_output_filename = ''
    if tool == 'hmmemit' and getattr(project, 'output_file', None):
        ext = os.path.splitext(project.output_file.name)[1].lstrip('.').lower() or 'fa'
        hmmemit_output_filename = f'alignment.{ext}' if ext == 'sto' else f'sequences.{ext}'

    context = {
        'project': project,
        'project_title': project_title,
        'tool': tool,
        'tool_label': tool_label,
        'can_edit': can_edit,
        'is_owner': is_owner,
        'can_share': is_owner,
        'can_delete': is_owner,
        'can_continue': is_owner,
        'can_download_input': can_download_input,
        'can_download_output': can_download_output,
        'hmmbuild_input_filename': hmmbuild_input_filename,
        'format_convert_input_filename': format_convert_input_filename,
        'format_convert_output_filename': format_convert_output_filename,
        'hmmemit_output_filename': hmmemit_output_filename,
        'can_view_preview': can_download_output or tool == 'fasta_validate',
        'back_label': back_label,
        'back_url': back_url,
        'delete_from_tool': back_tab,
        'visibility_label': visibility_label,
        'visibility_key': visibility_key,
        'report_rows': report['rows'],
        'report_note': report['note'],
        'parameter_overrides': build_parameter_overrides(project, tool),
        'workflow_steps': workflow_steps,
        'workflow_step_rows': workflow_step_rows,
        'workflow_hmmsearch_outputs': workflow_hmmsearch_outputs,
        'workflow_output_label': _workflow_output_label(project) if tool == 'workflow_run' else '',
        'top_hits': top_hits,
        'target_hits': target_hits,
        'has_next_actions': has_next_actions,
        'view_context': view_context,
        'sidebar_section': sidebar_section,
        'sidebar_category': back_tab,
    }

    return render(request, 'users/shared_project_view.html', context)


@login_required
def remove_shared_project(request, tool, pk):
    if request.method != "POST":
        return redirect("my-projects")

    if tool not in MODEL_FIELDS:
        return redirect("my-projects")

    Model, _ = MODEL_FIELDS[tool]
    project = get_object_or_404(Model, pk=pk)

    if request.user in project.shared_with.all():
        project.shared_with.remove(request.user)

    return redirect(f"{reverse('my-projects')}?tool={tool}")


def public_projects(request):
    active_tool = request.GET.get('tool')
    now = timezone.now()

    context = {'active_tool': active_tool, 'active_preproc_type': ''}

    hidden_step_project_refs = set(
        StepRun.objects.filter(
            workflow_run__status__in=['PENDING', 'RUNNING', 'FAILURE', 'SUCCESS'],
            content_type__isnull=False,
            object_id__isnull=False,
        ).values_list('content_type_id', 'object_id')
    )

    _ct_id_cache = {}

    def is_hidden_workflow_step_project(project):
        cls = project.__class__
        ct_id = _ct_id_cache.get(cls)
        if ct_id is None:
            ct_id = ContentType.objects.get_for_model(cls).id
            _ct_id_cache[cls] = ct_id
        return (ct_id, project.pk) in hidden_step_project_refs

    def _hmm_public(Model, file_fields):
        qs = Model.objects.filter(
            visibility='public',
            task_status='SUCCESS',
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).select_related('user').order_by('-created_at')
        return [p for p in qs if not is_hidden_workflow_step_project(p) and has_files(p, file_fields)]

    def _preproc_public(Model, file_fields, extra_filter=None):
        qs = Model.objects.filter(visibility='public').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).select_related('user').order_by('-created_at')
        if any(field.name == 'task_status' for field in Model._meta.fields):
            qs = qs.filter(task_status='SUCCESS')
        if extra_filter:
            qs = qs.filter(**extra_filter)
        return [p for p in qs if not is_hidden_workflow_step_project(p) and has_files(p, file_fields)]

    projects = []

    if active_tool == 'hmmbuild':
        projects = _hmm_public(HMMBuildProject, ('hmm_file',))
        for p in projects:
            p.tool_type = 'hmmbuild'
            attach_project_preview(p, 'hmmbuild', public_context=True)

    elif active_tool == 'hmmsearch':
        projects = _hmm_public(HMMSearchProject, ('out_file', 'tblout_file', 'domtbl_file'))
        for p in projects:
            p.tool_type = 'hmmsearch'
            attach_project_preview(p, 'hmmsearch', public_context=True)

    elif active_tool == 'hmmemit':
        projects = _hmm_public(HMMEmitProject, ('output_file',))
        for p in projects:
            p.tool_type = 'hmmemit'
            attach_project_preview(p, 'hmmemit', public_context=True)

    elif active_tool == 'preprocessing':
        fv = _preproc_public(FASTAValidationProject,  ('input_fasta',),
                             {'stats__isnull': False})
        sc = _preproc_public(SequenceCleanerProject,  ('output_fasta',))
        co = _preproc_public(ClustalOmegaProject,     ('output_alignment',))
        fc = _preproc_public(FormatConversionProject, ('output_file',))
        for p in fv: p.tool_type = 'fasta_validate'
        for p in sc: p.tool_type = 'sequence_cleaner'
        for p in co: p.tool_type = 'clustalo'
        for p in fc: p.tool_type = 'format_convert'
        for p in fv: attach_project_preview(p, 'fasta_validate', public_context=True)
        for p in sc: attach_project_preview(p, 'sequence_cleaner', public_context=True)
        for p in co: attach_project_preview(p, 'clustalo', public_context=True)
        for p in fc: attach_project_preview(p, 'format_convert', public_context=True)
        projects = sorted(fv + sc + co + fc, key=lambda x: x.created_at, reverse=True)

        PREPROC_TYPE_MAP = {
            'fasta_validator':  'fasta_validate',
            'sequence_cleaner': 'sequence_cleaner',
            'msa':              'clustalo',
            'format_converter': 'format_convert',
        }
        active_preproc_type = request.GET.get('type', '')
        if active_preproc_type in PREPROC_TYPE_MAP:
            internal = PREPROC_TYPE_MAP[active_preproc_type]
            projects = [p for p in projects if p.tool_type == internal]
        else:
            active_preproc_type = ''
        context['active_preproc_type'] = active_preproc_type

    elif active_tool == 'workflows':
        qs = WorkflowRun.objects.filter(
            visibility='public', status='SUCCESS',
        ).select_related('workflow', 'user').prefetch_related('workflow__steps').order_by('-created_at')
        projects = [p for p in qs if has_files(p, ('output_file',))]
        for p in projects:
            p.tool_type = 'workflow_run'
            p.workflow_steps = (
                list(p.workflow.steps.order_by('order')) if p.workflow
                else _snapshot_steps_from_run(p)
            )
            attach_project_preview(p, 'workflow_run', public_context=True)

    else:
        hmmbuild  = _hmm_public(HMMBuildProject,   ('hmm_file',))
        hmmsearch = _hmm_public(HMMSearchProject,  ('out_file', 'tblout_file', 'domtbl_file'))
        hmmemit   = _hmm_public(HMMEmitProject,    ('output_file',))
        fv  = _preproc_public(FASTAValidationProject,  ('input_fasta',),              {'stats__isnull': False})
        sc  = _preproc_public(SequenceCleanerProject,  ('output_fasta',))
        co  = _preproc_public(ClustalOmegaProject,     ('output_alignment',))
        fc  = _preproc_public(FormatConversionProject, ('output_file',))
        wfq = WorkflowRun.objects.filter(
            visibility='public', status='SUCCESS'
        ).select_related('workflow', 'user').prefetch_related('workflow__steps')
        wf  = [p for p in wfq if has_files(p, ('output_file',))]

        for p in hmmbuild:  p.tool_type = 'hmmbuild'
        for p in hmmsearch: p.tool_type = 'hmmsearch'
        for p in hmmemit:   p.tool_type = 'hmmemit'
        for p in fv:  p.tool_type = 'fasta_validate'
        for p in sc:  p.tool_type = 'sequence_cleaner'
        for p in co:  p.tool_type = 'clustalo'
        for p in fc:  p.tool_type = 'format_convert'
        for p in wf:  p.tool_type = 'workflow_run'
        for p in wf:  p.workflow_steps = (
            list(p.workflow.steps.order_by('order')) if p.workflow
            else _snapshot_steps_from_run(p)
        )
        for p in hmmbuild:  attach_project_preview(p, 'hmmbuild', public_context=True)
        for p in hmmsearch: attach_project_preview(p, 'hmmsearch', public_context=True)
        for p in hmmemit:   attach_project_preview(p, 'hmmemit', public_context=True)
        for p in fv:  attach_project_preview(p, 'fasta_validate', public_context=True)
        for p in sc:  attach_project_preview(p, 'sequence_cleaner', public_context=True)
        for p in co:  attach_project_preview(p, 'clustalo', public_context=True)
        for p in fc:  attach_project_preview(p, 'format_convert', public_context=True)
        for p in wf:  attach_project_preview(p, 'workflow_run', public_context=True)

        projects = sorted(
            hmmbuild + hmmsearch + hmmemit + fv + sc + co + fc + wf,
            key=lambda x: x.created_at, reverse=True,
        )

    paginator = Paginator(projects, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    context['projects'] = page_obj.object_list
    context['page_obj'] = page_obj

    return render(request, 'users/public_projects.html', context)


def get_user_history(request):
    limit = int(request.GET.get('limit', 20))

    from workflows.models import StepRun
    if request.user.is_authenticated:
        history_filter = {'user': request.user}
        step_runs = StepRun.objects.filter(
            workflow_run__user=request.user,
            content_type__isnull=False,
            object_id__isnull=False,
        )
    else:
        session_key = request.session.session_key or ''
        if not session_key:
            return JsonResponse({'history': []})
        history_filter = {'user__isnull': True, 'session_key': session_key}
        from .models import UserActionHistory as _UAH
        anon_run_ids = list(
            _UAH.objects.filter(
                user__isnull=True,
                session_key=session_key,
                tool_type='workflow',
                content_type__isnull=False,
                object_id__isnull=False,
            ).values_list('object_id', flat=True)
        )
        step_runs = StepRun.objects.filter(
            workflow_run_id__in=anon_run_ids,
            content_type__isnull=False,
            object_id__isnull=False,
        )

    step_project_refs = set(step_runs.values_list('content_type_id', 'object_id'))

    raw_items = UserActionHistory.objects.filter(
        **history_filter
    ).select_related('user', 'content_type').order_by('-timestamp')[:limit * 3]

    raw_items = [
        item for item in raw_items
        if (item.content_type_id, item.object_id) not in step_project_refs
    ]

    NON_RUN_ACTIONS = {
        'project_deleted',
        'project_shared',
        'project_unshared',
        'project_visibility_changed',
        'file_downloaded',
    }
    raw_items = [
        it for it in raw_items
        if it.action_type not in NON_RUN_ACTIONS
        and not (it.content_type_id and not it.object_id)
    ]

    terminal_refs = {
        (it.content_type_id, it.object_id)
        for it in raw_items
        if it.action_type in (
            'project_completed', 'project_failed',
            'workflow_run_completed', 'workflow_run_failed',
            'tool_completed', 'tool_failed',
        )
        and it.content_type_id and it.object_id
    }
    started_codes = ('project_created', 'workflow_run_started')
    history_items = [
        it for it in raw_items
        if not (it.action_type in started_codes
                and (it.content_type_id, it.object_id) in terminal_refs)
    ][:limit]

    STEP_META = {
        'hmmbuild':       {'step_label': 'Build profile',     'tool_name': 'HMMBUILD'},
        'hmmemit':        {'step_label': 'Emit sequences',    'tool_name': 'HMMEMIT'},
        'hmmsearch':      {'step_label': 'Search database',   'tool_name': 'HMMSEARCH'},
        'clustal_omega':  {'step_label': 'Align sequences',   'tool_name': 'CLUSTAL OMEGA'},
        'clustalo':       {'step_label': 'Align sequences',   'tool_name': 'CLUSTAL OMEGA'},
        'mafft':          {'step_label': 'Align sequences',   'tool_name': 'MAFFT'},
        'muscle':         {'step_label': 'Align sequences',   'tool_name': 'MUSCLE'},
        'kalign':         {'step_label': 'Align sequences',   'tool_name': 'KALIGN'},
        'fasta_validate': {'step_label': 'Validate FASTA',    'tool_name': 'FASTA VALIDATE'},
        'sequence_clean': {'step_label': 'Clean sequences',   'tool_name': 'SEQUENCE CLEAN'},
        'sequence_cleaner': {'step_label': 'Clean sequences', 'tool_name': 'SEQUENCE CLEAN'},
        'format_convert': {'step_label': 'Convert format',    'tool_name': 'FORMAT CONVERT'},
        'msa_trim':       {'step_label': 'Trim MSA',          'tool_name': 'MSA TRIM'},
    }

    data = []
    for item in history_items:
        action_code = item.action_type
        item_status = item.status

        if action_code in ('project_created', 'workflow_run_started'):
            try:
                proj = item.project
                real_status = getattr(proj, 'task_status', None) or getattr(proj, 'status', None)
                if real_status == 'SUCCESS':
                    action_code = ('workflow_run_completed'
                                   if action_code == 'workflow_run_started'
                                   else 'project_completed')
                    item_status = 'success'
                elif real_status == 'FAILURE':
                    action_code = ('workflow_run_failed'
                                   if action_code == 'workflow_run_started'
                                   else 'project_failed')
                    item_status = 'failure'
            except Exception:
                pass

        entry = {
            'id': item.id,
            'action_type': item.get_action_type_display(),
            'action_type_code': action_code,
            'tool_type': item.tool_type.upper(),
            'project_name': item.project_name,
            'timestamp': item.timestamp.isoformat(),
            'status': item_status,
            'description': item.description,
            'url': item.get_project_url(action_type_override=action_code),
            'steps': [],
        }

        if action_code in ('workflow_run_started', 'workflow_run_completed', 'workflow_run_failed'):
            try:
                run = item.project
                if run is None:
                    entry['deleted'] = True
                else:
                    run_failed = (run.status == 'FAILURE')
                    for sr in run.step_runs.select_related('step').order_by('step_order_snapshot'):
                        tool = sr.step.tool_type if sr.step else sr.tool_type_snapshot
                        meta = STEP_META.get(tool, {
                            'step_label': tool.replace('_', ' ').title(),
                            'tool_name':  tool.upper().replace('_', ' '),
                        })
                        status = sr.status
                        if run_failed and status in ('PENDING', 'RUNNING'):
                            status = 'SKIPPED'
                        entry['steps'].append({
                            'order':      sr.step.order,
                            'tool_type':  tool,
                            'step_label': meta['step_label'],
                            'tool_name':  meta['tool_name'],
                            'status':     status,
                        })
            except Exception:
                pass

        data.append(entry)

    return JsonResponse({'history': data})


def delete_history_entry(request, history_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    try:
        if request.user.is_authenticated:
            entry = UserActionHistory.objects.get(id=history_id, user=request.user)
        else:
            session_key = request.session.session_key or ''
            if not session_key:
                return JsonResponse({'success': False, 'error': 'Not found'}, status=404)
            entry = UserActionHistory.objects.get(
                id=history_id, user__isnull=True, session_key=session_key,
            )
        entry.delete()
        return JsonResponse({'success': True})
    except UserActionHistory.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def clear_user_history(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        if request.user.is_authenticated:
            qs = UserActionHistory.objects.filter(user=request.user)
        else:
            session_key = request.session.session_key or ''
            if not session_key:
                return JsonResponse({'success': True, 'deleted_count': 0})
            qs = UserActionHistory.objects.filter(user__isnull=True, session_key=session_key)
        deleted_count, _ = qs.delete()
        return JsonResponse({'success': True, 'deleted_count': deleted_count})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
