from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


class DataType(Enum):
    FASTA = 'FASTA'
    FASTA_CLEANED = 'FASTA_CLEANED'
    FASTA_DB = 'FASTA_DB'
    EMITTED_FASTA = 'EMITTED_FASTA'
    ALIGNMENT = 'ALIGNMENT'
    ALIGNMENT_STOCKHOLM = 'ALIGNMENT_STOCKHOLM'
    HMM_PROFILE = 'HMM_PROFILE'
    SEARCH_RESULTS = 'SEARCH_RESULTS'
    REPORT = 'REPORT'


_PARENT = {
    DataType.FASTA_CLEANED: DataType.FASTA,
    DataType.FASTA_DB: DataType.FASTA,
    DataType.EMITTED_FASTA: DataType.FASTA,
    DataType.ALIGNMENT_STOCKHOLM: DataType.ALIGNMENT,
}

TERMINAL_TYPES = frozenset({DataType.SEARCH_RESULTS, DataType.REPORT})


def is_subtype(child: DataType, parent: DataType) -> bool:
    cur = child
    while cur is not None:
        if cur is parent:
            return True
        cur = _PARENT.get(cur)
    return False


def is_compatible(produced: DataType | None, required: DataType | Iterable[DataType]) -> bool:
    if produced is None:
        return False
    if isinstance(required, DataType):
        return is_subtype(produced, required)
    return any(is_subtype(produced, r) for r in required)


SOURCE_PREVIOUS = 'previous_step'
SOURCE_UPLOAD = 'upload'
SOURCE_PFAM = 'pfam'
SOURCE_INTERPRO = 'interpro'
SOURCE_EXTERNAL = 'external_accession'
LEGACY_ACCESSION_SOURCES = frozenset({SOURCE_PFAM, SOURCE_INTERPRO})
ACCESSION_HMM_SOURCES = frozenset({SOURCE_EXTERNAL, SOURCE_PFAM, SOURCE_INTERPRO})
EXTERNAL_HMM_SOURCES = frozenset({SOURCE_UPLOAD, SOURCE_EXTERNAL, SOURCE_PFAM, SOURCE_INTERPRO})

DB_PREVIOUS = 'previous_step'
DB_UPLOAD = 'upload'


@dataclass(frozen=True)
class ExternalInput:
    role: str
    step_index: int
    data_type: DataType
    label: str
    accept: str = ''
    kind: str = 'file'
    placeholder: str = ''


@dataclass(frozen=True)
class ToolContract:
    tool_type: str
    label: str
    color: str
    accepts_chain: tuple[DataType, ...]
    output_for: Callable[[dict], DataType | None]
    external_inputs_for: Callable[[dict, int, list[DataType | None]], list[ExternalInput]]
    needs_chain_input: Callable[[dict], bool]
    description: str = ''


def _out_validator(_cfg):
    return DataType.FASTA

def _out_cleaner(_cfg):
    return DataType.FASTA_CLEANED

def _out_msa(cfg):
    fmt = (cfg or {}).get('output_format', 'stockholm')
    msa_tool = (cfg or {}).get('msa_tool') or 'clustalo'
    from biologine_aplikacija.parameter_builder import load_schema
    try:
        supported = load_schema(msa_tool).get('supported_output_formats', ['fasta'])
    except Exception:
        supported = ['fasta']
    if fmt not in supported:
        fmt = supported[0]
    if fmt == 'stockholm':
        return DataType.ALIGNMENT_STOCKHOLM
    return DataType.ALIGNMENT

def _out_convert(cfg):
    fmt = (cfg or {}).get('output_format', 'stockholm')
    if fmt == 'stockholm':
        return DataType.ALIGNMENT_STOCKHOLM
    if fmt == 'clustal':
        return DataType.ALIGNMENT
    if fmt == 'fasta':
        return DataType.FASTA
    return None

def _out_hmmbuild(_cfg):
    return DataType.HMM_PROFILE

def _out_hmmsearch(_cfg):
    return DataType.SEARCH_RESULTS

def _out_hmmemit(_cfg):
    return DataType.EMITTED_FASTA


