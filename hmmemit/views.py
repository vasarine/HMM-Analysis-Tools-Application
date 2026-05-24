"""
Pages for emitting sequences from an HMM profile: the form, task status
and downloading the generated FASTA.
"""

from django.shortcuts import render, redirect
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from .models import HMMEmitProject
from .forms import HMMEmitForm
from .tasks import run_hmmemit
from celery.result import AsyncResult
from hmm_library.services import HMMCacheManager
from hmm_library.services.exceptions import RemoteUnavailable
from users.history_utils import log_user_action
import os
import uuid
import logging

logger = logging.getLogger(__name__)

def hmmemit_dismiss_preload(request):
    for k in ('emit_preloaded_hmm_path', 'emit_preloaded_hmm_name', 'emit_preloaded_hmm_label'):
        request.session.pop(k, None)
    return redirect('hmmemit_form')


def hmmemit_form(request):
    preload_error = None
    from_hmmbuild = request.GET.get('from_hmmbuild')
    if from_hmmbuild and request.method == 'GET':
        for k in ('emit_preloaded_hmm_path', 'emit_preloaded_hmm_name', 'emit_preloaded_hmm_label'):
            request.session.pop(k, None)
        from hmmbuild.models import HMMBuildProject
        try:
            src = HMMBuildProject.objects.get(id=from_hmmbuild)
            allowed = (
                src.user is None
                or (request.user.is_authenticated and src.user_id == request.user.id)
                or getattr(src, 'visibility', 'private') in ('public', 'link')
            )
            if allowed and src.hmm_file:
                src_path = os.path.join(settings.MEDIA_ROOT, str(src.hmm_file))
                if os.path.exists(src_path):
                    request.session['emit_preloaded_hmm_path'] = src_path
                    request.session['emit_preloaded_hmm_name'] = os.path.basename(src_path)
                    request.session['emit_preloaded_hmm_label'] = src.name or 'HMM Profile'
                else:
                    preload_error = "The generated HMM file for that HMMBUILD project could not be found."
            elif allowed:
                preload_error = "That HMMBUILD project does not have a generated HMM file yet."
            else:
                preload_error = "You do not have access to that HMMBUILD project."
        except HMMBuildProject.DoesNotExist:
            preload_error = "The selected HMMBUILD project could not be found."

    preloaded_hmm_label = request.session.get('emit_preloaded_hmm_label')
    preloaded_hmm_path = request.session.get('emit_preloaded_hmm_path')
    has_preloaded_hmm = bool(preloaded_hmm_path and os.path.exists(preloaded_hmm_path))

    if preloaded_hmm_path and not has_preloaded_hmm:
        for k in ('emit_preloaded_hmm_path', 'emit_preloaded_hmm_name', 'emit_preloaded_hmm_label'):
            request.session.pop(k, None)
        preloaded_hmm_label = None

    context = {
        'preloaded_hmm_label': preloaded_hmm_label,
        'has_preloaded_hmm': has_preloaded_hmm,
        'preload_error': preload_error,
    }

    if request.method == "POST":
        form = HMMEmitForm(request.POST, request.FILES, preloaded_hmm=has_preloaded_hmm)

        if not form.is_valid():
            context["form"] = form
            return render(request, "hmmemit_form.html", context)

        hmm_source = form.cleaned_data["hmm_source"]
        user_input_name = form.cleaned_data.get("name") or "Untitled project"
        num_seqs = form.cleaned_data["num_seqs"]
        seed = form.cleaned_data.get("seed")

        from biologine_aplikacija.parameter_builder.form_helpers import (
            extract_params_from_cleaned_data,
        )
        custom_params = extract_params_from_cleaned_data(form.cleaned_data, form._schema)

        unique_id = uuid.uuid4().hex[:8]
        file_prefix = f"hmmemit_{unique_id}"

        emit_dir = os.path.join(settings.MEDIA_ROOT, "hmmemit")
        os.makedirs(emit_dir, exist_ok=True)

        emit_alignment = bool(custom_params.get('alignment'))
        out_ext = 'sto' if emit_alignment else 'fa'
        out_filename = f"{file_prefix}.{out_ext}"
        out_path = os.path.join(emit_dir, out_filename)

        external_hmm_id = None
        external_hmm_name = None

        if hmm_source == 'upload':
            hmm_filename = f"{file_prefix}.hmm"
            hmm_path = os.path.join(emit_dir, hmm_filename)
            hmm_file = form.cleaned_data.get("hmm_file")

            if hmm_file:
                try:
                    with open(hmm_path, "wb+") as dest:
                        for chunk in hmm_file.chunks():
                            dest.write(chunk)
                except OSError:
                    form.add_error(None, "Could not save the uploaded file. Please try again.")
                    context["form"] = form
                    return render(request, "hmmemit_form.html", context)
            elif has_preloaded_hmm:
                import shutil
                try:
                    shutil.copy2(preloaded_hmm_path, hmm_path)
                except OSError:
                    form.add_error(None, "Could not load the pre-selected HMM file.")
                    context["form"] = form
                    return render(request, "hmmemit_form.html", context)
                for k in ('emit_preloaded_hmm_path', 'emit_preloaded_hmm_name', 'emit_preloaded_hmm_label'):
                    request.session.pop(k, None)

        elif hmm_source == 'library':
            external_hmm_id = form.cleaned_data["external_hmm_id"].upper().strip()

            import re
            if re.match(r'^PF\d{5}$', external_hmm_id):
                detected_source = 'pfam'
            elif re.match(r'^IPR\d{6}$', external_hmm_id):
                detected_source = 'interpro'
            else:
                form.add_error('external_hmm_id', 'Unrecognized ID format.')
                context["form"] = form
                return render(request, "hmmemit_form.html", context)

            try:
                logger.info(f"Attempting to get HMM from {detected_source}: {external_hmm_id}")

                hmm_path = HMMCacheManager.get_or_download(detected_source, external_hmm_id)

                logger.info(f"HMM path result: {hmm_path}")

                if not hmm_path:
                    if detected_source == 'interpro':
                        error_msg = (
                            f'Could not find a Pfam HMM model for {external_hmm_id}. '
                            'This InterPro entry has no associated Pfam model - '
                            'try a different InterPro entry, or use a Pfam ID (PF00001) directly.'
                        )
                    else:
                        error_msg = (
                            f'Could not download HMM for {external_hmm_id}. '
                            'Please check the ID and try again.'
                        )
                    form.add_error('external_hmm_id', error_msg)
                    context["form"] = form
                    return render(request, "hmmemit_form.html", context)

                from hmm_library.models import ExternalHMMModel
                ext_model = ExternalHMMModel.objects.filter(
                    source=detected_source,
                    external_id=external_hmm_id
                ).first()
                if ext_model:
                    external_hmm_name = ext_model.name
                    logger.info(f"Found HMM name: {external_hmm_name}")

                hmm_source = detected_source

            except RemoteUnavailable as e:
                logger.warning(f"Remote API unreachable for {external_hmm_id}: {e}")
                api_label = 'InterPro' if detected_source == 'interpro' else 'Pfam'
                form.add_error(
                    'external_hmm_id',
                    f'{api_label} API is currently unreachable. The HMM was not downloaded - '
                    'no analysis was started. Please try again in a few moments.'
                )
                context["form"] = form
                return render(request, "hmmemit_form.html", context)
            except Exception as e:
                import traceback
                logger.error(f"Error getting HMM: {str(e)}")
                logger.error(traceback.format_exc())
                form.add_error('external_hmm_id',
                               'Could not download the HMM profile. Please try again.')
                context["form"] = form
                return render(request, "hmmemit_form.html", context)

            hmm_filename = f"{external_hmm_id}.hmm"

        project = HMMEmitProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            hmm_file=f"hmmemit/{hmm_filename}" if hmm_source == 'upload' else None,
            hmm_source=hmm_source,
            external_hmm_id=external_hmm_id,
            external_hmm_name=external_hmm_name,
            output_file=f"hmmemit/{out_filename}",
            parameters=custom_params,
            task_status='PENDING'
        )

        task = run_hmmemit.delay(
            project.id, hmm_path, out_path, num_seqs, seed,
            parameters=custom_params,
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])

        description = f'Created HMMEMIT project'
        if hmm_source == 'upload':
            description += f' with uploaded HMM file'
        else:
            description += f' using {hmm_source.upper()} library: {external_hmm_id}'

        log_user_action(
            user=request.user if request.user.is_authenticated else None,
            action_type='project_created',
            tool_type='hmmemit',
            project=project,
            project_name=user_input_name,
            description=description,
            metadata={
                'hmm_source': hmm_source,
                'external_hmm_id': external_hmm_id,
                'num_seqs': num_seqs
            },
            request=request,
        )

        return redirect('hmmemit_status', project_id=project.id)

    form = HMMEmitForm(preloaded_hmm=has_preloaded_hmm)
    context["form"] = form
    return render(request, "hmmemit_form.html", context)


