"""
Pages for building HMM profiles: the build form, task status and
downloading the finished profile.
"""

from .forms import HMMBuildForm
from django.http import FileResponse, Http404, JsonResponse
from .models import HMMBuildProject
import os
import shutil
from django.conf import settings
from django.shortcuts import render, redirect
import uuid
from .tasks import run_hmmbuild
from celery.result import AsyncResult
from users.history_utils import log_user_action


_BIOPYTHON_FMT = {
    'stockholm': 'stockholm',
    'clustal':   'clustal',
    'afa':       'fasta',
    'phylip':    'phylip',
    'phylips':   'phylip-sequential',
}

_HMMER_INFORMATS = frozenset({
    'stockholm', 'a2m', 'afa', 'psiblast', 'clustal', 'phylip', 'phylips', 'selex',
})


def _detect_alignment_format(file_path):
    try:
        with open(file_path, 'r', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('# STOCKHOLM'):
                    return 'stockholm'
                if line.upper().startswith('CLUSTAL'):
                    return 'clustal'
                if line.startswith('>'):
                    return 'afa'
                tokens = line.split()
                if len(tokens) >= 2 and all(t.lstrip('-').isdigit() for t in tokens[:2]):
                    return 'phylip'
                return None
    except OSError:
        return None
    return None


def _prevalidate_alignment(msa_path, informat=None, singlemx=False):
    if not os.path.exists(msa_path):
        return False, "Could not read the uploaded alignment file. Please upload it again."

    try:
        if os.path.getsize(msa_path) == 0:
            return False, "The uploaded alignment file is empty."
    except OSError:
        pass

    if informat:
        informat = informat.lower()
        if informat not in _HMMER_INFORMATS:
            return False, (
                f"Unknown alignment format: {informat!r}. "
                "Pick one of stockholm, a2m, afa, psiblast, clustal, phylip, phylips, selex."
            )
        biopython_fmt = _BIOPYTHON_FMT.get(informat)
        if biopython_fmt is None:
            return True, ''
        try:
            from Bio import AlignIO
            alignment = AlignIO.read(msa_path, biopython_fmt)
        except ValueError as e:
            msg = str(e).lower()
            if 'different lengths' in msg or 'length' in msg or 'not all sequences' in msg:
                return False, (
                    "Sequences have different lengths - this file is not aligned. "
                    "Run MSA first, then use the aligned/Stockholm "
                    "output for HMMBUILD."
                )
            return False, (
                f"Could not read the file as {informat}. "
                "Check that the input format matches your --informat selection."
            )
        except Exception:
            return False, (
                f"Could not read the file as {informat}. "
                "Check that the input format matches your --informat selection."
            )
    else:
        fmt = _detect_alignment_format(msa_path)
        if fmt is None:
            return True, ''
        try:
            from Bio import AlignIO
            alignment = AlignIO.read(msa_path, _BIOPYTHON_FMT[fmt])
        except ValueError as e:
            msg = str(e).lower()
            if 'different lengths' in msg or 'length' in msg or 'not all sequences' in msg:
                return False, (
                    "Sequences have different lengths - this file is not aligned. "
                    "Run MSA first, then use the aligned/Stockholm "
                    "output for HMMBUILD."
                )
            return False, (
                "Could not read the file as an alignment. Pick the input format "
                "explicitly under --informat if autodetect cannot recognise it."
            )
        except Exception:
            return False, (
                "Could not read the file as an alignment. Pick the input format "
                "explicitly under --informat if autodetect cannot recognise it."
            )

    if len(alignment) < 2 and not singlemx:
        return False, (
            "HMMBUILD needs at least 2 aligned sequences. The file contains "
            f"{len(alignment)}. Tip: enable --singlemx (Additional parameters "
            "-> Single-sequence scoring) to build a profile from a single "
            "sequence using a substitution matrix."
        )

    lengths = {len(rec.seq) for rec in alignment}
    if len(lengths) > 1:
        return False, (
            "Sequences have different lengths - this file is not aligned. "
            "Run MSA first, then use the aligned/Stockholm "
            "output for HMMBUILD."
        )

    return True, ''


def hmmbuild_form(request):
    _SESSION_KEYS = (
        'hmmbuild_preloaded_msa_path',
        'hmmbuild_preloaded_msa_name',
        'hmmbuild_preloaded_msa_label',
        'hmmbuild_preloaded_source_id',
        'hmmbuild_preloaded_source_type',
    )

    from_clustalo = request.GET.get('from_clustalo')
    if from_clustalo and request.method == 'GET':
        from msa_tools.models import ClustalOmegaProject
        try:
            src = ClustalOmegaProject.objects.get(id=from_clustalo)
            allowed = (
                src.user is None
                or (request.user.is_authenticated and src.user_id == request.user.id)
            )
            if allowed and src.output_alignment:
                src_path = os.path.join(settings.MEDIA_ROOT, str(src.output_alignment))
                if os.path.exists(src_path):
                    request.session['hmmbuild_preloaded_msa_path'] = src_path
                    request.session['hmmbuild_preloaded_msa_name'] = os.path.basename(src_path)
                    request.session['hmmbuild_preloaded_msa_label'] = src.name or 'MSA result'
                    request.session['hmmbuild_preloaded_source_id'] = str(src.id)
                    request.session['hmmbuild_preloaded_source_type'] = 'clustalo'
        except ClustalOmegaProject.DoesNotExist:
            pass

    from_convert = request.GET.get('from_convert')
    if from_convert and request.method == 'GET':
        from msa_tools.models import FormatConversionProject
        try:
            src = FormatConversionProject.objects.get(id=from_convert)
            allowed = (
                src.user is None
                or (request.user.is_authenticated and src.user_id == request.user.id)
            )
            if allowed and src.output_file:
                src_path = os.path.join(settings.MEDIA_ROOT, str(src.output_file))
                if os.path.exists(src_path):
                    request.session['hmmbuild_preloaded_msa_path'] = src_path
                    request.session['hmmbuild_preloaded_msa_name'] = os.path.basename(src_path)
                    request.session['hmmbuild_preloaded_msa_label'] = src.name or 'Format Converter result'
                    request.session['hmmbuild_preloaded_source_id'] = str(src.id)
                    request.session['hmmbuild_preloaded_source_type'] = 'convert'
        except FormatConversionProject.DoesNotExist:
            pass

    preloaded_msa_label = request.session.get('hmmbuild_preloaded_msa_label')
    preloaded_msa_path = request.session.get('hmmbuild_preloaded_msa_path')
    preloaded_source_id = request.session.get('hmmbuild_preloaded_source_id')
    preloaded_source_type = request.session.get('hmmbuild_preloaded_source_type', 'clustalo')
    has_preloaded_msa = bool(preloaded_msa_path and os.path.exists(preloaded_msa_path))

    if preloaded_msa_path and not has_preloaded_msa:
        for key in _SESSION_KEYS:
            request.session.pop(key, None)
        preloaded_msa_label = None
        preloaded_source_id = None
        preloaded_source_type = None

    if request.method == 'POST' and not has_preloaded_msa:
        pid = request.POST.get('preloaded_source_id', '').strip()
        src_type = request.POST.get('preloaded_source_type', '').strip()
        if pid:
            if src_type == 'convert':
                from msa_tools.models import FormatConversionProject
                try:
                    src = FormatConversionProject.objects.get(id=pid)
                    allowed = (
                        src.user is None
                        or (request.user.is_authenticated and src.user_id == request.user.id)
                    )
                    if allowed and src.output_file:
                        src_path = os.path.join(settings.MEDIA_ROOT, str(src.output_file))
                        if os.path.exists(src_path):
                            preloaded_msa_path = src_path
                            preloaded_msa_label = src.name or 'Format Converter result'
                            has_preloaded_msa = True
                            preloaded_source_id = pid
                            preloaded_source_type = 'convert'
                except FormatConversionProject.DoesNotExist:
                    pass
            else:
                from msa_tools.models import ClustalOmegaProject
                try:
                    src = ClustalOmegaProject.objects.get(id=pid)
                    allowed = (
                        src.user is None
                        or (request.user.is_authenticated and src.user_id == request.user.id)
                    )
                    if allowed and src.output_alignment:
                        src_path = os.path.join(settings.MEDIA_ROOT, str(src.output_alignment))
                        if os.path.exists(src_path):
                            preloaded_msa_path = src_path
                            preloaded_msa_label = src.name or 'MSA result'
                            has_preloaded_msa = True
                            preloaded_source_id = pid
                            preloaded_source_type = 'clustalo'
                except ClustalOmegaProject.DoesNotExist:
                    pass

    if request.method == 'POST':
        if not preloaded_source_id:
            preloaded_source_id = request.POST.get('preloaded_source_id') or None
        if not preloaded_source_type:
            preloaded_source_type = request.POST.get('preloaded_source_type') or None

    preloaded_msa_text = (
        'A converted alignment file has been selected automatically.'
        if preloaded_source_type == 'convert'
        else 'An alignment result from MSA has been selected automatically.'
    )

    context = {
        'has_preloaded_msa': has_preloaded_msa,
        'preloaded_msa_label': preloaded_msa_label,
        'preloaded_msa_text': preloaded_msa_text,
        'preloaded_source_id': preloaded_source_id,
        'preloaded_source_type': preloaded_source_type,
    }

    if request.method == "POST":
        form = HMMBuildForm(request.POST, request.FILES, preloaded_msa=has_preloaded_msa)

        if not form.is_valid():
            context['form'] = form
            return render(request, "hmmbuild_form.html", context)

        user_input_name = form.cleaned_data.get("name") or "Untitled project"
        from biologine_aplikacija.parameter_builder.form_helpers import (
            extract_params_from_cleaned_data,
            extract_file_uploads,
        )
        custom_params = extract_params_from_cleaned_data(form.cleaned_data, form._schema)
        unique_id = uuid.uuid4().hex[:8]
        file_prefix = f"hmmbuild_{unique_id}"
        hmmbuild_dir = os.path.join(settings.MEDIA_ROOT, "hmmbuild")
        os.makedirs(hmmbuild_dir, exist_ok=True)
        file_params = extract_file_uploads(
            form.cleaned_data, form._schema, hmmbuild_dir,
            filename_prefix=f"{file_prefix}_",
        )
        custom_params.update(file_params)

        if has_preloaded_msa and not form.cleaned_data.get('msa_file'):
            preloaded_name = os.path.basename(preloaded_msa_path)
            original_ext = os.path.splitext(preloaded_name)[1].lower() or '.sto'
            msa_filename = f"{file_prefix}{original_ext}"
            hmm_filename = f"{file_prefix}.hmm"
            msa_path = os.path.join(hmmbuild_dir, msa_filename)
            hmm_path = os.path.join(hmmbuild_dir, hmm_filename)
            try:
                shutil.copy2(preloaded_msa_path, msa_path)
            except OSError:
                form.add_error(None, "Could not copy preloaded alignment. Please try again.")
                context['form'] = form
                return render(request, "hmmbuild_form.html", context)
        else:
            from biologine_aplikacija.input_utils import resolve_input_payload, write_input_payload
            file_obj, text, display_name, ext = resolve_input_payload(
                form, file_field='msa_file', paste_field='pasted_text',
                source_field='input_source', default_ext='.fasta',
            )
            original_ext = ext
            msa_filename = f"{file_prefix}{original_ext}"
            hmm_filename = f"{file_prefix}.hmm"
            msa_path = os.path.join(hmmbuild_dir, msa_filename)
            hmm_path = os.path.join(hmmbuild_dir, hmm_filename)
            try:
                write_input_payload(file_obj, text, msa_path)
            except OSError:
                form.add_error(None, "Could not save the input. Please try again.")
                context['form'] = form
                return render(request, "hmmbuild_form.html", context)

        ok, validation_error = _prevalidate_alignment(
            msa_path,
            informat=custom_params.get('informat'),
            singlemx=(custom_params.get('singlemx_mode') == 'on'),
        )
        if not ok:
            try:
                if os.path.exists(msa_path):
                    os.remove(msa_path)
            except OSError:
                pass
            form.add_error(None, validation_error)
            context['form'] = form
            return render(request, "hmmbuild_form.html", context)

        save_annotated = bool(form.cleaned_data.get('save_annotated_msa'))
        annotated_filename = f"{file_prefix}_annotated.sto" if save_annotated else None
        annotated_path = (
            os.path.join(hmmbuild_dir, annotated_filename) if save_annotated else None
        )

        matrix_file_value = ""
        if 'mxfile' in custom_params:
            matrix_filename = os.path.basename(custom_params['mxfile'])
            matrix_file_value = f"hmmbuild/{matrix_filename}"

        project = HMMBuildProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            msa_file=f"hmmbuild/{msa_filename}",
            hmm_file=f"hmmbuild/{hmm_filename}",
            annotated_msa_file=f"hmmbuild/{annotated_filename}" if save_annotated else "",
            matrix_file=matrix_file_value,
            parameters=custom_params,
            task_status='PENDING',
        )

        task = run_hmmbuild.delay(
            project.id, msa_path, hmm_path,
            parameters=custom_params,
            annotated_msa_path=annotated_path,
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])

        log_user_action(
            user=request.user if request.user.is_authenticated else None,
            action_type='project_created',
            tool_type='hmmbuild',
            project=project,
            project_name=user_input_name,
            description='Created HMMBUILD project',
            metadata={},
            request=request,
        )

        for key in _SESSION_KEYS:
            request.session.pop(key, None)

        return redirect('hmmbuild_status', project_id=project.id)

    context['form'] = HMMBuildForm()
    return render(request, "hmmbuild_form.html", context)