def _ext_none(cfg, idx, prior_outputs):
    return []

def _ext_first_step_fasta(cfg, idx, prior_outputs):
    if idx == 0:
        return [ExternalInput(
            role='primary',
            step_index=idx,
            data_type=DataType.FASTA,
            label='Input sequences (FASTA)',
            accept='.fasta,.fa,.fna,.faa,.ffn,.frn,.txt',
        )]
    return []

def _ext_first_step_alignment(cfg, idx, prior_outputs):
    if idx == 0:
        return [ExternalInput(
            role='primary',
            step_index=idx,
            data_type=DataType.ALIGNMENT,
            label='Input alignment',
            accept='.sto,.stk,.aln,.phy,.fa,.fasta,.afa,.a2m',
        )]
    return []

def _ext_first_step_any_sequences(cfg, idx, prior_outputs):
    if idx == 0:
        return [ExternalInput(
            role='primary',
            step_index=idx,
            data_type=DataType.FASTA,
            label='Input sequences or alignment',
            accept='.fasta,.fa,.afa,.sto,.stk,.aln,.phy,.txt',
        )]
    return []

def _ext_hmmsearch(cfg, idx, prior_outputs):
    cfg = cfg or {}
    inputs: list[ExternalInput] = []

    src = cfg.get('hmm_source', SOURCE_EXTERNAL)

    if src != SOURCE_PREVIOUS:
        inputs.append(ExternalInput(
            role=f'hmmsearch_hmm_{idx}',
            step_index=idx,
            data_type=DataType.HMM_PROFILE,
            label=f'Step {idx + 1}: HMM profile',
            kind='file' if src == SOURCE_UPLOAD else 'accession',
            placeholder='e.g. PF00069 or IPR000001',
            accept='.hmm',
        ))

    db_mode = cfg.get('db_mode', DB_UPLOAD)
    if db_mode == DB_UPLOAD:
        inputs.append(ExternalInput(
            role=f'hmmsearch_db_{idx}',
            step_index=idx,
            data_type=DataType.FASTA_DB,
            label=f'Step {idx + 1}: FASTA search database',
            accept='.fasta,.fa,.fna,.faa,.ffn,.frn,.txt',
        ))
    return inputs

def _ext_hmmemit(cfg, idx, prior_outputs):
    cfg = cfg or {}
    if any(t is not None and is_subtype(t, DataType.HMM_PROFILE) for t in prior_outputs):
        return []
    src = cfg.get('hmm_source', SOURCE_EXTERNAL)
    if src == SOURCE_UPLOAD or src in ACCESSION_HMM_SOURCES:
        return [ExternalInput(
            role=f'hmmemit_hmm_{idx}',
            step_index=idx,
            data_type=DataType.HMM_PROFILE,
            label=f'Step {idx + 1}: HMM profile',
            accept='.hmm',
            kind='file' if src == SOURCE_UPLOAD else 'accession',
            placeholder='e.g. PF00069 or IPR000001',
        )]
    return []

def _needs_chain_always(cfg):
    return True

def _needs_chain_never(cfg):
    return False

def _needs_chain_hmmsearch(cfg):
    cfg = cfg or {}
    return cfg.get('hmm_source') == SOURCE_PREVIOUS or cfg.get('db_mode') == DB_PREVIOUS

def _needs_chain_hmmemit(cfg):
    cfg = cfg or {}
    return cfg.get('hmm_source') == SOURCE_PREVIOUS