def hmmemit_status(request, project_id):
    try:
        project = HMMEmitProject.objects.get(id=project_id)
        if request.user.is_authenticated and project.user and project.user != request.user:
            raise Http404("Project not found")
    except HMMEmitProject.DoesNotExist:
        raise Http404("Project not found")

    from biologine_aplikacija.parameter_builder import build_parameter_overrides
    context = {
        'project': project,
        'form': HMMEmitForm(),
        'can_download_output': project.can_download_output(request.user),
        'can_download_input': project.can_download_input(request.user),
        'parameter_overrides': build_parameter_overrides(project, 'hmmemit'),
    }

    if project.task_status == 'SUCCESS' and project.result_text:
        output_filename = os.path.basename(project.output_file.name) if project.output_file else None
        full_text = project.result_text
        from biologine_aplikacija.preview_utils import read_text_preview, build_preview_note
        preview = read_text_preview(full_text, max_lines=2000, max_chars=500000)
        note = f"\n\n{preview.note}" if preview.note else ''
        preview_text = f"{preview.text}{note}" if preview.text else ''
        lines = full_text.splitlines()
        seq_count = sum(1 for ln in lines if ln.startswith('>'))
        context.update({
            'output_text': full_text,
            'output_preview': preview_text,
            'output_preview_note': build_preview_note(preview, 'the emitted sequences'),
            'output_filename': output_filename,
            'emit_seq_count': seq_count,
        })

    return render(request, 'hmmemit_status.html', context)


