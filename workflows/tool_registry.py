TOOL_SCHEMA_ID = {
    'fasta_validate': None,
    'sequence_clean': None,
    'clustal_omega':  'clustalo',
    'format_convert': None,
    'hmmbuild':       'hmmbuild',
    'hmmsearch':      'hmmsearch',
    'hmmemit':        'hmmemit',
}

MSA_TOOL_SCHEMAS = {
    'clustalo': 'clustalo',
    'mafft':    'mafft',
    'muscle':   'muscle',
    'kalign':   'kalign',
}


def resolve_schema_id(tool_type, config):
    if tool_type == 'clustal_omega':
        msa_tool = (config or {}).get('msa_tool') or 'clustalo'
        return MSA_TOOL_SCHEMAS.get(msa_tool, 'clustalo')
    return TOOL_SCHEMA_ID.get(tool_type)


def build_tool_parameters(tool_type, config):
    schema_id = resolve_schema_id(tool_type, config)
    if not schema_id:
        return {}

    raw = (config or {}).get('parameters') or {}
    if not isinstance(raw, dict) or not raw:
        return {}

    from biologine_aplikacija.parameter_builder import load_schema
    from biologine_aplikacija.parameter_builder.form_helpers import (
        extract_params_from_cleaned_data,
    )
    schema = load_schema(schema_id)
    cleaned = {f"param_{k}": v for k, v in raw.items()}
    return extract_params_from_cleaned_data(cleaned, schema)
