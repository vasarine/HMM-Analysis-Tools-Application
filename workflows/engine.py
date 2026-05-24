import os
import shutil
import time
import uuid
from dataclasses import dataclass, field

from django.conf import settings

from .validation import (
    CONTRACTS, DataType, is_subtype,
    SOURCE_PREVIOUS, SOURCE_UPLOAD, SOURCE_PFAM, SOURCE_INTERPRO,
    DB_PREVIOUS, DB_UPLOAD,
)
from .tool_registry import build_tool_parameters


@dataclass
class RunContext:
    step_index: int = 0
    inputs_by_role: dict = field(default_factory=dict)
    prior_outputs: list = field(default_factory=list)

    def role(self, role: str) -> dict:
        return self.inputs_by_role.get(role, {})

    def upstream_fasta_path(self) -> str | None:
        match = self.upstream_fasta_source()
        return match[0] if match else None

    def upstream_fasta_source(self) -> tuple[str, int] | None:
        for entry in reversed(self.prior_outputs):
            if len(entry) >= 3:
                dt_value, path, step_idx = entry[0], entry[1], entry[2]
            else:
                dt_value, path = entry[0], entry[1]
                step_idx = -1
            try:
                dt = DataType(dt_value)
            except ValueError:
                continue
            if is_subtype(dt, DataType.FASTA):
                return path, step_idx
        return None


def _empty_ctx(step_index=0):
    return RunContext(step_index=step_index)


def _tmp_copy(src, subdir, prefix='input', suffix=''):
    dest_dir = os.path.join(settings.MEDIA_ROOT, subdir)
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(src)[1].lower() or suffix or '.fasta'
    uid = uuid.uuid4().hex[:8]
    filename = f"{prefix}_{uid}{ext}"
    dest = os.path.join(dest_dir, filename)
    shutil.copy2(src, dest)
    return dest, filename


def _upstream_hmm_path(ctx: RunContext) -> str | None:
    for entry in reversed(ctx.prior_outputs):
        dt_val, path = entry[0], entry[1]
        try:
            dt = DataType(dt_val)
            if is_subtype(dt, DataType.HMM_PROFILE):
                return path
        except ValueError:
            continue
    return None


def _resolve_external_hmm(ctx_input: dict, fallback_dir: str) -> tuple[str, str, str | None, str | None]:
    accession = (ctx_input.get('accession') or '').strip().upper()
    file_path = ctx_input.get('file')

    if file_path and os.path.exists(file_path):
        os.makedirs(fallback_dir, exist_ok=True)
        dest = os.path.join(fallback_dir, f'profile_{uuid.uuid4().hex[:8]}.hmm')
        shutil.copy2(file_path, dest)
        return dest, 'upload', None, None

    if accession:
        import re
        from hmm_library.services import HMMCacheManager
        from hmm_library.services.exceptions import RemoteUnavailable
        if re.match(r'^PF\d{5}$', accession):
            source = 'pfam'
        elif re.match(r'^IPR\d{6}$', accession):
            source = 'interpro'
        else:
            raise ValueError(f'Unrecognized HMM accession format: {accession}')
        try:
            hmm_path = HMMCacheManager.get_or_download(source, accession)
        except RemoteUnavailable as e:
            api_label = 'InterPro' if source == 'interpro' else 'Pfam'
            raise ValueError(f'{api_label} API is currently unreachable. Please try again later.') from e
        if not hmm_path:
            raise ValueError(f'Could not retrieve HMM for {accession}.')
        try:
            from hmm_library.models import ExternalHMMModel
            ext_model = ExternalHMMModel.objects.filter(source=source, external_id=accession).first()
            ext_name = ext_model.name if ext_model else None
        except Exception:
            ext_name = None
        return hmm_path, source, accession, ext_name

    raise ValueError('No HMM input provided.')


def execute_validate(current_file, user, config=None, wf_name='', ctx=None, **_):
    from sequence_tools.validator import analyze_fasta
    from sequence_tools.models import FASTAValidationProject

    stats = analyze_fasta(current_file)

    rel = os.path.relpath(current_file, settings.MEDIA_ROOT)
    project = FASTAValidationProject.objects.create(
        user=user,
        name=f'{wf_name} – FASTA Validation' if wf_name else 'FASTA Validation',
        visibility='private',
        share_token=uuid.uuid4(),
        input_fasta=rel,
        stats=stats,
    )

    if not stats.get('valid') and stats.get('errors'):
        raise ValueError(stats['errors'][0])

    return current_file, project