def hmmemit_task_status(request, task_id):
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
        response_data['message'] = task.info.get('message', 'Generating sequences...')
        response_data['progress'] = task.info.get('progress', 50)
    elif task.state == 'SUCCESS':
        response_data['message'] = 'Sequences generated successfully'
        response_data['progress'] = 100
        response_data['result'] = task.result
    elif task.state == 'FAILURE':
        response_data['message'] = 'Task failed'
        response_data['progress'] = 100
        friendly = None
        try:
            project = HMMEmitProject.objects.filter(task_id=task_id).first()
            if project and project.result_text and project.result_text.startswith('ERROR:'):
                friendly = project.result_text[len('ERROR:'):].strip()
        except Exception:
            pass
        response_data['error'] = friendly or 'Sequence generation failed. Please try again.'
    else:
        response_data['message'] = str(task.state)
        response_data['progress'] = 50

    return JsonResponse(response_data)


def download_emit(request, file_name):
    file_path = os.path.join(settings.MEDIA_ROOT, "hmmemit", file_name)
    if os.path.exists(file_path):
        return FileResponse(open(file_path, "rb"), as_attachment=True)
    raise Http404("File not found")


def _serve_file(file_field, download_name):
    if not file_field:
        raise Http404("File not available.")
    file_path = os.path.join(settings.MEDIA_ROOT, str(file_field))
    if not os.path.exists(file_path):
        raise Http404("File not found on server.")
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=download_name)


def hmmemit_input_download(request, project_id):
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponseForbidden
    project = get_object_or_404(HMMEmitProject, pk=project_id)
    if not project.can_download_input(request.user):
        return HttpResponseForbidden("You do not have permission to download this file.")
    return _serve_file(project.hmm_file, 'profile.hmm')


def hmmemit_output_download(request, project_id):
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponseForbidden
    project = get_object_or_404(HMMEmitProject, pk=project_id)
    if not project.can_download_output(request.user):
        return HttpResponseForbidden("You do not have permission to download this file.")
    stored = project.output_file.name if project.output_file else ''
    download_name = 'alignment.sto' if stored.endswith('.sto') else 'sequences.fa'
    return _serve_file(project.output_file, download_name)