CONTRACTS: dict[str, ToolContract] = {
    'fasta_validate': ToolContract(
        tool_type='fasta_validate',
        label='FASTA Validator',
        color='#10b981',
        accepts_chain=(DataType.FASTA,),
        output_for=_out_validator,
        external_inputs_for=_ext_first_step_fasta,
        needs_chain_input=_needs_chain_always,
        description='Validate FASTA structure and report stats.',
    ),
    'sequence_clean': ToolContract(
        tool_type='sequence_clean',
        label='Sequence Cleaner',
        color='#f59e0b',
        accepts_chain=(DataType.FASTA,),
        output_for=_out_cleaner,
        external_inputs_for=_ext_first_step_fasta,
        needs_chain_input=_needs_chain_always,
        description='Clean FASTA sequences (gaps, duplicates, length filtering).',
    ),
    'clustal_omega': ToolContract(
        tool_type='clustal_omega',
        label='MSA',
        color='#3b82f6',
        accepts_chain=(DataType.FASTA,),
        output_for=_out_msa,
        external_inputs_for=_ext_first_step_fasta,
        needs_chain_input=_needs_chain_always,
        description='Align sequences with the chosen MSA tool (Clustal Omega, MAFFT, MUSCLE, or Kalign).',
    ),
    'format_convert': ToolContract(
        tool_type='format_convert',
        label='Format Converter',
        color='#8b5cf6',
        accepts_chain=(DataType.FASTA, DataType.ALIGNMENT),
        output_for=_out_convert,
        external_inputs_for=_ext_first_step_any_sequences,
        needs_chain_input=_needs_chain_always,
        description='Convert between FASTA / Stockholm / Clustal formats.',
    ),
    'hmmbuild': ToolContract(
        tool_type='hmmbuild',
        label='HMMBUILD',
        color='#ef4444',
        accepts_chain=(DataType.ALIGNMENT,),
        output_for=_out_hmmbuild,
        external_inputs_for=_ext_first_step_alignment,
        needs_chain_input=_needs_chain_always,
        description='Build an HMM profile from a multiple sequence alignment.',
    ),
    'hmmsearch': ToolContract(
        tool_type='hmmsearch',
        label='HMMSEARCH',
        color='#ec4899',
        accepts_chain=(DataType.HMM_PROFILE,),
        output_for=_out_hmmsearch,
        external_inputs_for=_ext_hmmsearch,
        needs_chain_input=_needs_chain_hmmsearch,
        description='Search an HMM profile against a FASTA sequence database.',
    ),
    'hmmemit': ToolContract(
        tool_type='hmmemit',
        label='HMMEMIT',
        color='#0ea5e9',
        accepts_chain=(DataType.HMM_PROFILE,),
        output_for=_out_hmmemit,
        external_inputs_for=_ext_hmmemit,
        needs_chain_input=_needs_chain_hmmemit,
        description='Emit consensus / sample sequences from an HMM profile.',
    ),
}

TOOL_ORDER = (
    'fasta_validate',
    'sequence_clean',
    'clustal_omega',
    'format_convert',
    'hmmbuild',
    'hmmsearch',
    'hmmemit',
)


@dataclass(frozen=True)
class StepIssue:
    step_index: int
    code: str
    message: str
    expected: tuple[DataType, ...] = ()
    got: DataType | None = None
    severity: str = 'error'


@dataclass
class ValidationResult:
    is_valid: bool
    issues: list[StepIssue] = field(default_factory=list)
    step_outputs: list[DataType | None] = field(default_factory=list)
    last_output_type: DataType | None = None
    external_inputs: list[ExternalInput] = field(default_factory=list)

    def issue_for(self, idx: int) -> StepIssue | None:
        for i in self.issues:
            if i.step_index == idx:
                return i
        return None


def _resolve_output(spec: dict, prior_output: DataType | None) -> DataType | None:
    contract = CONTRACTS.get(spec.get('tool_type'))
    if contract is None:
        return None
    if contract.tool_type == 'fasta_validate':
        return prior_output if prior_output is not None else DataType.FASTA
    return contract.output_for(spec.get('config') or {})


def infer_step_output(spec: dict, prior_output: DataType | None = None) -> DataType | None:
    return _resolve_output(spec, prior_output)


def _has_upstream_fasta(step_outputs: list[DataType | None]) -> bool:
    return any(t is not None and is_subtype(t, DataType.FASTA) for t in step_outputs)


