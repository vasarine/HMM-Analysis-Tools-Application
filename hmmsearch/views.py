import os
import uuid
import logging
from django.shortcuts import render, redirect
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from .models import HMMSearchProject
from .forms import HMMSearchForm
from .tasks import run_hmmsearch
from celery.result import AsyncResult
from hmmsearch.services import detect_hmm_source, resolve_external_hmm
from users.history_utils import log_user_action

logger = logging.getLogger(__name__)

def hmmsearch_form(request):
    context = {}
    preloaded_hmm_name = None

    from_hmmbuild = request.GET.get('from_hmmbuild')
    if from_hmmbuild and request.method == 'GET':
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
                    request.session['preloaded_hmm_path'] = src_path
                    request.session['preloaded_hmm_name'] = os.path.basename(src_path)
                    request.session['preloaded_hmm_label'] = src.name or 'HMM Profile'
        except HMMBuildProject.DoesNotExist:
            pass

    preloaded_hmm_label = request.session.get('preloaded_hmm_label')
    preloaded_hmm_path = request.session.get('preloaded_hmm_path')
    has_preloaded_hmm = bool(preloaded_hmm_path and os.path.exists(preloaded_hmm_path))

    if preloaded_hmm_path and not has_preloaded_hmm:
        for key in ('preloaded_hmm_path', 'preloaded_hmm_name', 'preloaded_hmm_label'):
            request.session.pop(key, None)
        preloaded_hmm_label = None

    if request.method == "POST":
        form = HMMSearchForm(request.POST, request.FILES, preloaded_hmm=has_preloaded_hmm)

        if not form.is_valid():
            return render(request, "hmmsearch_form.html", {
                "form": form,
                "preloaded_hmm_label": preloaded_hmm_label,
            })

        fasta_file = form.cleaned_data.get("fasta_file")
        pasted_fasta = (form.cleaned_data.get("pasted_fasta") or "").strip()
        hmm_source = form.cleaned_data["hmm_source"]
        user_input_name = form.cleaned_data.get("name") or "Untitled project"

        from biologine_aplikacija.parameter_builder.form_helpers import (
            extract_params_from_cleaned_data,
        )
        custom_params = extract_params_from_cleaned_data(form.cleaned_data, form._schema)

        unique_id = uuid.uuid4().hex[:8]
        prefix = f"hmmsearch_{unique_id}"

        search_dir = os.path.join(settings.MEDIA_ROOT, "hmmsearch")
        os.makedirs(search_dir, exist_ok=True)

        save_hits_msa = bool(form.cleaned_data.get('save_hits_msa'))
        hits_msa_filename = f"{prefix}_hits.sto" if save_hits_msa else None
        hits_msa_path = os.path.join(search_dir, hits_msa_filename) if save_hits_msa else None

        save_pfamtbl = bool(form.cleaned_data.get('save_pfamtbl'))
        pfamtbl_filename = f"{prefix}.pfamtbl" if save_pfamtbl else None
        pfamtbl_path = os.path.join(search_dir, pfamtbl_filename) if save_pfamtbl else None

        if fasta_file:
            fasta_ext      = os.path.splitext(fasta_file.name)[1].lower() or ".fa"
            fasta_filename = f"{prefix}{fasta_ext}"
            display_fasta_name = fasta_file.name
        else:
            fasta_filename = f"{prefix}.fa"
            display_fasta_name = "pasted_sequences.fasta"
        out_filename    = f"{prefix}.out"
        tblout_filename = f"{prefix}.tblout"
        domtbl_filename = f"{prefix}.domtbl"

        fasta_path  = os.path.join(search_dir, fasta_filename)
        out_path    = os.path.join(search_dir, out_filename)
        tblout_path = os.path.join(search_dir, tblout_filename)
        domtbl_path = os.path.join(search_dir, domtbl_filename)

        try:
            if fasta_file:
                with open(fasta_path, "wb+") as d:
                    for ch in fasta_file.chunks():
                        d.write(ch)
            else:
                content = pasted_fasta if pasted_fasta.endswith("\n") else pasted_fasta + "\n"
                with open(fasta_path, "w", encoding="utf-8") as d:
                    d.write(content)
        except OSError:
            form.add_error(None, "Could not save the FASTA sequences. Please try again.")
            return render(request, "hmmsearch_form.html", {
                "form": form,
                "preloaded_hmm_label": preloaded_hmm_label,
            })

        external_hmm_id = None
        external_hmm_name = None

        if hmm_source == 'upload':
            hmm_file = form.cleaned_data.get("hmm_file")
            hmm_filename = f"{prefix}.hmm"
            hmm_path = os.path.join(search_dir, hmm_filename)

            if not hmm_file and preloaded_hmm_path and os.path.exists(preloaded_hmm_path):
                try:
                    import shutil
                    shutil.copy2(preloaded_hmm_path, hmm_path)
                except OSError:
                    form.add_error(None, "Could not prepare the pre-loaded HMM file. Please try again.")
                    return render(request, "hmmsearch_form.html", {
                        "form": form,
                        "preloaded_hmm_label": preloaded_hmm_label,
                    })
            else:
                try:
                    with open(hmm_path, "wb+") as d:
                        for ch in hmm_file.chunks():
                            d.write(ch)
                except OSError:
                    form.add_error(None, "Could not save the HMM file. Please try again.")
                    return render(request, "hmmsearch_form.html", {
                        "form": form,
                        "preloaded_hmm_label": preloaded_hmm_label,
                    })

        elif hmm_source == 'library':
            external_hmm_id = form.cleaned_data["external_hmm_id"].upper().strip()

            detected_source = detect_hmm_source(external_hmm_id)
            if not detected_source:
                form.add_error('external_hmm_id', 'Unrecognized ID format.')
                return render(request, "hmmsearch_form.html", {
                    "form": form,
                    "preloaded_hmm_label": preloaded_hmm_label,
                })

            try:
                hmm_path, external_hmm_name = resolve_external_hmm(detected_source, external_hmm_id)
                hmm_source = detected_source
            except ValueError as e:
                form.add_error('external_hmm_id', str(e))
                return render(request, "hmmsearch_form.html", {
                    "form": form,
                    "preloaded_hmm_label": preloaded_hmm_label,
                })
            except Exception as e:
                import traceback
                logger.error("Unexpected error resolving HMM for %s: %s", external_hmm_id, e)
                logger.error(traceback.format_exc())
                form.add_error('external_hmm_id',
                               'Could not download the HMM profile. Please try again.')
                return render(request, "hmmsearch_form.html", {
                    "form": form,
                    "preloaded_hmm_label": preloaded_hmm_label,
                })

            hmm_filename = f"{external_hmm_id}.hmm"

        project = HMMSearchProject.objects.create(
            user=request.user if request.user.is_authenticated else None,
            name=user_input_name,
            fasta_file=f"hmmsearch/{fasta_filename}",
            hmm_file=f"hmmsearch/{hmm_filename}" if hmm_source == 'upload' else None,
            hmm_source=hmm_source,
            external_hmm_id=external_hmm_id,
            external_hmm_name=external_hmm_name,
            out_file=f"hmmsearch/{out_filename}",
            tblout_file=f"hmmsearch/{tblout_filename}",
            domtbl_file=f"hmmsearch/{domtbl_filename}",
            hits_msa_file=f"hmmsearch/{hits_msa_filename}" if save_hits_msa else "",
            pfamtbl_file=f"hmmsearch/{pfamtbl_filename}" if save_pfamtbl else "",
            parameters=custom_params,
            task_status='PENDING'
        )

        task = run_hmmsearch.delay(
            project.id, hmm_path, fasta_path, out_path, tblout_path, domtbl_path,
            parameters=custom_params,
            hits_msa_path=hits_msa_path,
            pfamtbl_path=pfamtbl_path,
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])

        for key in ('preloaded_hmm_path', 'preloaded_hmm_name', 'preloaded_hmm_label'):
            request.session.pop(key, None)

        description = f'Created HMMSEARCH project with FASTA file: {display_fasta_name}'
        if hmm_source == 'upload':
            description += f' and uploaded HMM file'
        else:
            description += f' using {hmm_source.upper()} library: {external_hmm_id}'

        log_user_action(
            user=request.user if request.user.is_authenticated else None,
            action_type='project_created',
            tool_type='hmmsearch',
            project=project,
            project_name=user_input_name,
            description=description,
            metadata={
                'hmm_source': hmm_source,
                'external_hmm_id': external_hmm_id,
                'fasta_filename': display_fasta_name
            },
            request=request,
        )

        return redirect('hmmsearch_status', project_id=project.id)

    form = HMMSearchForm(preloaded_hmm=has_preloaded_hmm)
    return render(request, "hmmsearch_form.html", {
        "form": form,
        "preloaded_hmm_label": preloaded_hmm_label,
    })


