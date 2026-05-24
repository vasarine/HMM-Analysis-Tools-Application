"""
Validating FASTA input: checking the file is well-formed and what
type of sequences it contains.
"""

from Bio import SeqIO


STOP_CODONS = {'TAA', 'TAG', 'TGA', 'UAA', 'UAG', 'UGA'}

IUPAC_DNA = 'ACGTRYSWKMBDHVNacgtryswkmbdhvn-'
IUPAC_RNA = 'ACGURYSWKMBDHVNacguryswkmbdhvn-'
IUPAC_PROTEIN = 'ACDEFGHIKLMNPQRSTVWYBJZXUOacdefghiklmnpqrstvwybjzxuo*-'


def detect_sequence_type(sequences):
    dna_chars = set(IUPAC_DNA)
    rna_chars = set(IUPAC_RNA)
    protein_chars = set(IUPAC_PROTEIN)

    all_chars = set()
    for seq in sequences[:10]:
        all_chars.update(str(seq.seq))

    clean_chars = all_chars - {'-', 'N', 'n', '*'}

    if not clean_chars:
        return 'Unknown'

    if clean_chars <= dna_chars:
        return 'DNA'
    if clean_chars <= rna_chars:
        return 'RNA'
    if clean_chars <= protein_chars:
        return 'Protein'
    return 'Unknown'


def has_stop_codons_in_seq(seq_str, seq_type):
    if seq_type not in ('DNA', 'RNA'):
        return False
    seq_clean = seq_str.upper().replace('-', '').replace('U', 'T')
    return any(
        seq_clean[i:i+3] in {'TAA', 'TAG', 'TGA'}
        for i in range(0, len(seq_clean) - 2, 3)
    )


_EMPTY_RESULT_DEFAULTS = {
    'valid': False, 'sequence_count': 0,
    'min_length': None, 'max_length': None, 'avg_length': None,
    'detected_type': None, 'has_gaps': False, 'has_stop_codons': False,
    'duplicate_count': 0, 'warnings': [],
}


def _empty_result(error_msg):
    return {**_EMPTY_RESULT_DEFAULTS, 'errors': [error_msg]}


def _parse_fasta(file_path):
    try:
        with open(file_path, 'rb') as _src:
            raw = _src.read()
        cleaned = raw.decode('ascii', errors='ignore').encode('ascii')
        if cleaned != raw:
            with open(file_path, 'wb') as _dst:
                _dst.write(cleaned)
    except OSError:
        pass

    last_err = ''
    for fmt in ('fasta', 'fasta-pearson'):
        try:
            return list(SeqIO.parse(file_path, fmt)), None
        except Exception as e:
            last_err = str(e).lower()

    if any(tok in last_err for tok in ('comment', 'pearson', 'blast')):
        return None, "File contains comment lines before sequences. Remove lines starting with ';' at the top of the file."
    if any(tok in last_err for tok in ('unicode', 'decode', 'codec')):
        return None, "File contains non-text content. Make sure it is a plain-text FASTA file."
    return None, "File does not appear to be a valid FASTA file. Make sure sequences start with '>' headers."


def _seq_list(names, limit=5):
    listed = ', '.join(f'"{n}"' for n in names[:limit])
    return listed + (f' and {len(names) - limit} more' if len(names) > limit else '')


def _n(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


_VALID_CHARS = {
    'DNA':     set(IUPAC_DNA),
    'RNA':     set(IUPAC_RNA),
    'Protein': set(IUPAC_PROTEIN),
}


def analyze_fasta(file_path):
    sequences, parse_err = _parse_fasta(file_path)
    if parse_err is not None:
        return _empty_result(parse_err)
    if not sequences:
        return _empty_result(
            'No sequences found. Make sure the file is a valid FASTA file with sequences starting with ">".'
        )

    errors = []
    warnings = []
    sequence_count = len(sequences)

    empty_ids = [r.id for r in sequences if len(r.seq) == 0]
    if empty_ids:
        errors.append(
            f"{_n(len(empty_ids), 'sequence has', 'sequences have')} no content: {_seq_list(empty_ids)}."
        )

    lengths = [len(r.seq) for r in sequences if len(r.seq) > 0]
    if lengths:
        min_length = min(lengths)
        max_length = max(lengths)
        avg_length = round(sum(lengths) / len(lengths), 1)
    else:
        min_length = max_length = avg_length = 0

    detected_type = detect_sequence_type(sequences)
    unit = 'aa' if detected_type == 'Protein' else 'nt'

    if sequence_count == 1 and not empty_ids:
        warnings.append('Only 1 sequence found. At least 2 sequences are needed for alignment.')

    has_gaps = any('-' in str(r.seq) for r in sequences)
    if has_gaps:
        warnings.append('Gap characters (-) detected - these sequences appear to be already aligned.')

    stop_char_seqs = [r.id for r in sequences if '*' in str(r.seq)]
    if stop_char_seqs:
        warnings.append(
            f"{_n(len(stop_char_seqs), 'sequence contains', 'sequences contain')} a stop character (*): "
            f"{_seq_list(stop_char_seqs)}. Use Sequence Cleaner to remove them."
        )

    has_stop_codons = bool(stop_char_seqs)
    if detected_type in ('DNA', 'RNA') and not stop_char_seqs:
        stop_codon_seqs = [r.id for r in sequences if has_stop_codons_in_seq(str(r.seq), detected_type)]
        if stop_codon_seqs:
            has_stop_codons = True
            warnings.append(
                f"{_n(len(stop_codon_seqs), 'sequence contains', 'sequences contain')} internal stop codons: "
                f"{_seq_list(stop_codon_seqs)}. Consider cleaning before use."
            )

    seen = set()
    duplicates = set()
    for sid in (r.id for r in sequences):
        if sid in seen:
            duplicates.add(sid)
        seen.add(sid)
    duplicate_count = len(duplicates)
    if duplicate_count:
        errors.append(
            f"{_n(duplicate_count, 'sequence name is', 'sequence names are')} used more than once: "
            f"{_seq_list(list(duplicates))}. Each sequence must have a unique name."
        )

    short_seqs = [(r.id, len(r.seq)) for r in sequences if 0 < len(r.seq) < 10]
    if short_seqs:
        names = [f'"{sid}" ({length} {unit})' for sid, length in short_seqs[:5]]
        extra = f' and {len(short_seqs) - 5} more' if len(short_seqs) > 5 else ''
        warnings.append(
            f"{_n(len(short_seqs), 'sequence is', 'sequences are')} very short: "
            f"{', '.join(names)}{extra}. Very short sequences may reduce analysis quality."
        )

    valid_chars = _VALID_CHARS.get(detected_type)
    if valid_chars:
        invalid_seqs = [r.id for r in sequences if not set(str(r.seq)) <= valid_chars]
        if invalid_seqs:
            warnings.append(
                f"{_n(len(invalid_seqs), 'sequence contains', 'sequences contain')} unexpected characters: "
                f"{_seq_list(invalid_seqs)}. This may cause issues in downstream tools."
            )

    return {
        'valid': not errors,
        'sequence_count': sequence_count,
        'min_length': min_length,
        'max_length': max_length,
        'avg_length': avg_length,
        'detected_type': detected_type,
        'has_gaps': has_gaps,
        'has_stop_codons': has_stop_codons,
        'duplicate_count': duplicate_count,
        'errors': errors,
        'warnings': warnings,
    }