def execute_clean(current_file, user, config, wf_name='', ctx=None, **_):
    from sequence_tools.cleaner import clean_fasta
    from sequence_tools.models import SequenceCleanerProject

    options = {
        'sequence_type':         config.get('sequence_type', 'auto'),
        'invalid_char_strategy': config.get('invalid_char_strategy', 'replace'),
        'remove_gaps':          config.get('remove_gaps', True),
        'remove_stop_chars':    config.get('remove_stop_chars', False),
        'uppercase':            config.get('uppercase', True),
        'remove_duplicate_ids': config.get('remove_duplicate_ids', False),
        'remove_duplicate_seqs':config.get('remove_duplicate_seqs', False),
        'min_length':           config.get('min_length'),
        'max_length':           config.get('max_length'),
    }

    in_path, in_fn = _tmp_copy(current_file, 'workflows/clean/input', prefix='sequences')
    out_dir = os.path.join(settings.MEDIA_ROOT, 'workflows', 'clean', 'output')
    os.makedirs(out_dir, exist_ok=True)

    stats, cleaned_path = clean_fasta(in_path, options, out_dir)
    if not cleaned_path or not os.path.exists(cleaned_path):
        raise ValueError(
            'Cleaning produced no output. Make sure the file has at least one valid sequence.'
        )

    project = SequenceCleanerProject.objects.create(
        user=user,
        name=f'{wf_name} – Sequence Cleaning' if wf_name else 'Sequence Cleaning',
        visibility='private',
        share_token=uuid.uuid4(),
        input_fasta=os.path.relpath(in_path, settings.MEDIA_ROOT),
        output_fasta=os.path.relpath(cleaned_path, settings.MEDIA_ROOT),
        options=options,
        stats=stats,
    )
    return cleaned_path, project


_MSA_TOOL_BINARY = {
    'clustalo': 'clustalo',
    'mafft':    'mafft',
    'muscle':   'muscle',
    'kalign':   'kalign',
}
_MSA_TOOL_LABEL = {
    'clustalo': 'Clustal Omega',
    'mafft':    'MAFFT',
    'muscle':   'MUSCLE',
    'kalign':   'Kalign',
}


def _resolve_msa_tool(config):
    return (config or {}).get('msa_tool') or 'clustalo'


def execute_clustalo(current_file, user, config, wf_name='', ctx=None,
                     workflow_run_id=None, step_index=None, **_):
    from msa_tools.models import ClustalOmegaProject
    from msa_tools.tasks import run_clustalo, run_mafft, run_muscle, run_kalign

    msa_tool = _resolve_msa_tool(config)
    binary = _MSA_TOOL_BINARY[msa_tool]
    if not shutil.which(binary):
        raise ValueError(f'{_MSA_TOOL_LABEL[msa_tool]} is not installed on this server.')

    from biologine_aplikacija.parameter_builder import load_schema
    try:
        supported = load_schema(msa_tool).get('supported_output_formats', ['fasta'])
    except Exception:
        supported = ['fasta']
    output_format = config.get('output_format', 'stockholm')
    if output_format not in supported:
        output_format = supported[0]
    in_path, in_fn = _tmp_copy(current_file, 'msa_tools/clustalo/input', prefix='sequences')

    out_dir = os.path.join(settings.MEDIA_ROOT, 'msa_tools', 'clustalo', 'output')
    os.makedirs(out_dir, exist_ok=True)
    _ext_map = {'stockholm': '.sto', 'clustal': '.aln', 'fasta': '.fasta'}
    out_ext = _ext_map.get(output_format, '.sto')
    out_fn = f"alignment_{uuid.uuid4().hex[:8]}{out_ext}"
    out_path = os.path.join(out_dir, out_fn)

    project = ClustalOmegaProject.objects.create(
        user=user,
        name=f'{wf_name} – MSA Alignment ({_MSA_TOOL_LABEL[msa_tool]})' if wf_name else f'MSA Alignment ({_MSA_TOOL_LABEL[msa_tool]})',
        visibility='private',
        share_token=uuid.uuid4(),
        tool=msa_tool,
        input_fasta=f'msa_tools/clustalo/input/{in_fn}',
        output_format=output_format,
        task_status='PENDING',
    )

    parameters = build_tool_parameters('clustal_omega', config)
    project.parameters = parameters
    project.save(update_fields=['parameters'])

    task_for_tool = {
        'clustalo': run_clustalo,
        'mafft':    run_mafft,
        'muscle':   run_muscle,
        'kalign':   run_kalign,
    }
    task_fn = task_for_tool[msa_tool]
    if msa_tool == 'clustalo':
        run_args = [project.id, in_path, out_path, output_format, 1]
    else:
        run_args = [project.id, in_path, out_path, output_format]

    if workflow_run_id is not None:
        from workflows.tasks import run_next_workflow_step
        task = task_fn.apply_async(
            args=run_args,
            kwargs={
                'skip_history': True,
                'workflow_run_id': workflow_run_id,
                'step_index': step_index,
                'parameters': parameters,
            },
            link_error=run_next_workflow_step.s(workflow_run_id, step_index, project.id),
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])
        return None, project

    task = task_fn.delay(*run_args, skip_history=True, parameters=parameters)
    project.task_id = task.id
    project.save(update_fields=['task_id'])

    for _ in range(60):
        time.sleep(5)
        project.refresh_from_db()
        if project.task_status == 'SUCCESS':
            break
        if project.task_status == 'FAILURE':
            raise ValueError(project.error_message or 'MSA alignment failed.')
    else:
        raise ValueError('MSA alignment timed out after 5 minutes.')

    if not project.output_alignment:
        raise ValueError('MSA produced no output file.')

    return os.path.join(settings.MEDIA_ROOT, str(project.output_alignment)), project


