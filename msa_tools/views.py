"""
Pages for multiple sequence alignment and format conversion: picking the
tool, running it and downloading the aligned output.
"""

import os
import uuid
import shutil
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, FileResponse, JsonResponse
from django.conf import settings
from celery.result import AsyncResult

from Bio import AlignIO
from Bio.Align import MultipleSeqAlignment

from .forms import ClustalOmegaForm, FormatConvertForm
from .models import ClustalOmegaProject, FormatConversionProject
from .tasks import run_clustalo, run_mafft, run_muscle, run_kalign
from biologine_aplikacija.input_utils import resolve_input_payload, write_input_payload

VALID_TOOLS = {'clustalo', 'mafft', 'muscle', 'kalign'}
TASK_FOR_TOOL = {
    'clustalo': run_clustalo,
    'mafft': run_mafft,
    'muscle': run_muscle,
    'kalign': run_kalign,
}
TOOL_BINARY = {
    'clustalo': 'clustalo',
    'mafft': 'mafft',
    'muscle': 'muscle',
    'kalign': 'kalign',
}


def _resolve_tool(request):
    raw = (
        request.POST.get('tool')
        or request.GET.get('tool')
        or 'clustalo'
    )
    return raw if raw in VALID_TOOLS else 'clustalo'


def _output_ext_for(output_format):
    return {
        'stockholm': '.sto',
        'clustal':   '.aln',
        'fasta':     '.fasta',
        'phylip':    '.phy',
        'vienna':    '.vie',
    }.get(output_format, '.fasta')

BIOPYTHON_FMT = {
    'fasta': 'fasta',
    'clustal': 'clustal',
    'stockholm': 'stockholm',
}
OUTPUT_EXT = {
    'fasta': '.fasta',
    'clustal': '.aln',
    'stockholm': '.sto',
}


def _is_project_owner(request, project):
    return request.user.is_authenticated and project.user_id == request.user.id


def _download_file_field(file_field, download_name, missing_message):
    if not file_field:
        raise Http404(missing_message)

    file_path = os.path.join(settings.MEDIA_ROOT, str(file_field))
    if not os.path.exists(file_path):
        raise Http404("File not found on server.")

    response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return response


