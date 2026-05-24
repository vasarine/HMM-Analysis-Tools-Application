"""
Pages for the FASTA validator and sequence cleaner: running them and
serving the cleaned files for download.
"""

import os
import uuid
import shutil
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.http import Http404, FileResponse

from .forms import FASTAValidateForm, SequenceCleanForm
from .models import FASTAValidationProject, SequenceCleanerProject
from .validator import analyze_fasta
from .cleaner import clean_fasta
from biologine_aplikacija.input_utils import resolve_input_payload, write_input_payload


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


def fasta_validate_form(request):
    if request.method == 'POST':
        form = FASTAValidateForm(request.POST, request.FILES)

        if not form.is_valid():
            return render(request, 'sequence_tools/fasta_validate.html', {'form': form})

        file_obj, text, display_name, ext = resolve_input_payload(form)
        user_input_name = form.cleaned_data.get('name') or display_name

        upload_dir = os.path.join(settings.MEDIA_ROOT, 'sequence_tools', 'fasta')
        os.makedirs(upload_dir, exist_ok=True)

        unique_id = uuid.uuid4().hex[:8]
        base = os.path.splitext(display_name)[0]
        safe_name = ''.join(c for c in base if c.isalnum() or c in '-_ ')[:40].strip().replace(' ', '_')
        saved_filename = f"validated_{safe_name}_{unique_id}{ext}" if safe_name else f"validated_{unique_id}{ext}"
        saved_path = os.path.join(upload_dir, saved_filename)

        try:
            write_input_payload(file_obj, text, saved_path)
        except OSError as e:
            form.add_error(None, f"Failed to save input: {e}")
            return render(request, 'sequence_tools/fasta_validate.html', {'form': form})

        stats = analyze_fasta(saved_path)

        project = FASTAValidationProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            visibility='private',
            share_token=uuid.uuid4(),
            input_fasta=f"sequence_tools/fasta/{saved_filename}",
            stats=stats,
        )

        try:
            from users.history_utils import log_user_action
            log_user_action(
                request.user if request.user.is_authenticated else None,
                'tool_completed', 'fasta_validate', project,
                user_input_name, status='success', request=request,
            )
        except Exception:
            pass

        return render(request, 'sequence_tools/fasta_validate.html', {
            'form': FASTAValidateForm(),
            'stats': stats,
            'project': project,
            'filename': display_name,
            'can_download_input': _is_project_owner(request, project),
        })

    form = FASTAValidateForm()
    return render(request, 'sequence_tools/fasta_validate.html', {'form': form})


def fasta_validate_result(request, project_id):
    project = get_object_or_404(FASTAValidationProject, id=project_id)

    if project.user and request.user != project.user:
        if not request.user.is_authenticated:
            raise Http404("Project not found")
        raise Http404("Project not found")

    return render(request, 'sequence_tools/fasta_validate.html', {
        'form': FASTAValidateForm(),
        'stats': project.stats,
        'project': project,
        'filename': os.path.basename(project.input_fasta.name) if project.input_fasta else '',
        'can_download_input': _is_project_owner(request, project),
    })


def fasta_validate_input_download(request, project_id):
    project = get_object_or_404(FASTAValidationProject, id=project_id)

    if not project.can_download_input(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    base_name = project.name or 'sequences'
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in os.path.splitext(base_name)[0]).strip('_')
    ext = os.path.splitext(str(project.input_fasta))[1] or '.fasta'
    return _download_file_field(project.input_fasta, f"{safe_name or 'sequences'}_input{ext}", "No input file available.")