def execute_format_convert(current_file, user, config, wf_name='', ctx=None, **_):
    from msa_tools.models import FormatConversionProject
    from msa_tools.views import _detect_alignment_format, _convert_alignment, OUTPUT_EXT

    output_format = config.get('output_format', 'stockholm')
    in_path, in_fn = _tmp_copy(current_file, 'msa_tools/converter/input', prefix='alignment')

    input_format = _detect_alignment_format(in_path)
    if input_format is None:
        raise ValueError(
            'Could not detect the file format. '
            'Make sure the file is a valid FASTA, Clustal, or Stockholm alignment.'
        )

    proj_name = f'{wf_name} – Format Conversion' if wf_name else 'Format Conversion'

    if input_format == output_format:
        project = FormatConversionProject.objects.create(
            user=user,
            name=proj_name,
            visibility='private',
            share_token=uuid.uuid4(),
            input_file=f'msa_tools/converter/input/{in_fn}',
            input_format=input_format,
            output_format=output_format,
            error_message='',
        )
        return current_file, project

    out_dir = os.path.join(settings.MEDIA_ROOT, 'msa_tools', 'converter', 'output')
    os.makedirs(out_dir, exist_ok=True)
    out_fn = f"converted_alignment_{uuid.uuid4().hex[:8]}{OUTPUT_EXT[output_format]}"
    out_path = os.path.join(out_dir, out_fn)

    seq_count, aln_length, error = _convert_alignment(in_path, input_format, out_path, output_format)
    project = FormatConversionProject.objects.create(
        user=user,
        name=proj_name,
        visibility='private',
        share_token=uuid.uuid4(),
        input_file=f'msa_tools/converter/input/{in_fn}',
        input_format=input_format,
        output_format=output_format,
        sequence_count=seq_count,
        alignment_length=aln_length,
        error_message=error or '',
    )
    if error:
        raise ValueError(error)
    project.output_file = f'msa_tools/converter/output/{out_fn}'
    project.save(update_fields=['output_file'])
    return out_path, project


def _looks_unaligned_fasta(path: str) -> bool:
    if not path:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.fa', '.fasta', '.faa', '.fna', '.ffn'):
        return False
    try:
        from Bio import SeqIO
        records = list(SeqIO.parse(path, 'fasta'))
    except Exception:
        return False
    if len(records) < 2:
        return False
    lengths = {len(r.seq) for r in records}
    has_gaps = any('-' in str(r.seq) for r in records)
    return not (len(lengths) == 1 and has_gaps)


