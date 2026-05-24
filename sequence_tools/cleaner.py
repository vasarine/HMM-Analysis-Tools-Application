import os
import uuid
from Bio import SeqIO
from Bio.SeqRecord import SeqRecord
from Bio.Seq import Seq

PROTEIN_ALPHABET = set("ACDEFGHIKLMNPQRSTVWYBJZXUO")
DNA_ALPHABET = set("ACGTNRYSWKMBDHV")
RNA_ALPHABET = set("ACGUNRYSWKMBDHV")
NUCLEOTIDE_LETTERS = set("ACGTUNRYSWKMBDHV")
PROTEIN_ONLY = set("EFILPQJZO")

GAP_CHARS = set("-.~")
STOP_CHAR = "*"

DNA_DETECTION_RATIO = 0.90
HIGH_INVALID_RATIO = 0.05


def looks_like_msa(records):
    if len(records) < 2:
        return False
    lengths = {len(r.seq) for r in records}
    if len(lengths) != 1:
        return False
    gapped = sum(1 for r in records if any(c in GAP_CHARS for c in str(r.seq)))
    return gapped >= 2


def detect_alphabet(records, sample_size=10):
    sample = records[:sample_size]
    bio_chars = []
    for rec in sample:
        s = str(rec.seq).upper()
        for ch in s:
            if not ch.isalpha():
                continue
            if ch in GAP_CHARS or ch == STOP_CHAR:
                continue
            bio_chars.append(ch)

    if not bio_chars:
        return 'protein', 0.0

    total = len(bio_chars)

    protein_only_hits = sum(1 for c in bio_chars if c in PROTEIN_ONLY)
    if protein_only_hits > 0:
        confidence = min(1.0, 0.95 + (protein_only_hits / total))
        return 'protein', confidence

    nucl_hits = sum(1 for c in bio_chars if c in NUCLEOTIDE_LETTERS)
    ratio = nucl_hits / total
    if ratio >= DNA_DETECTION_RATIO:
        has_u = any(c == 'U' for c in bio_chars)
        has_t = any(c == 'T' for c in bio_chars)
        if has_u and not has_t:
            return 'rna', ratio
        return 'dna', ratio
    return 'protein', 1 - ratio


