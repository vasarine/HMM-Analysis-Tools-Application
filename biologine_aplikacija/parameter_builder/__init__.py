import json
from pathlib import Path

from .builder import CommandBuilder

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_SCHEMA_TOOLS = {'hmmbuild', 'hmmsearch', 'hmmemit',
                 'clustalo', 'mafft', 'muscle', 'kalign'}


def load_schema(tool_id):
    with open(SCHEMAS_DIR / f"{tool_id}.json") as f:
        return json.load(f)


def build_parameter_overrides(project, tool):
    schema_name = tool
    if tool == 'clustalo':
        schema_name = getattr(project, 'tool', 'clustalo') or 'clustalo'
    if schema_name not in _SCHEMA_TOOLS:
        return {'has_schema': False, 'overrides': []}

    try:
        schema = load_schema(schema_name)
    except Exception:
        return {'has_schema': False, 'overrides': []}

    params = getattr(project, 'parameters', None) or {}
    specs = {p['name']: p for p in schema.get('parameters', [])}
    overrides = []
    for name, value in params.items():
        spec = specs.get(name)
        if not spec:
            continue
        if value in (None, '', []):
            continue
        if value == spec.get('default'):
            continue

        if spec.get('type') == 'radio_group':
            display = str(value)
            for opt in spec.get('options', []) or []:
                if isinstance(opt, dict) and opt.get('value') == value:
                    flag = opt.get('flag')
                    if flag:
                        display = flag
                    else:
                        label = opt.get('label') or str(value)
                        display = label.split(' · ', 1)[-1].replace(' (default)', '').strip()
                    break
        elif isinstance(value, bool):
            display = 'On' if value else 'Off'
        else:
            display = str(value)

        overrides.append({
            'label': spec.get('label') or name,
            'flag': spec.get('flag') or '',
            'value': display,
        })
    return {'has_schema': True, 'overrides': overrides}


_WORKFLOW_TOOL_OVERRIDE_MAP = {
    'hmmbuild':      'hmmbuild',
    'hmmsearch':     'hmmsearch',
    'hmmemit':       'hmmemit',
    'clustal_omega': 'clustalo',
}

_SEQUENCE_CLEAN_FIELDS = [
    ('sequence_type',         'Sequence type',         'auto'),
    ('invalid_char_strategy', 'Invalid characters',    'replace'),
    ('remove_gaps',           'Remove gap characters', False),
    ('remove_stop_chars',     'Remove stop characters', False),
    ('uppercase',             'Convert to uppercase',  True),
    ('remove_duplicate_ids',  'Remove duplicate IDs',  True),
    ('remove_duplicate_seqs', 'Remove identical sequences', False),
    ('min_length',            'Min length',            None),
    ('max_length',            'Max length',            None),
    ('max_ambiguity_percent', 'Max ambiguity (%)',     None),
]
_SEQUENCE_CLEAN_CHOICE_LABELS = {
    'sequence_type': {
        'auto': 'Auto-detect', 'protein': 'Protein', 'dna': 'DNA', 'rna': 'RNA',
    },
    'invalid_char_strategy': {
        'replace': 'Replace with X / N',
        'remove':  'Remove silently',
        'reject':  'Reject sequence',
    },
}


def _sequence_clean_overrides(project):
    options = getattr(project, 'options', None) or {}
    overrides = []
    for key, label, default in _SEQUENCE_CLEAN_FIELDS:
        if key not in options:
            continue
        value = options[key]
        if value in (None, ''):
            continue
        if value == default:
            continue
        if isinstance(value, bool):
            display = 'On' if value else 'Off'
        else:
            choices = _SEQUENCE_CLEAN_CHOICE_LABELS.get(key)
            display = choices.get(value, str(value)) if choices else str(value)
        overrides.append({'label': label, 'flag': '', 'value': display})
    return {'has_schema': True, 'overrides': overrides}


def build_step_parameter_overrides(step_run):
    if step_run is None:
        return {'has_schema': False, 'overrides': []}

    step = getattr(step_run, 'step', None)
    tool_type = step.tool_type if step else getattr(step_run, 'tool_type_snapshot', '')
    project = getattr(step_run, 'project', None)

    if not tool_type or project is None:
        return {'has_schema': False, 'overrides': []}

    if tool_type in _WORKFLOW_TOOL_OVERRIDE_MAP:
        return build_parameter_overrides(project, _WORKFLOW_TOOL_OVERRIDE_MAP[tool_type])

    if tool_type == 'sequence_clean':
        return _sequence_clean_overrides(project)

    return {'has_schema': False, 'overrides': []}


__all__ = ("CommandBuilder", "SCHEMAS_DIR", "load_schema",
           "build_parameter_overrides", "build_step_parameter_overrides")