def execute_hmmbuild(current_file, user, config, wf_name='', ctx=None,
                     workflow_run_id=None, step_index=None):
    from hmmbuild.models import HMMBuildProject
    from hmmbuild.tasks import run_hmmbuild

    if not shutil.which('hmmbuild'):
        raise ValueError('hmmbuild is not installed on this server.')

    if not current_file or not os.path.exists(current_file):
        raise ValueError(
            'HMMBUILD needs an input alignment from the previous step, '
            'but none was provided.'
        )

    uid = uuid.uuid4().hex[:8]
    hmmbuild_dir = os.path.join(settings.MEDIA_ROOT, 'hmmbuild')
    os.makedirs(hmmbuild_dir, exist_ok=True)

    ext = os.path.splitext(current_file)[1].lower() or '.sto'
    msa_fn = f'alignment_{uid}{ext}'
    hmm_fn = f'hmm_profile_{uid}.hmm'
    msa_path = os.path.join(hmmbuild_dir, msa_fn)
    hmm_path = os.path.join(hmmbuild_dir, hmm_fn)
    shutil.copy2(current_file, msa_path)

    parameters = build_tool_parameters('hmmbuild', config)
    if _looks_unaligned_fasta(msa_path):
        parameters['_input_warning'] = (
            'Input looks like unaligned FASTA, not a multiple sequence alignment. '
            'hmmbuild may fail or produce a low-quality profile - run the MSA tool first.'
        )
    save_annotated = bool((config or {}).get('save_annotated_msa'))
    annotated_fn = f'annotated_{uid}.sto' if save_annotated else None
    annotated_path = os.path.join(hmmbuild_dir, annotated_fn) if save_annotated else None

    project = HMMBuildProject.objects.create(
        user=user,
        name=f'{wf_name} – HMM Profile' if wf_name else 'HMM Profile',
        msa_file=f'hmmbuild/{msa_fn}',
        hmm_file=f'hmmbuild/{hmm_fn}',
        annotated_msa_file=f'hmmbuild/{annotated_fn}' if save_annotated else '',
        parameters=parameters,
        task_status='PENDING',
    )

    if workflow_run_id is not None:
        from workflows.tasks import run_next_workflow_step
        task = run_hmmbuild.apply_async(
            args=[project.id, msa_path, hmm_path],
            kwargs={
                'skip_history': True,
                'workflow_run_id': workflow_run_id,
                'step_index': step_index,
                'parameters': parameters,
                'annotated_msa_path': annotated_path,
            },
            link_error=run_next_workflow_step.s(workflow_run_id, step_index, project.id),
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])
        return None, project

    task = run_hmmbuild.delay(project.id, msa_path, hmm_path,
                              skip_history=True, parameters=parameters,
                              annotated_msa_path=annotated_path)
    project.task_id = task.id
    project.save(update_fields=['task_id'])

    for _ in range(60):
        time.sleep(5)
        project.refresh_from_db()
        if project.task_status == 'SUCCESS':
            break
        if project.task_status == 'FAILURE':
            raise ValueError(project.error_message or 'HMM build failed.')
    else:
        raise ValueError('HMM build timed out after 5 minutes.')

    return hmm_path, project