def _detect_alignment_format(file_path):
    try:
        with open(file_path, 'r', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('# STOCKHOLM'):
                    return 'stockholm'
                if line.upper().startswith('CLUSTAL') or line.upper().startswith('KALIGN'):
                    return 'clustal'
                if line.startswith('>'):
                    return 'fasta'
                return None
    except OSError:
        return None
    return None


def _convert_alignment(in_path, in_fmt, out_path, out_fmt):
    try:
        with open(in_path, 'rb') as _src:
            _raw = _src.read()
        _ascii = _raw.decode('ascii', errors='ignore').encode('ascii')
        if _ascii != _raw:
            with open(in_path, 'wb') as _dst:
                _dst.write(_ascii)
    except OSError:
        pass

    try:
        alignment = AlignIO.read(in_path, BIOPYTHON_FMT[in_fmt])
    except Exception as e:
        msg = str(e).lower()
        if "not all sequences" in msg or "different lengths" in msg or "length" in msg:
            return None, None, (
                "Not all sequences are the same length. "
                "This does not appear to be an aligned file. Run MSA first."
            )
        return None, None, f"Could not read the file as {in_fmt.upper()} format. Check that the format is correct."

    seq_count = len(alignment)
    aln_length = alignment.get_alignment_length()

    lengths = set(len(r) for r in alignment)
    if len(lengths) > 1:
        return None, None, (
            "Sequences have different lengths. "
            "This does not appear to be an aligned file. Run MSA first."
        )

    try:
        with open(out_path, 'w') as f:
            AlignIO.write(alignment, f, BIOPYTHON_FMT[out_fmt])
    except Exception:
        return None, None, "Could not write the output file. Please try again."

    return seq_count, aln_length, None


def _base_name(name):
    if not name:
        return "Project"
    for suffix in (' - HMM', ' - MSA', ' - Stockholm', ' - Converted'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return os.path.splitext(name)[0] or "Project"


def _is_tool_available(tool):
    return shutil.which(TOOL_BINARY.get(tool, 'clustalo')) is not None


def clustalo_form(request):
    _SESSION_KEYS = (
        'clustalo_preloaded_fasta_path',
        'clustalo_preloaded_fasta_name',
        'clustalo_preloaded_fasta_label',
        'clustalo_preloaded_source_id',
        'clustalo_preloaded_source_type',
    )

    from_clean = request.GET.get('from_clean')
    if from_clean and request.method == 'GET':
        from sequence_tools.models import SequenceCleanerProject
        try:
            src = SequenceCleanerProject.objects.get(id=from_clean)
            allowed = (
                src.user is None
                or (request.user.is_authenticated and src.user_id == request.user.id)
            )
            if allowed and src.output_fasta:
                src_path = os.path.join(settings.MEDIA_ROOT, str(src.output_fasta))
                if os.path.exists(src_path):
                    request.session['clustalo_preloaded_fasta_path'] = src_path
                    request.session['clustalo_preloaded_fasta_name'] = os.path.basename(src_path)
                    request.session['clustalo_preloaded_fasta_label'] = src.name or 'Sequence Cleaner result'
                    request.session['clustalo_preloaded_source_id'] = str(src.id)
                    request.session['clustalo_preloaded_source_type'] = 'sequence_cleaner'
        except SequenceCleanerProject.DoesNotExist:
            pass

    from_fasta_validate = request.GET.get('from_fasta_validate')
    if from_fasta_validate and request.method == 'GET':
        from sequence_tools.models import FASTAValidationProject
        try:
            src = FASTAValidationProject.objects.get(id=from_fasta_validate)
            allowed = (
                src.user is None
                or (request.user.is_authenticated and src.user_id == request.user.id)
            )
            if allowed and src.input_fasta:
                src_path = os.path.join(settings.MEDIA_ROOT, str(src.input_fasta))
                if os.path.exists(src_path):
                    request.session['clustalo_preloaded_fasta_path'] = src_path
                    request.session['clustalo_preloaded_fasta_name'] = os.path.basename(src_path)
                    request.session['clustalo_preloaded_fasta_label'] = src.name or 'FASTA Validate result'
                    request.session['clustalo_preloaded_source_id'] = str(src.id)
                    request.session['clustalo_preloaded_source_type'] = 'fasta_validator'
        except FASTAValidationProject.DoesNotExist:
            pass

    preloaded_fasta_label = request.session.get('clustalo_preloaded_fasta_label')
    preloaded_fasta_path = request.session.get('clustalo_preloaded_fasta_path')
    preloaded_source_id = request.session.get('clustalo_preloaded_source_id')
    preloaded_source_type = request.session.get('clustalo_preloaded_source_type', 'fasta_validator')
    has_preloaded_fasta = bool(preloaded_fasta_path and os.path.exists(preloaded_fasta_path))

    if preloaded_fasta_path and not has_preloaded_fasta:
        for key in _SESSION_KEYS:
            request.session.pop(key, None)
        preloaded_fasta_label = None
        preloaded_source_id = None
        preloaded_source_type = None

    if request.method == 'POST' and not has_preloaded_fasta:
        pid = request.POST.get('preloaded_source_id', '').strip()
        src_type = request.POST.get('preloaded_source_type', '').strip()
        if pid:
            if src_type == 'sequence_cleaner':
                from sequence_tools.models import SequenceCleanerProject
                try:
                    src = SequenceCleanerProject.objects.get(id=pid)
                    allowed = (
                        src.user is None
                        or (request.user.is_authenticated and src.user_id == request.user.id)
                    )
                    if allowed and src.output_fasta:
                        src_path = os.path.join(settings.MEDIA_ROOT, str(src.output_fasta))
                        if os.path.exists(src_path):
                            preloaded_fasta_path = src_path
                            preloaded_fasta_label = src.name or 'Sequence Cleaner result'
                            has_preloaded_fasta = True
                            preloaded_source_id = pid
                            preloaded_source_type = 'sequence_cleaner'
                except SequenceCleanerProject.DoesNotExist:
                    pass
            else:
                from sequence_tools.models import FASTAValidationProject
                try:
                    src = FASTAValidationProject.objects.get(id=pid)
                    allowed = (
                        src.user is None
                        or (request.user.is_authenticated and src.user_id == request.user.id)
                    )
                    if allowed and src.input_fasta:
                        src_path = os.path.join(settings.MEDIA_ROOT, str(src.input_fasta))
                        if os.path.exists(src_path):
                            preloaded_fasta_path = src_path
                            preloaded_fasta_label = src.name or 'FASTA Validate result'
                            has_preloaded_fasta = True
                            preloaded_source_id = pid
                            preloaded_source_type = 'fasta_validator'
                except FASTAValidationProject.DoesNotExist:
                    pass

    if request.method == 'POST':
        if not preloaded_source_id:
            preloaded_source_id = request.POST.get('preloaded_source_id') or None
        if not preloaded_source_type:
            preloaded_source_type = request.POST.get('preloaded_source_type') or None

    if preloaded_source_type == 'sequence_cleaner':
        preloaded_fasta_text = 'A cleaned FASTA file from Sequence Cleaner has been selected automatically.'
    else:
        preloaded_fasta_text = 'A validated FASTA file from FASTA Validator has been selected automatically.'

    selected_tool = _resolve_tool(request)
    selected_tool_label = dict(ClustalOmegaForm.TOOL_CHOICES).get(selected_tool, selected_tool)

    def _form_ctx(form, **extra):
        ctx = {
            'form': form,
            'selected_tool': selected_tool,
            'selected_tool_label': selected_tool_label,
            'preloaded_fasta_label': preloaded_fasta_label,
            'preloaded_fasta_text': preloaded_fasta_text,
            'has_preloaded_fasta': has_preloaded_fasta,
            'preloaded_source_id': preloaded_source_id,
            'preloaded_source_type': preloaded_source_type,
        }
        ctx.update(extra)
        return ctx

    if not _is_tool_available(selected_tool):
        return render(request, 'msa_tools/clustalo_form.html', _form_ctx(
            ClustalOmegaForm(tool=selected_tool),
            clustalo_missing=True,
            missing_tool=selected_tool,
            missing_tool_binary=TOOL_BINARY[selected_tool],
        ))

    if request.method == 'POST':
        form = ClustalOmegaForm(
            request.POST, request.FILES,
            preloaded_fasta=has_preloaded_fasta,
            tool=selected_tool,
        )
        if not form.is_valid():
            return render(request, 'msa_tools/clustalo_form.html', _form_ctx(form))

        output_format = form.cleaned_data['output_format']
        from biologine_aplikacija.parameter_builder.form_helpers import (
            extract_params_from_cleaned_data,
        )
        custom_params = extract_params_from_cleaned_data(
            form.cleaned_data, form._schema,
        )
        input_dir = os.path.join(settings.MEDIA_ROOT, 'msa_tools', 'clustalo', 'input')
        os.makedirs(input_dir, exist_ok=True)
        unique_id = uuid.uuid4().hex[:8]

        if has_preloaded_fasta and not form.cleaned_data.get('fasta_file') and not (form.cleaned_data.get('pasted_text') or '').strip():
            preloaded_name = os.path.basename(preloaded_fasta_path)
            display_name = preloaded_name
            user_input_name = form.cleaned_data.get('name') or f"{os.path.splitext(preloaded_name)[0]} - MSA"
            ext = os.path.splitext(preloaded_name)[1] or '.fasta'
            in_filename = f"to_align_preloaded_{unique_id}{ext}"
            in_path = os.path.join(input_dir, in_filename)
            try:
                shutil.copy2(preloaded_fasta_path, in_path)
            except OSError:
                form.add_error(None, "Could not copy preloaded input. Please try again.")
                return render(request, 'msa_tools/clustalo_form.html', _form_ctx(form))
        else:
            file_obj, text, display_name, ext = resolve_input_payload(form, file_field='fasta_file')
            user_input_name = form.cleaned_data.get('name') or display_name
            base = os.path.splitext(display_name)[0]
            safe_name = ''.join(c for c in base if c.isalnum() or c in '-_ ')[:40].strip().replace(' ', '_')
            in_filename = f"to_align_{safe_name}_{unique_id}{ext}" if safe_name else f"to_align_{unique_id}{ext}"
            in_path = os.path.join(input_dir, in_filename)
            try:
                write_input_payload(file_obj, text, in_path)
            except OSError:
                form.add_error(None, "Could not save the input. Please try again.")
                return render(request, 'msa_tools/clustalo_form.html', _form_ctx(form))

        output_dir = os.path.join(settings.MEDIA_ROOT, 'msa_tools', 'clustalo', 'output')
        os.makedirs(output_dir, exist_ok=True)
        out_ext = _output_ext_for(output_format)
        safe_in = os.path.splitext(in_filename)[0]
        out_filename = f"aligned_{safe_in}_{unique_id}{out_ext}"
        out_path = os.path.join(output_dir, out_filename)

        project = ClustalOmegaProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            tool=selected_tool,
            input_fasta=f"msa_tools/clustalo/input/{in_filename}",
            output_format=output_format,
            parameters=custom_params,
            task_status='PENDING',
        )

        task_fn = TASK_FOR_TOOL[selected_tool]
        if selected_tool == 'clustalo':
            task = task_fn.delay(
                project.id, in_path, out_path, output_format,
                threads=1, parameters=custom_params,
            )
        else:
            task = task_fn.delay(
                project.id, in_path, out_path, output_format,
                parameters=custom_params,
            )
        project.task_id = task.id
        project.save(update_fields=['task_id'])

        try:
            from users.history_utils import log_user_action
            display_tool = dict(ClustalOmegaForm.TOOL_CHOICES).get(selected_tool, selected_tool)
            log_user_action(
                user=request.user if request.user.is_authenticated else None,
                action_type='project_created',
                tool_type=selected_tool,
                project=project,
                project_name=user_input_name,
                description=f'Created MSA ({display_tool}) project',
                request=request,
            )
        except Exception:
            pass

        for key in _SESSION_KEYS:
            request.session.pop(key, None)

        return redirect('clustalo_status', project_id=project.id)

    form = ClustalOmegaForm(tool=selected_tool)
    return render(request, 'msa_tools/clustalo_form.html', _form_ctx(form))


def clustalo_dismiss_preload(request):
    for key in ('clustalo_preloaded_fasta_path', 'clustalo_preloaded_fasta_name',
                'clustalo_preloaded_fasta_label', 'clustalo_preloaded_source_id',
                'clustalo_preloaded_source_type'):
        request.session.pop(key, None)
    return redirect('clustalo_form')


_TOOL_LABEL = {
    'clustalo': 'Clustal Omega',
    'mafft': 'MAFFT',
    'muscle': 'MUSCLE',
    'kalign': 'Kalign',
}


def clustalo_status(request, project_id):
    project = get_object_or_404(ClustalOmegaProject, id=project_id)

    if not project.can_view(request.user):
        raise Http404("Project not found")

    tool = project.tool or 'clustalo'
    from django.urls import reverse
    from biologine_aplikacija.parameter_builder import build_parameter_overrides
    retry_url = reverse('clustalo_form')
    if tool != 'clustalo':
        retry_url += f'?tool={tool}'
    context = {
        'project': project,
        'form': ClustalOmegaForm(),
        'can_download_input': _is_project_owner(request, project),
        'can_download_output': project.can_view(request.user),
        'parameter_overrides': build_parameter_overrides(project, 'clustalo'),
        'tool_label': _TOOL_LABEL.get(tool, 'Clustal Omega'),
        'tool_type': tool,
        'retry_form_url': retry_url,
    }

    if project.task_status == 'SUCCESS' and project.output_alignment:
        from biologine_aplikacija.preview_utils import read_file_preview, build_preview_note
        _EXT_SUBTITLE = {
            'stockholm': 'Alignment output (.sto)',
            'clustal': 'Alignment output (.aln)',
            'fasta': 'Alignment output (.fasta)',
            'phylip': 'Alignment output (.phy)',
            'vienna': 'Alignment output (.vie)',
        }
        _OF_LABEL = {
            'stockholm': 'the Stockholm alignment',
            'clustal':   'the Clustal alignment',
            'fasta':     'the FASTA alignment',
            'phylip':    'the Phylip alignment',
            'vienna':    'the Vienna alignment',
        }
        preview = read_file_preview(project.output_alignment, max_lines=2000, max_chars=500000)
        context.update({
            'alignment_preview': preview.text,
            'alignment_preview_subtitle': _EXT_SUBTITLE.get(project.output_format, 'Alignment output'),
            'alignment_preview_note': build_preview_note(
                preview, _OF_LABEL.get(project.output_format, 'the alignment')
            ),
        })

    return render(request, 'msa_tools/clustalo_status.html', context)


def clustalo_task_status(request, task_id):
    task = AsyncResult(task_id)
    data = {'task_id': task_id, 'status': task.state}

    if task.state == 'PENDING':
        data['message'] = 'Queued...'
        data['progress'] = 0
    elif task.state == 'STARTED':
        info = task.info or {}
        data['message'] = info.get('message', 'Running alignment...')
        data['progress'] = info.get('progress', 30)
    elif task.state == 'SUCCESS':
        data['message'] = 'Alignment complete!'
        data['progress'] = 100
    elif task.state == 'FAILURE':
        data['message'] = 'Alignment failed'
        data['progress'] = 100
        try:
            project = ClustalOmegaProject.objects.get(task_id=task_id)
            data['error'] = project.error_message or str(task.info)
        except ClustalOmegaProject.DoesNotExist:
            data['error'] = str(task.info)
    else:
        data['message'] = str(task.state)
        data['progress'] = 50

    return JsonResponse(data)


def clustalo_download(request, project_id):
    project = get_object_or_404(ClustalOmegaProject, id=project_id)

    if not project.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    if not project.output_alignment:
        raise Http404("No output file available.")

    file_path = os.path.join(settings.MEDIA_ROOT, str(project.output_alignment))
    if not os.path.exists(file_path):
        raise Http404("Output file not found on server.")

    base = os.path.splitext(project.name or 'alignment')[0]
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in base).strip('_')
    ext = _output_ext_for(project.output_format)
    download_name = f"{safe_name}_aligned{ext}"

    response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return response