def hmmbuild_dismiss_preload(request):
    for key in ('hmmbuild_preloaded_msa_path', 'hmmbuild_preloaded_msa_name',
                'hmmbuild_preloaded_msa_label', 'hmmbuild_preloaded_source_id',
                'hmmbuild_preloaded_source_type'):
        request.session.pop(key, None)
    return redirect('hmmbuild_form')


def _parse_hmm_stats(hmm_text):
    stats = {}
    if not hmm_text:
        return stats
    for line in hmm_text.splitlines()[:40]:
        if line.startswith('//'):
            break
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts[0], parts[1].strip()
        if key in ('NAME', 'LENG', 'NSEQ', 'ALPH', 'M', 'EFFN', 'CKSUM'):
            stats[key.lower()] = val
    return stats


def _hmm_preview(hmm_text, max_lines=2000):
    from biologine_aplikacija.preview_utils import read_text_preview
    preview = read_text_preview(hmm_text, max_lines=max_lines, max_chars=500000)
    note = f"\n\n{preview.note}" if preview.note else ''
    text = f"{preview.text}{note}" if preview.text else ''
    return text, 1 if preview.is_truncated else 0, preview


def hmmbuild_status(request, project_id):
    try:
        project = HMMBuildProject.objects.get(id=project_id)
        if request.user.is_authenticated and project.user and project.user != request.user:
            raise Http404("Project not found")
    except HMMBuildProject.DoesNotExist:
        raise Http404("Project not found")

    from biologine_aplikacija.parameter_builder import build_parameter_overrides
    context = {
        'project': project,
        'form': HMMBuildForm(),
        'can_download_output': project.can_download_output(request.user),
        'can_download_input': project.can_download_input(request.user),
        'input_warning': (project.parameters or {}).get('_input_warning'),
        'parameter_overrides': build_parameter_overrides(project, 'hmmbuild'),
    }

    if project.task_status == 'SUCCESS' and project.result_text:
        hmm_filename = os.path.basename(project.hmm_file.name) if project.hmm_file else None
        preview_text, remaining, preview_obj = _hmm_preview(project.result_text)
        stats = _parse_hmm_stats(project.result_text)
        from biologine_aplikacija.preview_utils import build_preview_note, read_text_preview

        annotated_preview = None
        annotated_preview_note = None
        if project.annotated_msa_file:
            try:
                with open(os.path.join(settings.MEDIA_ROOT, str(project.annotated_msa_file)), 'r') as f:
                    annotated_text = f.read()
                preview = read_text_preview(annotated_text, max_lines=30, max_chars=500000)
                note = f"\n\n{preview.note}" if preview.note else ''
                annotated_preview = f"{preview.text}{note}" if preview.text else ''
                annotated_preview_note = build_preview_note(preview, 'the annotated MSA')
            except OSError:
                annotated_preview = None

        context.update({
            'hmm_model_text': project.result_text,
            'hmm_model_preview': preview_text,
            'hmm_preview_remaining': remaining,
            'hmm_preview_note': build_preview_note(preview_obj, 'the HMM profile'),
            'hmm_model_name': hmm_filename,
            'hmm_stats': stats,
            'annotated_preview': annotated_preview,
            'annotated_preview_note': annotated_preview_note,
        })

    return render(request, 'hmmbuild_status.html', context)