def clean_fasta(file_path, options, output_dir):
    stats = {
        'original_count': 0,
        'final_count': 0,
        'modified_count': 0,
        'removed_empty': 0,
        'removed_min_length': 0,
        'removed_max_length': 0,
        'removed_dup_ids': 0,
        'renamed_dup_ids': 0,
        'removed_dup_seqs': 0,
        'removed_invalid_chars': 0,
        'removed_high_ambiguity': 0,
        'modified_uppercase': 0,
        'modified_gaps_stripped': 0,
        'modified_stops_stripped': 0,
        'modified_invalid_replaced': 0,
        'modified_invalid_removed': 0,
        'detected_alphabet': None,
        'alphabet_confidence': 0.0,
        'alphabet_was_autodetected': False,
        'warnings': [],
        'notices': [],
        'errors': [],
    }

    try:
        with open(file_path, 'rb') as _src:
            raw = _src.read()
        cleaned_bytes = raw.decode('ascii', errors='ignore').encode('ascii')
        if cleaned_bytes != raw:
            with open(file_path, 'wb') as _dst:
                _dst.write(cleaned_bytes)
            stats['notices'].append(
                "Non-ASCII characters were found in the file and ignored before cleaning."
            )
    except OSError:
        pass

    try:
        records = list(SeqIO.parse(file_path, 'fasta'))
    except Exception:
        stats['errors'].append("Could not parse the FASTA file. Please make sure it is a valid FASTA file.")
        return stats, None

    stats['original_count'] = len(records)

    if not records:
        stats['errors'].append("No sequences found in the uploaded file.")
        return stats, None

    sequence_type = options.get('sequence_type', 'auto')
    invalid_strategy = options.get('invalid_char_strategy', 'replace')
    remove_gaps = options.get('remove_gaps', False)
    remove_stop_chars = options.get('remove_stop_chars', False)
    do_uppercase = options.get('uppercase', True)
    rm_dup_ids = options.get('remove_duplicate_ids', False)
    rm_dup_seqs = options.get('remove_duplicate_seqs', False)
    min_len = options.get('min_length') or None
    max_len = options.get('max_length') or None
    max_ambiguity = options.get('max_ambiguity_percent')
    max_ambiguity = max_ambiguity if max_ambiguity not in (None, '') else None

    if sequence_type == 'auto':
        detected, confidence = detect_alphabet(records)
        stats['detected_alphabet'] = detected
        stats['alphabet_confidence'] = round(confidence * 100, 1)
        stats['alphabet_was_autodetected'] = True
        active_alphabet_name = detected
        if confidence < 0.70:
            stats['notices'].append(
                "Low-confidence alphabet detection. "
                "Consider selecting DNA or Protein manually."
            )
    else:
        stats['detected_alphabet'] = sequence_type
        stats['alphabet_confidence'] = 100.0
        stats['alphabet_was_autodetected'] = False
        active_alphabet_name = sequence_type

    if active_alphabet_name == 'dna':
        valid_set = DNA_ALPHABET
        replacement_char = 'N'
        ambiguity_char = 'N'
    elif active_alphabet_name == 'rna':
        valid_set = RNA_ALPHABET
        replacement_char = 'N'
        ambiguity_char = 'N'
    else:
        valid_set = PROTEIN_ALPHABET
        replacement_char = 'X'
        ambiguity_char = 'X'

    input_is_msa = looks_like_msa(records)
    if input_is_msa:
        if remove_gaps:
            stats['notices'].append(
                "Input looks like a multiple sequence alignment (equal-length sequences with gaps). "
                "Gap removal is enabled and will break the alignment."
            )
        elif invalid_strategy == 'remove':
            stats['notices'].append(
                "Input looks like a multiple sequence alignment. Removing invalid characters "
                "shifts positions and will break the alignment - consider Replace or Reject instead."
            )
        else:
            stats['notices'].append(
                "Input looks like a multiple sequence alignment. Alignment columns are preserved "
                "with the current settings."
            )

    cleaned = []
    seen_ids = set()
    seen_id_seq_pairs = set()
    seen_seqs = set()

    for record in records:
        seq_str = str(record.seq)
        original_seq = seq_str
        was_modified = False

        if do_uppercase and seq_str != seq_str.upper():
            seq_str = seq_str.upper()
            stats['modified_uppercase'] += 1
            was_modified = True

        check_str = seq_str.upper() if not do_uppercase else seq_str

        if remove_gaps:
            new_check = ''.join(c for c in check_str if c not in GAP_CHARS)
            new_seq = ''.join(c for c in seq_str if c not in GAP_CHARS)
            if new_seq != seq_str:
                stats['modified_gaps_stripped'] += 1
                was_modified = True
            seq_str = new_seq
            check_str = new_check

        if remove_stop_chars and STOP_CHAR in seq_str:
            seq_str = seq_str.replace(STOP_CHAR, '')
            check_str = check_str.replace(STOP_CHAR, '')
            stats['modified_stops_stripped'] += 1
            was_modified = True

        invalid_positions = [i for i, c in enumerate(check_str)
                             if c not in valid_set
                             and c != STOP_CHAR
                             and c not in GAP_CHARS]
        invalid_count = len(invalid_positions)

        if invalid_count > 0:
            if invalid_strategy == 'reject':
                stats['removed_invalid_chars'] += 1
                continue
            elif invalid_strategy == 'remove':
                seq_str = ''.join(c for i, c in enumerate(seq_str) if i not in set(invalid_positions))
                check_str = ''.join(c for i, c in enumerate(check_str) if i not in set(invalid_positions))
                stats['modified_invalid_removed'] += 1
                was_modified = True
            else:
                inv_set = set(invalid_positions)
                seq_str = ''.join(replacement_char if i in inv_set else c for i, c in enumerate(seq_str))
                check_str = ''.join(replacement_char if i in inv_set else c for i, c in enumerate(check_str))
                stats['modified_invalid_replaced'] += 1
                was_modified = True

            ratio = invalid_count / max(len(original_seq), 1)
            if ratio >= HIGH_INVALID_RATIO:
                stats['warnings'].append({
                    'id': record.id,
                    'invalid_count': invalid_count,
                    'invalid_ratio': round(ratio * 100, 1),
                })

        if not seq_str:
            stats['removed_empty'] += 1
            continue

        if max_ambiguity is not None:
            amb_count = check_str.count(ambiguity_char)
            amb_ratio = (amb_count / len(check_str)) * 100
            if amb_ratio > max_ambiguity:
                stats['removed_high_ambiguity'] += 1
                continue

        if min_len is not None and len(seq_str) < min_len:
            stats['removed_min_length'] += 1
            continue
        if max_len is not None and len(seq_str) > max_len:
            stats['removed_max_length'] += 1
            continue

        if rm_dup_ids:
            if record.id in seen_ids:
                id_seq_key = (record.id, seq_str.upper())
                if id_seq_key in seen_id_seq_pairs:
                    stats['removed_dup_ids'] += 1
                    continue
                else:
                    counter = 2
                    new_id = f"{record.id}_{counter}"
                    while new_id in seen_ids:
                        counter += 1
                        new_id = f"{record.id}_{counter}"
                    record = SeqRecord(Seq(seq_str), id=new_id, name=new_id, description='')
                    stats['renamed_dup_ids'] += 1
                    seen_ids.add(new_id)
                    seen_id_seq_pairs.add((new_id, seq_str.upper()))
            else:
                seen_ids.add(record.id)
                seen_id_seq_pairs.add((record.id, seq_str.upper()))

        if rm_dup_seqs:
            seq_key = seq_str.upper()
            if seq_key in seen_seqs:
                stats['removed_dup_seqs'] += 1
                continue
            seen_seqs.add(seq_key)

        if was_modified or seq_str != original_seq:
            stats['modified_count'] += 1

        new_record = SeqRecord(
            Seq(seq_str),
            id=record.id,
            name=record.name,
            description=record.description,
        )
        cleaned.append(new_record)

    stats['final_count'] = len(cleaned)

    PREVIEW_COUNT_MAX = 50
    PREVIEW_SEQ_CHARS = 240
    preview = []
    show_count = min(len(cleaned), PREVIEW_COUNT_MAX)
    for rec in cleaned[:show_count]:
        seq = str(rec.seq)
        truncated = len(seq) > PREVIEW_SEQ_CHARS
        preview.append({
            'id': rec.id,
            'length': len(seq),
            'seq': seq[:PREVIEW_SEQ_CHARS],
            'truncated': truncated,
        })
    stats['preview'] = preview
    stats['preview_remaining'] = max(0, len(cleaned) - show_count)

    if len(stats['warnings']) > 20:
        truncated = len(stats['warnings']) - 20
        stats['warnings'] = stats['warnings'][:20]
        stats['warnings_truncated'] = truncated

    if not cleaned:
        return stats, None

    os.makedirs(output_dir, exist_ok=True)
    out_filename = f"cleaned_{uuid.uuid4().hex[:8]}.fasta"
    out_path = os.path.join(output_dir, out_filename)

    try:
        with open(out_path, 'w') as f:
            SeqIO.write(cleaned, f, 'fasta')
    except OSError as e:
        stats['errors'].append(f"Failed to write cleaned file: {e}")
        return stats, None

    return stats, out_path