def execute_hmmsearch(current_file, user, config, wf_name='', ctx=None,
                      workflow_run_id=None, step_index=None, **_):
    from hmmsearch.models import HMMSearchProject
    from hmmsearch.tasks import run_hmmsearch

    if not shutil.which('hmmsearch'):
        raise ValueError('hmmsearch is not installed on this server.')

    ctx = ctx or _empty_ctx()
    cfg = config or {}
    src = cfg.get('hmm_source', SOURCE_PFAM)
    db_mode = cfg.get('db_mode', DB_UPLOAD)

    search_dir = os.path.join(settings.MEDIA_ROOT, 'hmmsearch')
    os.makedirs(search_dir, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    prefix = f'hmmsearch_{uid}'

    if src == SOURCE_PREVIOUS:
        if not current_file or not os.path.exists(current_file):
            raise ValueError('HMMSEARCH expected an HMM profile from the previous step but none was available.')
        hmm_dest = os.path.join(search_dir, f'{prefix}.hmm')
        shutil.copy2(current_file, hmm_dest)
        hmm_path = hmm_dest
        hmm_source_label = 'upload'
        external_id = None
        external_name = None
    else:
        role = f'hmmsearch_hmm_{ctx.step_index}'
        try:
            hmm_path, hmm_source_label, external_id, external_name = _resolve_external_hmm(
                ctx.role(role), search_dir,
            )
        except ValueError:
            raise

    db_source_info = None
    if db_mode == DB_PREVIOUS:
        source = ctx.upstream_fasta_source()
        if not source:
            raise ValueError('HMMSEARCH database is set to "previous step" but no upstream FASTA was found.')
        fasta_src, source_step_idx = source
        fasta_ext = os.path.splitext(fasta_src)[1].lower() or '.fasta'
        fasta_path = os.path.join(search_dir, f'{prefix}{fasta_ext}')
        shutil.copy2(fasta_src, fasta_path)
        db_source_info = {
            'mode': 'previous_step',
            'step_number': source_step_idx + 1 if source_step_idx >= 0 else None,
            'filename': os.path.basename(fasta_src),
        }
    else:
        role = f'hmmsearch_db_{ctx.step_index}'
        rec = ctx.role(role)
        ext_path = rec.get('file')
        if not ext_path or not os.path.exists(ext_path):
            raise ValueError('HMMSEARCH requires an uploaded FASTA database; none was provided.')
        fasta_ext = os.path.splitext(ext_path)[1].lower() or '.fasta'
        fasta_path = os.path.join(search_dir, f'{prefix}{fasta_ext}')
        shutil.copy2(ext_path, fasta_path)
        db_source_info = {'mode': 'upload', 'filename': os.path.basename(ext_path)}

    out_path    = os.path.join(search_dir, f'{prefix}.out')
    tblout_path = os.path.join(search_dir, f'{prefix}.tblout')
    domtbl_path = os.path.join(search_dir, f'{prefix}.domtbl')

    parameters = build_tool_parameters('hmmsearch', cfg)
    if db_source_info is not None:
        parameters['_db_source'] = db_source_info
    save_hits_msa = bool(cfg.get('save_hits_msa'))
    save_pfamtbl = bool(cfg.get('save_pfamtbl'))
    hits_msa_fn = f'{prefix}_hits.sto' if save_hits_msa else None
    pfamtbl_fn = f'{prefix}.pfamtbl' if save_pfamtbl else None
    hits_msa_path = os.path.join(search_dir, hits_msa_fn) if save_hits_msa else None
    pfamtbl_path = os.path.join(search_dir, pfamtbl_fn) if save_pfamtbl else None

    project = HMMSearchProject.objects.create(
        user=user,
        name=f'{wf_name} – HMMSEARCH' if wf_name else 'HMMSEARCH',
        fasta_file=os.path.relpath(fasta_path, settings.MEDIA_ROOT),
        hmm_file=os.path.relpath(hmm_path, settings.MEDIA_ROOT) if hmm_source_label == 'upload' else None,
        hmm_source=hmm_source_label,
        external_hmm_id=external_id,
        external_hmm_name=external_name,
        out_file=os.path.relpath(out_path, settings.MEDIA_ROOT),
        tblout_file=os.path.relpath(tblout_path, settings.MEDIA_ROOT),
        domtbl_file=os.path.relpath(domtbl_path, settings.MEDIA_ROOT),
        hits_msa_file=f'hmmsearch/{hits_msa_fn}' if save_hits_msa else '',
        pfamtbl_file=f'hmmsearch/{pfamtbl_fn}' if save_pfamtbl else '',
        parameters=parameters,
        task_status='PENDING',
    )
    if workflow_run_id is not None:
        from workflows.tasks import run_next_workflow_step
        task = run_hmmsearch.apply_async(
            args=[project.id, hmm_path, fasta_path, out_path, tblout_path, domtbl_path],
            kwargs={
                'workflow_run_id': workflow_run_id,
                'step_index': step_index,
                'parameters': parameters,
                'hits_msa_path': hits_msa_path,
                'pfamtbl_path': pfamtbl_path,
            },
            link_error=run_next_workflow_step.s(workflow_run_id, step_index, project.id),
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])
        return None, project

    task = run_hmmsearch.delay(project.id, hmm_path, fasta_path, out_path, tblout_path, domtbl_path,
                               parameters=parameters,
                               hits_msa_path=hits_msa_path, pfamtbl_path=pfamtbl_path)
    project.task_id = task.id
    project.save(update_fields=['task_id'])

    for _ in range(120):
        time.sleep(5)
        project.refresh_from_db()
        if project.task_status == 'SUCCESS':
            break
        if project.task_status == 'FAILURE':
            err = project.result_text or 'HMMSEARCH failed.'
            if err.startswith('ERROR:'):
                err = err[len('ERROR:'):].strip()
            raise ValueError(err)
    else:
        raise ValueError('HMMSEARCH timed out.')

    if not os.path.exists(out_path):
        raise ValueError('HMMSEARCH produced no main output file.')
    if not os.path.exists(tblout_path):
        raise ValueError('HMMSEARCH produced no output file.')

    return out_path, project