def fasta_clean_form(request):
    _SESSION_KEYS = (
        'clean_preloaded_fasta_path',
        'clean_preloaded_fasta_name',
        'clean_preloaded_fasta_label',
        'clean_preloaded_source_id',
    )

    from_fasta_validate = request.GET.get('from_fasta_validate')
    if from_fasta_validate and request.method == 'GET':
        try:
            src = FASTAValidationProject.objects.get(id=from_fasta_validate)
            allowed = (
                src.user is None
                or (request.user.is_authenticated and src.user_id == request.user.id)
            )
            if allowed and src.input_fasta:
                src_path = os.path.join(settings.MEDIA_ROOT, str(src.input_fasta))
                if os.path.exists(src_path):
                    request.session['clean_preloaded_fasta_path'] = src_path
                    request.session['clean_preloaded_fasta_name'] = os.path.basename(src_path)
                    request.session['clean_preloaded_fasta_label'] = src.name or 'FASTA Validate result'
                    request.session['clean_preloaded_source_id'] = str(src.id)
        except FASTAValidationProject.DoesNotExist:
            pass

    preloaded_fasta_label = request.session.get('clean_preloaded_fasta_label')
    preloaded_fasta_path = request.session.get('clean_preloaded_fasta_path')
    preloaded_source_id = request.session.get('clean_preloaded_source_id')
    has_preloaded_fasta = bool(preloaded_fasta_path and os.path.exists(preloaded_fasta_path))

    if preloaded_fasta_path and not has_preloaded_fasta:
        for key in _SESSION_KEYS:
            request.session.pop(key, None)
        preloaded_fasta_label = None
        preloaded_source_id = None

    if request.method == 'POST' and not has_preloaded_fasta:
        pid = request.POST.get('preloaded_source_id', '').strip()
        if pid:
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
            except FASTAValidationProject.DoesNotExist:
                pass

    if request.method == 'POST' and not preloaded_source_id:
        preloaded_source_id = request.POST.get('preloaded_source_id') or None

    if request.method == 'POST':
        form = SequenceCleanForm(request.POST, request.FILES, preloaded_fasta=has_preloaded_fasta)
        if not form.is_valid():
            return render(request, 'sequence_tools/fasta_clean.html', {
                'form': form,
                'preloaded_fasta_label': preloaded_fasta_label,
                'has_preloaded_fasta': has_preloaded_fasta,
                'preloaded_source_id': preloaded_source_id,
            })

        input_dir = os.path.join(settings.MEDIA_ROOT, 'sequence_tools', 'cleaner', 'input')
        os.makedirs(input_dir, exist_ok=True)
        unique_id = uuid.uuid4().hex[:8]

        if has_preloaded_fasta and not form.cleaned_data.get('fasta_file') and not (form.cleaned_data.get('pasted_text') or '').strip():
            preloaded_name = os.path.basename(preloaded_fasta_path)
            display_name = preloaded_name
            user_input_name = form.cleaned_data.get('name') or os.path.splitext(preloaded_name)[0]
            ext = os.path.splitext(preloaded_name)[1] or '.fasta'
            in_filename = f"to_clean_preloaded_{unique_id}{ext}"
            in_path = os.path.join(input_dir, in_filename)
            try:
                shutil.copy2(preloaded_fasta_path, in_path)
            except OSError as e:
                form.add_error(None, f"Failed to copy preloaded input: {e}")
                return render(request, 'sequence_tools/fasta_clean.html', {
                    'form': form,
                    'preloaded_fasta_label': preloaded_fasta_label,
                    'has_preloaded_fasta': has_preloaded_fasta,
                    'preloaded_source_id': preloaded_source_id,
                })
        else:
            file_obj, text, display_name, ext = resolve_input_payload(form)
            user_input_name = form.cleaned_data.get('name') or display_name
            base = os.path.splitext(display_name)[0]
            safe_name = ''.join(c for c in base if c.isalnum() or c in '-_ ')[:40].strip().replace(' ', '_')
            in_filename = f"to_clean_{safe_name}_{unique_id}{ext}" if safe_name else f"to_clean_{unique_id}{ext}"
            in_path = os.path.join(input_dir, in_filename)
            try:
                write_input_payload(file_obj, text, in_path)
            except OSError as e:
                form.add_error(None, f"Failed to save input: {e}")
                return render(request, 'sequence_tools/fasta_clean.html', {
                    'form': form,
                    'preloaded_fasta_label': preloaded_fasta_label,
                    'has_preloaded_fasta': has_preloaded_fasta,
                    'preloaded_source_id': preloaded_source_id,
                })

        options = {
            'sequence_type': form.cleaned_data.get('sequence_type', 'auto'),
            'invalid_char_strategy': form.cleaned_data.get('invalid_char_strategy', 'replace'),
            'remove_gaps': form.cleaned_data.get('remove_gaps', False),
            'remove_stop_chars': form.cleaned_data.get('remove_stop_chars', False),
            'uppercase': form.cleaned_data.get('uppercase', False),
            'remove_duplicate_ids': form.cleaned_data.get('remove_duplicate_ids', False),
            'remove_duplicate_seqs': form.cleaned_data.get('remove_duplicate_seqs', False),
            'min_length': form.cleaned_data.get('min_length'),
            'max_length': form.cleaned_data.get('max_length'),
            'max_ambiguity_percent': form.cleaned_data.get('max_ambiguity_percent'),
        }

        output_dir = os.path.join(settings.MEDIA_ROOT, 'sequence_tools', 'cleaner', 'output')
        stats, out_path = clean_fasta(in_path, options, output_dir)

        out_relative = None
        if out_path:
            out_relative = os.path.relpath(out_path, settings.MEDIA_ROOT)

        project = SequenceCleanerProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            input_fasta=f"sequence_tools/cleaner/input/{in_filename}",
            output_fasta=out_relative,
            options=options,
            stats=stats,
        )

        try:
            from users.history_utils import log_user_action
            log_user_action(
                request.user if request.user.is_authenticated else None,
                'tool_completed', 'sequence_cleaner', project,
                user_input_name, status='success', request=request,
            )
        except Exception:
            pass

        for key in _SESSION_KEYS:
            request.session.pop(key, None)

        return render(request, 'sequence_tools/fasta_clean.html', {
            'form': form,
            'stats': stats,
            'project': project,
            'filename': display_name,
            'has_output': bool(out_path),
            'can_download_input': _is_project_owner(request, project),
        })

    form = SequenceCleanForm()
    return render(request, 'sequence_tools/fasta_clean.html', {
        'form': form,
        'preloaded_fasta_label': preloaded_fasta_label,
        'has_preloaded_fasta': has_preloaded_fasta,
        'preloaded_source_id': preloaded_source_id,
    })