def validate_workflow(steps: list[dict]) -> ValidationResult:
    issues: list[StepIssue] = []
    step_outputs: list[DataType | None] = []
    externals: list[ExternalInput] = []
    prior: DataType | None = None

    for idx, raw in enumerate(steps):
        spec = dict(raw)
        cfg = spec.get('config') or {}
        tool_type = spec.get('tool_type')
        contract = CONTRACTS.get(tool_type)

        if contract is None:
            issues.append(StepIssue(
                step_index=idx, code='unknown_tool',
                message=f'Unknown tool: {tool_type}',
            ))
            step_outputs.append(None)
            prior = None
            continue

        if idx > 0 and contract.needs_chain_input(cfg):
            if tool_type == 'hmmsearch':
                if cfg.get('hmm_source') == SOURCE_PREVIOUS:
                    if not is_compatible(prior, DataType.HMM_PROFILE):
                        issues.append(StepIssue(
                            step_index=idx, code='hmm_source_chain_mismatch',
                            message='HMMSEARCH is set to use the previous step as its HMM source, '
                                    'but the previous step does not produce an HMM profile.',
                            expected=(DataType.HMM_PROFILE,), got=prior,
                        ))
                if cfg.get('db_mode') == DB_PREVIOUS and not _has_upstream_fasta(step_outputs):
                    issues.append(StepIssue(
                        step_index=idx, code='db_mode_no_upstream_fasta',
                        message='HMMSEARCH database is set to "previous step" but no earlier step '
                                'produces FASTA sequences.',
                        expected=(DataType.FASTA,), got=None,
                    ))
                if (
                    cfg.get('hmm_source') == SOURCE_PREVIOUS
                    and cfg.get('db_mode') == DB_PREVIOUS
                ):
                    issues.append(StepIssue(
                        step_index=idx, code='circular_chain_db',
                        message='HMMSEARCH would search the HMM against the same sequences it '
                                'was built from. Upload a separate FASTA database.',
                        expected=(DataType.FASTA,), got=None,
                    ))
            elif tool_type == 'hmmemit':
                if cfg.get('hmm_source') == SOURCE_PREVIOUS:
                    if not is_compatible(prior, DataType.HMM_PROFILE):
                        issues.append(StepIssue(
                            step_index=idx, code='hmm_source_chain_mismatch',
                            message='HMMEMIT is set to use the previous step as its HMM source, '
                                    'but the previous step does not produce an HMM profile.',
                            expected=(DataType.HMM_PROFILE,), got=prior,
                        ))
            else:
                if not is_compatible(prior, contract.accepts_chain):
                    issues.append(StepIssue(
                        step_index=idx, code='chain_type_mismatch',
                        message=_chain_mismatch_message(contract, prior),
                        expected=contract.accepts_chain, got=prior,
                    ))

        if idx == 0 and tool_type in ('hmmsearch', 'hmmemit'):
            if cfg.get('hmm_source') == SOURCE_PREVIOUS:
                issues.append(StepIssue(
                    step_index=idx, code='first_step_needs_external_source',
                    message=f'{contract.label} cannot use "previous step" as its HMM source '
                            f'when it is the first step. Choose Pfam, InterPro, or upload an HMM file.',
                    expected=(DataType.HMM_PROFILE,), got=None,
                ))
            if tool_type == 'hmmsearch' and cfg.get('db_mode') == DB_PREVIOUS:
                issues.append(StepIssue(
                    step_index=idx, code='first_step_needs_external_db',
                    message='HMMSEARCH cannot use "previous step" as its FASTA database when '
                            'it is the first step. Upload a FASTA database.',
                    expected=(DataType.FASTA,), got=None,
                ))

        if tool_type == 'hmmbuild' and DataType.EMITTED_FASTA in step_outputs:
            issues.append(StepIssue(
                step_index=idx, code='hmm_from_synthetic',
                message='This HMM will be built from synthetic sequences produced by HMMEmit upstream. '
                        'For meaningful homolog search, train HMMs from natural sequences instead.',
                severity='warning',
            ))

        if idx > 0 and prior in TERMINAL_TYPES and contract.needs_chain_input(cfg):
            issues.append(StepIssue(
                step_index=idx, code='after_terminal',
                message=f'The previous step ({_label_for_type(prior)}) is terminal and cannot feed {contract.label}.',
                expected=contract.accepts_chain, got=prior,
            ))

        externals.extend(contract.external_inputs_for(cfg, idx, list(step_outputs)))

        out = _resolve_output(spec, prior)
        step_outputs.append(out)
        prior = out

    last = step_outputs[-1] if step_outputs else None
    blocking = [i for i in issues if i.severity == 'error']
    return ValidationResult(
        is_valid=not blocking,
        issues=issues,
        step_outputs=step_outputs,
        last_output_type=last,
        external_inputs=externals,
    )