def clustalo_input_download(request, project_id):
    project = get_object_or_404(ClustalOmegaProject, id=project_id)

    if not project.can_download_input(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    base = os.path.splitext(project.name or 'sequences')[0]
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in base).strip('_')
    ext = os.path.splitext(str(project.input_fasta))[1] or '.fasta'
    return _download_file_field(project.input_fasta, f"{safe_name or 'sequences'}_input{ext}", "No input file available.")


_FC_PRELOAD_KEYS = (
    'fc_preloaded_path',
    'fc_preloaded_name',
    'fc_preloaded_label',
    'fc_preloaded_source_id',
    'fc_preloaded_source_type',
    'fc_preloaded_output_format',
)


def _resolve_fc_preload_source(request, source_type, source_id):
    if source_type == 'fasta_validator':
        from sequence_tools.models import FASTAValidationProject
        try:
            src = FASTAValidationProject.objects.get(id=source_id)
        except FASTAValidationProject.DoesNotExist:
            return None, None
        if src.user and src.user != request.user and not request.user.is_superuser:
            if not (request.user.is_authenticated and src.user_id == request.user.id) and src.user is not None:
                return None, None
        if not src.input_fasta:
            return None, None
        path = os.path.join(settings.MEDIA_ROOT, str(src.input_fasta))
        return (src.name or 'FASTA Validator result'), path

    if source_type == 'clustalo_fasta':
        try:
            src = ClustalOmegaProject.objects.get(id=source_id)
        except ClustalOmegaProject.DoesNotExist:
            return None, None
        if src.user and not (request.user.is_authenticated and src.user_id == request.user.id):
            return None, None
        if not src.output_alignment:
            return None, None
        path = os.path.join(settings.MEDIA_ROOT, str(src.output_alignment))
        return (src.name or 'MSA result'), path

    return None, None