def hmmbuild_task_status(request, task_id):
    task = AsyncResult(task_id)

    response_data = {
        'task_id': task_id,
        'status': task.state,
        'result': None
    }

    if task.state == 'PENDING':
        response_data['message'] = 'Task queued...'
        response_data['progress'] = 0
    elif task.state == 'STARTED':
        response_data['message'] = task.info.get('message', 'Building HMM profile...')
        response_data['progress'] = task.info.get('progress', 50)
    elif task.state == 'SUCCESS':
        response_data['message'] = 'HMM build completed successfully'
        response_data['progress'] = 100
        response_data['result'] = task.result
    elif task.state == 'FAILURE':
        response_data['message'] = 'Task failed'
        response_data['progress'] = 100
        friendly = None
        try:
            project = HMMBuildProject.objects.filter(task_id=task_id).first()
            if project and project.result_text and project.result_text.startswith('ERROR:'):
                friendly = project.result_text[len('ERROR:'):].strip()
        except Exception:
            pass
        response_data['error'] = friendly or 'HMM profile building failed. Please check your alignment and try again.'
    else:
        response_data['message'] = str(task.state)
        response_data['progress'] = 50

    return JsonResponse(response_data)


def _serve_file(file_field, download_name):
    if not file_field:
        raise Http404("File not available.")
    file_path = os.path.join(settings.MEDIA_ROOT, str(file_field))
    if not os.path.exists(file_path):
        raise Http404("File not found on server.")
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=download_name)


def hmmbuild_input_download(request, project_id):
    from django.shortcuts import get_object_or_404
    project = get_object_or_404(HMMBuildProject, pk=project_id)
    if not project.can_download_input(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")
    return _serve_file(project.msa_file, 'alignment.msa')


def hmmbuild_output_download(request, project_id):
    from django.shortcuts import get_object_or_404
    project = get_object_or_404(HMMBuildProject, pk=project_id)
    if not project.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")
    return _serve_file(project.hmm_file, 'profile.hmm')


def hmmbuild_annotated_msa_download(request, project_id):
    from django.shortcuts import get_object_or_404
    project = get_object_or_404(HMMBuildProject, pk=project_id)
    if not project.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")
    return _serve_file(project.annotated_msa_file, 'annotated.sto')