def execute_hmmemit(current_file, user, config, wf_name='', ctx=None,
                    workflow_run_id=None, step_index=None, **_):
    from hmmemit.models import HMMEmitProject
    from hmmemit.tasks import run_hmmemit

    if not shutil.which('hmmemit'):
        raise ValueError('hmmemit is not installed on this server.')

    ctx = ctx or _empty_ctx()
    cfg = config or {}
    src = cfg.get('hmm_source', SOURCE_PFAM)
    num_seqs = int(cfg.get('num_seqs') or 10)
    seed = cfg.get('seed')
    if seed in (None, ''):
        seed = None

    emit_dir = os.path.join(settings.MEDIA_ROOT, 'hmmemit')
    os.makedirs(emit_dir, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    prefix = f'hmmemit_{uid}'

    upstream_hmm = _upstream_hmm_path(ctx)

    if upstream_hmm and os.path.exists(upstream_hmm):
        hmm_dest = os.path.join(emit_dir, f'{prefix}.hmm')
        shutil.copy2(upstream_hmm, hmm_dest)
        hmm_path = hmm_dest
        hmm_source_label = 'upload'
        external_id = None
        external_name = None
    elif src == SOURCE_PREVIOUS:
        if not current_file or not os.path.exists(current_file):
            raise ValueError('HMMEMIT expected an HMM profile from the previous step but none was available.')
        hmm_dest = os.path.join(emit_dir, f'{prefix}.hmm')
        shutil.copy2(current_file, hmm_dest)
        hmm_path = hmm_dest
        hmm_source_label = 'upload'
        external_id = None
        external_name = None
    else:
        role = f'hmmemit_hmm_{ctx.step_index}'
        hmm_path, hmm_source_label, external_id, external_name = _resolve_external_hmm(
            ctx.role(role), emit_dir,
        )

    parameters = build_tool_parameters('hmmemit', cfg)
    out_ext = 'sto' if parameters.get('alignment') else 'fa'
    out_path = os.path.join(emit_dir, f'{prefix}.{out_ext}')

    project = HMMEmitProject.objects.create(
        user=user,
        name=f'{wf_name} – HMMEMIT' if wf_name else 'HMMEMIT',
        hmm_file=os.path.relpath(hmm_path, settings.MEDIA_ROOT) if hmm_source_label == 'upload' else None,
        hmm_source=hmm_source_label,
        external_hmm_id=external_id,
        external_hmm_name=external_name,
        output_file=os.path.relpath(out_path, settings.MEDIA_ROOT),
        parameters=parameters,
        task_status='PENDING',
    )
    if workflow_run_id is not None:
        from workflows.tasks import run_next_workflow_step
        task = run_hmmemit.apply_async(
            args=[project.id, hmm_path, out_path, num_seqs, seed],
            kwargs={
                'workflow_run_id': workflow_run_id,
                'step_index': step_index,
                'parameters': parameters,
            },
            link_error=run_next_workflow_step.s(workflow_run_id, step_index, project.id),
        )
        project.task_id = task.id
        project.save(update_fields=['task_id'])
        return None, project

    task = run_hmmemit.delay(project.id, hmm_path, out_path, num_seqs, seed,
                             parameters=parameters)
    project.task_id = task.id
    project.save(update_fields=['task_id'])

    for _ in range(60):
        time.sleep(5)
        project.refresh_from_db()
        if project.task_status == 'SUCCESS':
            break
        if project.task_status == 'FAILURE':
            err = project.result_text or 'HMMEMIT failed.'
            if err.startswith('ERROR:'):
                err = err[len('ERROR:'):].strip()
            raise ValueError(err)
    else:
        raise ValueError('HMMEMIT timed out.')

    if not os.path.exists(out_path):
        raise ValueError('HMMEMIT produced no output file.')

    return out_path, project


DISPATCHERS = {
    'fasta_validate': execute_validate,
    'sequence_clean': execute_clean,
    'clustal_omega':  execute_clustalo,
    'format_convert': execute_format_convert,
    'hmmbuild':       execute_hmmbuild,
    'hmmsearch':      execute_hmmsearch,
    'hmmemit':        execute_hmmemit,
}


def dispatch_step(tool_type, current_file, user, config, wf_name='', ctx=None,
                  workflow_run_id=None, step_index=None):
    handler = DISPATCHERS.get(tool_type)
    if not handler:
        raise ValueError(f'Unknown tool type: {tool_type}')
    return handler(
        current_file, user, config,
        wf_name=wf_name, ctx=ctx,
        workflow_run_id=workflow_run_id, step_index=step_index,
    )