def _label_for_type(t: DataType | None) -> str:
    if t is None:
        return 'nothing'
    return {
        DataType.FASTA: 'FASTA sequences',
        DataType.FASTA_CLEANED: 'cleaned FASTA',
        DataType.FASTA_DB: 'FASTA database',
        DataType.EMITTED_FASTA: 'emitted FASTA',
        DataType.ALIGNMENT: 'alignment',
        DataType.ALIGNMENT_STOCKHOLM: 'Stockholm alignment',
        DataType.HMM_PROFILE: 'HMM profile',
        DataType.SEARCH_RESULTS: 'search results',
        DataType.REPORT: 'report-only output',
    }.get(t, t.value)


def _chain_mismatch_message(contract: ToolContract, got: DataType | None) -> str:
    accepted = ' or '.join(_label_for_type(t) for t in contract.accepts_chain)
    return f'{contract.label} needs {accepted}, but the previous step produced {_label_for_type(got)}.'


@dataclass(frozen=True)
class ToolEntry:
    tool_type: str
    label: str
    color: str
    description: str = ''
    reason: str = ''


def get_starting_tools() -> list[ToolEntry]:
    return [
        ToolEntry(
            tool_type=t,
            label=CONTRACTS[t].label,
            color=CONTRACTS[t].color,
            description=CONTRACTS[t].description,
        )
        for t in TOOL_ORDER
    ]


def get_allowed_next_steps(steps: list[dict]) -> dict:
    result = validate_workflow(steps)
    last = result.last_output_type
    last_tool_type = steps[-1].get('tool_type') if steps else None
    recommended: list[ToolEntry] = []
    other: list[ToolEntry] = []
    unavailable: list[ToolEntry] = []

    if last == DataType.EMITTED_FASTA:
        for tool_type in TOOL_ORDER:
            contract = CONTRACTS[tool_type]
            has_external_mode = tool_type in ('hmmsearch', 'hmmemit')
            if is_compatible(last, contract.accepts_chain) or (
                has_external_mode and is_subtype(last, DataType.FASTA)
            ):
                other.append(_entry(contract))
            else:
                accepted = ' or '.join(_label_for_type(t) for t in contract.accepts_chain)
                unavailable.append(_entry(
                    contract,
                    reason=f'requires {accepted}; previous step produced {_label_for_type(last)}',
                ))
        return {'recommended': recommended, 'other': other, 'unavailable': unavailable}

    for tool_type in TOOL_ORDER:
        contract = CONTRACTS[tool_type]
        has_external_mode = tool_type in ('hmmsearch', 'hmmemit')

        if last in TERMINAL_TYPES:
            if has_external_mode:
                other.append(_entry(contract))
            else:
                unavailable.append(_entry(contract, reason=_terminal_reason(last)))
            continue

        if is_compatible(last, contract.accepts_chain):
            if tool_type == last_tool_type:
                other.append(_entry(contract))
            else:
                recommended.append(_entry(contract))
        elif has_external_mode:
            if tool_type == 'hmmsearch' and last is not None and is_subtype(last, DataType.FASTA):
                recommended.append(_entry(contract))
            else:
                other.append(_entry(contract))
        else:
            accepted = ' or '.join(_label_for_type(t) for t in contract.accepts_chain)
            unavailable.append(_entry(
                contract,
                reason=f'requires {accepted}; previous step produced {_label_for_type(last)}',
            ))

    recommended.sort(key=lambda e: _recommend_rank(last, e.tool_type))
    return {
        'recommended': recommended,
        'other': other,
        'unavailable': unavailable,
    }


def _entry(contract: ToolContract, reason: str = '') -> ToolEntry:
    return ToolEntry(
        tool_type=contract.tool_type,
        label=contract.label,
        color=contract.color,
        description=contract.description,
        reason=reason,
    )