def dismiss_preloaded_hmm(request):
    for key in ('preloaded_hmm_path', 'preloaded_hmm_name', 'preloaded_hmm_label'):
        request.session.pop(key, None)
    return redirect('hmmsearch_form')


def hmmsearch_status(request, project_id):
    try:
        project = HMMSearchProject.objects.get(id=project_id)
        if request.user.is_authenticated and project.user and project.user != request.user:
            raise Http404("Project not found")
    except HMMSearchProject.DoesNotExist:
        raise Http404("Project not found")

    from biologine_aplikacija.parameter_builder import build_parameter_overrides
    context = {
        'project': project,
        'form': HMMSearchForm(),
        'can_download_output': project.can_download_output(request.user),
        'can_download_input': project.can_download_input(request.user),
        'db_source': (project.parameters or {}).get('_db_source'),
        'parameter_overrides': build_parameter_overrides(project, 'hmmsearch'),
    }

    if project.task_status == 'SUCCESS' and project.result_text:
        out_filename = os.path.basename(project.out_file.name) if project.out_file else None
        tblout_filename = os.path.basename(project.tblout_file.name) if project.tblout_file else None
        domtbl_filename = os.path.basename(project.domtbl_file.name) if project.domtbl_file else None

        from biologine_aplikacija.preview_utils import read_text_preview, build_preview_note

        def _preview(text, n=2000):
            preview = read_text_preview(text, max_lines=n, max_chars=500000)
            note = f"\n\n{preview.note}" if preview.note else ''
            text_out = f"{preview.text}{note}" if preview.text else ''
            return text_out, preview

        target_hits = 0
        if project.tblout_text:
            for ln in project.tblout_text.splitlines():
                if ln.strip() and not ln.startswith('#'):
                    target_hits += 1

        result_text_out, result_pv = _preview(project.result_text, 50)
        tblout_text_out, tblout_pv = _preview(project.tblout_text, 30)
        domtbl_text_out, domtbl_pv = _preview(project.domtbl_text, 30)

        hits_msa_preview = None
        hits_msa_preview_note = None
        if project.hits_msa_file:
            try:
                with open(os.path.join(settings.MEDIA_ROOT, str(project.hits_msa_file)), 'r') as f:
                    hits_msa_text = f.read()
                hits_msa_preview, hits_msa_pv = _preview(hits_msa_text, 30)
                hits_msa_preview_note = build_preview_note(hits_msa_pv, 'the hits alignment')
            except OSError:
                hits_msa_preview = None

        pfamtbl_preview = None
        pfamtbl_preview_note = None
        if project.pfamtbl_file:
            try:
                with open(os.path.join(settings.MEDIA_ROOT, str(project.pfamtbl_file)), 'r') as f:
                    pfamtbl_text = f.read()
                pfamtbl_preview, pfamtbl_pv = _preview(pfamtbl_text, 30)
                pfamtbl_preview_note = build_preview_note(pfamtbl_pv, 'the Pfam-format table')
            except OSError:
                pfamtbl_preview = None

        context.update({
            'result_text': project.result_text,
            'tblout_text': project.tblout_text,
            'domtbl_text': project.domtbl_text,
            'result_preview': result_text_out,
            'tblout_preview': tblout_text_out,
            'domtbl_preview': domtbl_text_out,
            'result_preview_note': build_preview_note(result_pv, 'the .out file'),
            'tblout_preview_note': build_preview_note(tblout_pv, 'the .tblout table'),
            'domtbl_preview_note': build_preview_note(domtbl_pv, 'the .domtblout table'),
            'out_filename': out_filename,
            'tblout_filename': tblout_filename,
            'domtbl_filename': domtbl_filename,
            'target_hits': target_hits,
            'hits_msa_preview': hits_msa_preview,
            'hits_msa_preview_note': hits_msa_preview_note,
            'pfamtbl_preview': pfamtbl_preview,
            'pfamtbl_preview_note': pfamtbl_preview_note,
        })

    return render(request, 'hmmsearch_status.html', context)