_FC_PREVIEW_SUBTITLE = {
    'stockholm': 'Converted output (.sto)',
    'clustal':   'Converted output (.aln)',
    'fasta':     'Converted output (.fasta)',
}

_FC_PREVIEW_LABEL = {
    'stockholm': 'Preview Stockholm output (.sto)',
    'clustal':   'Preview Clustal output (.aln)',
    'fasta':     'Preview FASTA output (.fasta)',
}

_FC_DOWNLOAD_LABEL = {
    'stockholm': 'Download .sto',
    'clustal':   'Download .aln',
    'fasta':     'Download .fasta',
}

_FC_PREVIEW_OF_LABEL = {
    'stockholm': 'the Stockholm output',
    'clustal':   'the Clustal output',
    'fasta':     'the FASTA output',
}


def _fc_preview_context(project):
    if not project or not project.output_file or project.error_message:
        return {}
    from biologine_aplikacija.preview_utils import read_file_preview, build_preview_note
    preview = read_file_preview(project.output_file, max_lines=2000, max_chars=500000)
    fmt = project.output_format
    return {
        'output_preview': preview.text,
        'output_preview_title': _FC_PREVIEW_LABEL.get(fmt, 'Output preview'),
        'output_preview_subtitle': _FC_PREVIEW_SUBTITLE.get(fmt, 'Converted output'),
        'output_download_label': _FC_DOWNLOAD_LABEL.get(fmt, 'Download converted file'),
        'output_preview_note': build_preview_note(
            preview, _FC_PREVIEW_OF_LABEL.get(fmt, 'the converted file')
        ),
    }