def _terminal_reason(last: DataType | None) -> str:
    if last == DataType.SEARCH_RESULTS:
        return 'previous step produced search results'
    return f'previous step produced {_label_for_type(last)}'


_RECOMMEND_AFFINITY: dict[tuple[DataType, str], int] = {
    (DataType.FASTA, 'sequence_clean'): 0,
    (DataType.FASTA, 'clustal_omega'): 1,
    (DataType.FASTA, 'hmmsearch'): 2,
    (DataType.FASTA, 'format_convert'): 3,
    (DataType.FASTA, 'fasta_validate'): 4,
    (DataType.FASTA_CLEANED, 'clustal_omega'): 0,
    (DataType.FASTA_CLEANED, 'hmmsearch'): 1,
    (DataType.FASTA_CLEANED, 'format_convert'): 2,
    (DataType.FASTA_CLEANED, 'fasta_validate'): 3,
    (DataType.ALIGNMENT_STOCKHOLM, 'hmmbuild'): 0,
    (DataType.ALIGNMENT_STOCKHOLM, 'format_convert'): 1,
    (DataType.ALIGNMENT, 'format_convert'): 0,
    (DataType.ALIGNMENT, 'hmmbuild'): 1,
    (DataType.HMM_PROFILE, 'hmmsearch'): 0,
    (DataType.HMM_PROFILE, 'hmmemit'): 1,
    (DataType.EMITTED_FASTA, 'fasta_validate'): 0,
    (DataType.EMITTED_FASTA, 'sequence_clean'): 1,
    (DataType.EMITTED_FASTA, 'clustal_omega'): 2,
    (DataType.EMITTED_FASTA, 'hmmsearch'): 3,
}


def _recommend_rank(last: DataType | None, tool_type: str) -> int:
    if last is None:
        return 99
    return _RECOMMEND_AFFINITY.get((last, tool_type), 50)


def serialize_tool_entry(entry: ToolEntry) -> dict:
    return {
        'tool_type': entry.tool_type,
        'label': entry.label,
        'color': entry.color,
        'description': entry.description,
        'reason': entry.reason,
    }


def serialize_issue(issue: StepIssue) -> dict:
    return {
        'step_index': issue.step_index,
        'code': issue.code,
        'message': issue.message,
        'expected': [t.value for t in issue.expected],
        'got': issue.got.value if issue.got else None,
        'severity': issue.severity,
    }


def serialize_external_input(ext: ExternalInput) -> dict:
    return {
        'role': ext.role,
        'step_index': ext.step_index,
        'data_type': ext.data_type.value,
        'label': ext.label,
        'accept': ext.accept,
        'kind': ext.kind,
        'placeholder': ext.placeholder,
    }


def serialize_validation(result: ValidationResult) -> dict:
    return {
        'is_valid': result.is_valid,
        'last_output_type': result.last_output_type.value if result.last_output_type else None,
        'step_outputs': [t.value if t else None for t in result.step_outputs],
        'issues': [serialize_issue(i) for i in result.issues],
        'external_inputs': [serialize_external_input(e) for e in result.external_inputs],
    }


def builder_state_payload(steps: list[dict]) -> dict:
    if not steps:
        return {
            'state': 'empty',
            'starting_tools': [serialize_tool_entry(t) for t in get_starting_tools()],
        }

    result = validate_workflow(steps)
    base = {
        'validation': serialize_validation(result),
        'step_outputs_human': [_label_for_type(t) for t in result.step_outputs],
    }
    if result.is_valid:
        nxt = get_allowed_next_steps(steps)
        base.update({
            'state': 'valid',
            'recommended': [serialize_tool_entry(t) for t in nxt['recommended']],
            'other': [serialize_tool_entry(t) for t in nxt['other']],
            'unavailable': [serialize_tool_entry(t) for t in nxt['unavailable']],
        })
        return base

    base['state'] = 'invalid'
    return base


def external_inputs_for_workflow(steps: list[dict]) -> list[ExternalInput]:
    return validate_workflow(steps).external_inputs