def fasta_clean_dismiss_preload(request):
    for key in ('clean_preloaded_fasta_path', 'clean_preloaded_fasta_name', 'clean_preloaded_fasta_label', 'clean_preloaded_source_id'):
        request.session.pop(key, None)
    return redirect('fasta_clean')


def fasta_clean_result(request, project_id):
    project = get_object_or_404(SequenceCleanerProject, id=project_id)

    if project.user and request.user != project.user:
        raise Http404("Project not found")

    return render(request, 'sequence_tools/fasta_clean.html', {
        'form': SequenceCleanForm(),
        'stats': project.stats,
        'project': project,
        'filename': os.path.basename(project.input_fasta.name) if project.input_fasta else '',
        'has_output': bool(project.output_fasta),
        'view_only': True,
        'can_download_input': _is_project_owner(request, project),
    })


def fasta_clean_input_download(request, project_id):
    project = get_object_or_404(SequenceCleanerProject, id=project_id)

    if not project.can_download_input(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    base_name = project.name or 'sequences'
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in os.path.splitext(base_name)[0]).strip('_')
    ext = os.path.splitext(str(project.input_fasta))[1] or '.fasta'
    return _download_file_field(project.input_fasta, f"{safe_name or 'sequences'}_input{ext}", "No input file available.")


def fasta_clean_download(request, project_id):
    project = get_object_or_404(SequenceCleanerProject, id=project_id)

    if not project.can_download_output(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to download this file.")

    if not project.output_fasta:
        raise Http404("No output file available for this project.")

    file_path = os.path.join(settings.MEDIA_ROOT, str(project.output_fasta))
    if not os.path.exists(file_path):
        raise Http404("Output file not found on server.")

    base_name = project.name or 'cleaned'
    base_name = os.path.splitext(base_name)[0]
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in base_name).strip('_')
    download_name = f"{safe_name}_cleaned.fasta"

    response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return response