def format_convert_form(request):
    if request.method == 'GET':
        from_validate = request.GET.get('from_validate')
        from_clustalo = (
            request.GET.get('from_clustalo')
            or request.GET.get('from_clustalo_fasta')
        )
        target_format = (request.GET.get('output_format') or 'stockholm').strip()
        if target_format not in OUTPUT_EXT:
            target_format = 'stockholm'

        if from_validate:
            label, path = _resolve_fc_preload_source(request, 'fasta_validator', from_validate)
            if label and path and os.path.exists(path):
                request.session['fc_preloaded_path'] = path
                request.session['fc_preloaded_name'] = os.path.basename(path)
                request.session['fc_preloaded_label'] = label
                request.session['fc_preloaded_source_id'] = str(from_validate)
                request.session['fc_preloaded_source_type'] = 'fasta_validator'
                request.session['fc_preloaded_output_format'] = target_format
        elif from_clustalo:
            label, path = _resolve_fc_preload_source(request, 'clustalo_fasta', from_clustalo)
            if label and path and os.path.exists(path):
                request.session['fc_preloaded_path'] = path
                request.session['fc_preloaded_name'] = os.path.basename(path)
                request.session['fc_preloaded_label'] = label
                request.session['fc_preloaded_source_id'] = str(from_clustalo)
                request.session['fc_preloaded_source_type'] = 'clustalo_fasta'
                request.session['fc_preloaded_output_format'] = target_format

    preloaded_path = request.session.get('fc_preloaded_path')
    preloaded_label = request.session.get('fc_preloaded_label')
    preloaded_source_id = request.session.get('fc_preloaded_source_id')
    preloaded_source_type = request.session.get('fc_preloaded_source_type')
    preloaded_output_format = request.session.get('fc_preloaded_output_format') or 'stockholm'
    has_preloaded_alignment = bool(preloaded_path and os.path.exists(preloaded_path))

    if preloaded_path and not has_preloaded_alignment:
        for key in _FC_PRELOAD_KEYS:
            request.session.pop(key, None)
        preloaded_label = None
        preloaded_source_id = None
        preloaded_source_type = None

    if request.method == 'POST' and not has_preloaded_alignment:
        pid = (request.POST.get('preloaded_source_id') or '').strip()
        src_type = (request.POST.get('preloaded_source_type') or '').strip()
        if pid and src_type:
            label, path = _resolve_fc_preload_source(request, src_type, pid)
            if label and path and os.path.exists(path):
                preloaded_path = path
                preloaded_label = label
                preloaded_source_id = pid
                preloaded_source_type = src_type
                has_preloaded_alignment = True

    if preloaded_source_type == 'clustalo_fasta':
        preloaded_text = 'An alignment from MSA has been selected automatically.'
    else:
        preloaded_text = 'A FASTA file from FASTA Validator has been selected automatically.'

    if request.method == 'POST':
        form = FormatConvertForm(
            request.POST, request.FILES,
            preloaded_alignment=has_preloaded_alignment,
        )
        if not form.is_valid():
            return render(request, 'msa_tools/format_convert.html', {
                'form': form,
                'preloaded_alignment_label': preloaded_label,
                'preloaded_alignment_text': preloaded_text,
                'has_preloaded_alignment': has_preloaded_alignment,
                'preloaded_source_id': preloaded_source_id,
                'preloaded_source_type': preloaded_source_type,
            })

        output_format = form.cleaned_data['output_format']
        input_dir = os.path.join(settings.MEDIA_ROOT, 'msa_tools', 'converter', 'input')
        os.makedirs(input_dir, exist_ok=True)
        unique_id = uuid.uuid4().hex[:8]

        if (has_preloaded_alignment
                and not form.cleaned_data.get('input_file')
                and not (form.cleaned_data.get('pasted_text') or '').strip()):
            display_name = os.path.basename(preloaded_path)
            user_input_name = form.cleaned_data.get('name') or f"{_base_name(display_name)} - {output_format.capitalize()}"
            in_ext = os.path.splitext(display_name)[1].lower() or '.fasta'
            in_filename = f"to_convert_preloaded_{unique_id}{in_ext}"
            in_path = os.path.join(input_dir, in_filename)
            try:
                shutil.copy2(preloaded_path, in_path)
            except OSError:
                form.add_error(None, "Could not copy preloaded input. Please try again.")
                return render(request, 'msa_tools/format_convert.html', {
                    'form': form,
                    'preloaded_alignment_label': preloaded_label,
                    'preloaded_alignment_text': preloaded_text,
                    'has_preloaded_alignment': has_preloaded_alignment,
                    'preloaded_source_id': preloaded_source_id,
                    'preloaded_source_type': preloaded_source_type,
                })
        else:
            file_obj, text, display_name, in_ext = resolve_input_payload(
                form, file_field='input_file', default_ext='.txt')
            user_input_name = form.cleaned_data.get('name') or display_name
            base = os.path.splitext(display_name)[0]
            safe_name = ''.join(c for c in base if c.isalnum() or c in '-_ ')[:40].strip().replace(' ', '_')
            in_filename = f"to_convert_{safe_name}_{unique_id}{in_ext}" if safe_name else f"to_convert_{unique_id}{in_ext}"
            in_path = os.path.join(input_dir, in_filename)
            try:
                write_input_payload(file_obj, text, in_path)
            except OSError:
                form.add_error(None, "Could not save the input. Please try again.")
                return render(request, 'msa_tools/format_convert.html', {
                    'form': form,
                    'preloaded_alignment_label': preloaded_label,
                    'preloaded_alignment_text': preloaded_text,
                    'has_preloaded_alignment': has_preloaded_alignment,
                    'preloaded_source_id': preloaded_source_id,
                    'preloaded_source_type': preloaded_source_type,
                })

        input_format = _detect_alignment_format(in_path)
        if input_format is None:
            form.add_error(None, "Could not detect the file format. Make sure the file is a valid FASTA, Clustal, or Stockholm alignment.")
            return render(request, 'msa_tools/format_convert.html', {
                'form': form,
                'preloaded_alignment_label': preloaded_label,
                'preloaded_alignment_text': preloaded_text,
                'has_preloaded_alignment': has_preloaded_alignment,
                'preloaded_source_id': preloaded_source_id,
                'preloaded_source_type': preloaded_source_type,
            })

        if input_format == output_format:
            form.add_error(None, f"The file is already in {output_format.upper()} format. Please choose a different output format.")
            return render(request, 'msa_tools/format_convert.html', {
                'form': form,
                'preloaded_alignment_label': preloaded_label,
                'preloaded_alignment_text': preloaded_text,
                'has_preloaded_alignment': has_preloaded_alignment,
                'preloaded_source_id': preloaded_source_id,
                'preloaded_source_type': preloaded_source_type,
            })

        output_dir = os.path.join(settings.MEDIA_ROOT, 'msa_tools', 'converter', 'output')
        os.makedirs(output_dir, exist_ok=True)
        out_ext = OUTPUT_EXT[output_format]
        safe_out = ''.join(c for c in os.path.splitext(in_filename)[0] if c.isalnum() or c in '-_')[:40]
        out_filename = f"converted_{safe_out}_{unique_id}{out_ext}" if safe_out else f"converted_{unique_id}{out_ext}"
        out_path = os.path.join(output_dir, out_filename)

        seq_count, aln_length, error = _convert_alignment(in_path, input_format, out_path, output_format)

        project = FormatConversionProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            input_file=f"msa_tools/converter/input/{in_filename}",
            input_format=input_format,
            output_format=output_format,
            sequence_count=seq_count,
            alignment_length=aln_length,
            error_message=error or '',
        )

        if not error:
            project.output_file = f"msa_tools/converter/output/{out_filename}"
            project.save(update_fields=['output_file'])

        try:
            from users.history_utils import log_user_action
            action = 'tool_failed' if error else 'tool_completed'
            status = 'failure' if error else 'success'
            log_user_action(
                request.user if request.user.is_authenticated else None,
                action, 'format_convert', project,
                user_input_name, status=status, error_message=error or '',
                request=request,
            )
        except Exception:
            pass

        for key in _FC_PRELOAD_KEYS:
            request.session.pop(key, None)

        return render(request, 'msa_tools/format_convert.html', {
            'form': FormatConvertForm(),
            'project': project,
            'can_download_input': _is_project_owner(request, project),
            **_fc_preview_context(project),
        })

    form = FormatConvertForm(
        preloaded_alignment=has_preloaded_alignment,
        initial={'output_format': preloaded_output_format},
    )
    return render(request, 'msa_tools/format_convert.html', {
        'form': form,
        'preloaded_alignment_label': preloaded_label,
        'preloaded_alignment_text': preloaded_text,
        'has_preloaded_alignment': has_preloaded_alignment,
        'preloaded_source_id': preloaded_source_id,
        'preloaded_source_type': preloaded_source_type,
    })