def hmmsearch_task_status(request, task_id):
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
        response_data['message'] = task.info.get('message', 'Running HMM search...')
        response_data['progress'] = task.info.get('progress', 50)
    elif task.state == 'SUCCESS':
        response_data['message'] = 'Search completed successfully'
        response_data['progress'] = 100
        response_data['result'] = task.result
    elif task.state == 'FAILURE':
        response_data['message'] = 'Task failed'
        response_data['progress'] = 100
        friendly = None
        try:
            project = HMMSearchProject.objects.filter(task_id=task_id).first()
            if project and project.result_text and project.result_text.startswith('ERROR:'):
                friendly = project.result_text[len('ERROR:'):].strip()
        except Exception:
            pass
        response_data['error'] = friendly or 'The search could not be completed. Please check your inputs and try again.'
    else:
        response_data['message'] = str(task.state)
        response_data['progress'] = 50

    return JsonResponse(response_data)


def download_search_file(request, file_name):
    file_path = os.path.join(settings.MEDIA_ROOT, "hmmsearch", file_name)
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


def hmmsearch_fasta_download(request, project_id):
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponseForbidden
    project = get_object_or_404(HMMSearchProject, pk=project_id)
    if not project.can_download_input(request.user):
        return HttpResponseForbidden("You do not have permission to download this file.")
    return _serve_file(project.fasta_file, 'sequences.fasta')


def hmmsearch_hmm_input_download(request, project_id):
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponseForbidden
    project = get_object_or_404(HMMSearchProject, pk=project_id)
    if not project.can_download_input(request.user):
        return HttpResponseForbidden("You do not have permission to download this file.")
    return _serve_file(project.hmm_file, 'profile.hmm')


def hmmsearch_output_download(request, project_id, file_type='out'):
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponseForbidden
    project = get_object_or_404(HMMSearchProject, pk=project_id)
    if not project.can_download_output(request.user):
        return HttpResponseForbidden("You do not have permission to download this file.")
    if file_type == 'tblout':
        return _serve_file(project.tblout_file, 'results.tblout')
    if file_type == 'domtbl':
        return _serve_file(project.domtbl_file, 'results.domtbl')
    if file_type == 'hits_msa':
        return _serve_file(project.hits_msa_file, 'hits_alignment.sto')
    if file_type == 'pfamtbl':
        return _serve_file(project.pfamtbl_file, 'results.pfamtbl')
    return _serve_file(project.out_file, 'results.out')