def format_convert_dismiss_preload(request):
    for key in _FC_PRELOAD_KEYS:
        request.session.pop(key, None)
    return redirect('format_convert_form')


def format_convert_result(request, project_id):
    project = get_object_or_404(FormatConversionProject, id=project_id)

    if not project.can_view(request.user):
        raise Http404("Project not found")

    return render(request, 'msa_tools/format_convert.html', {
        'form': FormatConvertForm(),
        'project': project,
        'can_download_input': _is_project_owner(request, project),
        **_fc_preview_context(project),
    })


def format_convert_download(request, project_id):
    project = get_object_or_404(FormatConversionProject, id=project_id)

    if not project.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    if not project.output_file:
        raise Http404("No output file available.")

    file_path = os.path.join(settings.MEDIA_ROOT, str(project.output_file))
    if not os.path.exists(file_path):
        raise Http404("Output file not found on server.")

    base = os.path.splitext(project.name or 'converted')[0]
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in base).strip('_')
    ext = OUTPUT_EXT.get(project.output_format, '.txt')
    download_name = f"{safe_name}_converted{ext}"

    response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return response


def format_convert_input_download(request, project_id):
    project = get_object_or_404(FormatConversionProject, id=project_id)

    if not project.can_download_input(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    base = os.path.splitext(project.name or 'alignment')[0]
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in base).strip('_')
    ext = os.path.splitext(str(project.input_file))[1] or '.txt'
    return _download_file_field(project.input_file, f"{safe_name or 'alignment'}_input{ext}", "No input file available.")
