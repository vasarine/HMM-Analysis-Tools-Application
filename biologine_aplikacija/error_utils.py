"""
Turns raw tool and Python errors into short, clean messages that are
safe to show to the user.
"""

import re


_PATH_RE = re.compile(r"['\"]?/[^\s'\"]+['\"]?")
_PY_EXC_PREFIX_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_.]*Error|Exception|OSError|IOError|RuntimeError|"
    r"ValueError|TypeError|KeyError|FileNotFoundError|PermissionError|"
    r"SubprocessError|CalledProcessError):\s*"
)
_INTERNAL_MARKERS = (
    '<lambda>', 'unexpected keyword argument', 'positional argument',
    'takes no arguments', 'object has no attribute',
    'nonetype', 'traceback', 'assertion', 'cannot import',
    'module named', 'not subscriptable', 'not callable',
    'not iterable', 'got multiple values',
)


def _strip_paths(text):
    return _PATH_RE.sub("<file>", text)


def sanitize_exception_message(exc, fallback="Something went wrong. Please try again."):
    if exc is None:
        return fallback
    text = str(exc).strip()
    if not text:
        return fallback

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        text = lines[-1]

    text = _PY_EXC_PREFIX_RE.sub("", text)
    text = _strip_paths(text).strip()
    if not text:
        return fallback

    lowered = text.lower()
    if any(marker in lowered for marker in _INTERNAL_MARKERS):
        return fallback

    if len(text) > 300:
        text = text[:297] + "..."
    if text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def parse_hmmbuild_error(stderr):
    if not stderr:
        return "HMMBUILD failed for an unknown reason."
    s = stderr.lower()

    if "failed to parse command line" in s or "unrecognized option" in s:
        for line in stderr.splitlines():
            l = line.strip()
            if l and ("failed to parse" in l.lower() or "unrecognized option" in l.lower()):
                return _strip_paths(l)[:300]
    if "conflict" in s and "toggling" in s:
        for line in stderr.splitlines():
            if "conflict" in line.lower():
                return _strip_paths(line.strip())[:300]
    if "alen" in s and ("expected" in s or "parse error" in s):
        return "Sequences have different lengths - the file is not aligned. Run MSA first, then convert to Stockholm format."
    if "invalid sequence character" in s or "bad sequence" in s or "illegal character" in s:
        return ("The file contains characters that don't match the selected alphabet. "
                "Check your --amino / --dna / --rna choice, or let HMMER autodetect.")
    if "alphabet" in s and ("mismatch" in s or "does not match" in s or "doesn't match" in s):
        return ("Sequence alphabet does not match the selected one. "
                "Check your --amino / --dna / --rna choice.")
    if ("not in stockholm" in s or "not a stockholm" in s or "bad #=" in s
            or "format not recognized" in s):
        return "Input file must be in Stockholm format. Use the Format Converter to convert your alignment first."
    if "no sequences" in s or "zero length" in s:
        return "No sequences found in the file. Make sure the file is not empty."
    if "empty" in s:
        return "The input file appears to be empty."
    if "only 1 seq" in s or "need at least" in s:
        return "At least 2 sequences are required to build an HMM profile."
    if "binary" in s:
        return "File appears to be in binary format. Provide a plain-text Stockholm file."
    if "memory" in s or "alloc" in s:
        return "Not enough memory to process this file. Try with fewer or shorter sequences."

    for line in stderr.splitlines():
        line = line.strip()
        if line and "error" in line.lower():
            return _strip_paths(line)[:300]
    for line in stderr.splitlines():
        line = line.strip()
        if line and not line.lower().startswith(('usage:', 'where ', '  ', 'to see')):
            return _strip_paths(line)[:300]
    return "HMMBUILD failed."


def parse_hmmemit_error(stderr):
    if not stderr:
        return "HMMEMIT failed for an unknown reason."
    s = stderr.lower()

    if "failed to parse command line" in s or "unrecognized option" in s:
        for line in stderr.splitlines():
            l = line.strip()
            if l and ("failed to parse" in l.lower() or "unrecognized option" in l.lower()):
                return _strip_paths(l)[:200]
    if "no such file" in s or "cannot open" in s or "not found" in s:
        return "HMM profile file could not be found or opened."
    if "not a valid hmm" in s or "bad magic" in s:
        return "The file does not appear to be a valid HMM profile."
    if "parse" in s and ("hmm" in s or "magic" in s):
        return "The file does not appear to be a valid HMM profile."
    if "memory" in s:
        return "Not enough memory to run HMMEMIT."
    if "-n " in s or "n sequences" in s or "number of sampled sequences" in s:
        return "Invalid value for -N (number of sequences). Use a positive integer."

    for line in stderr.splitlines():
        line = line.strip()
        if line and "error" in line.lower():
            return _strip_paths(line[:200])

    return _strip_paths(stderr.splitlines()[0][:200]) if stderr else "HMMEMIT failed."


def parse_hmmsearch_error(stderr):
    if not stderr:
        return "HMMSEARCH failed for an unknown reason."
    s = stderr.lower()

    if "failed to parse command line" in s or "unrecognized option" in s:
        for line in stderr.splitlines():
            l = line.strip()
            if l and ("failed to parse" in l.lower() or "unrecognized option" in l.lower()):
                return _strip_paths(l)[:200]
    if "no such file" in s or "cannot open" in s:
        return "One of the input files could not be found or opened."
    if "parse" in s and "sequence file" in s:
        return "The sequence database file is not valid FASTA. Please upload a properly formatted FASTA file as the search database."
    if "not a valid hmm" in s or "bad magic" in s:
        return "The HMM profile file is not valid. Rebuild it with hmmbuild."
    if "parse" in s and "hmm" in s:
        return "The HMM profile file is not valid. Rebuild it with hmmbuild."
    if "no sequences" in s or "empty" in s:
        return "No sequences found in the target database file."
    if "memory" in s:
        return "Not enough memory to run HMMSEARCH."

    for line in stderr.splitlines():
        line = line.strip()
        if line and "error" in line.lower():
            return _strip_paths(line[:200])

    return _strip_paths(stderr.splitlines()[0][:200]) if stderr else "HMMSEARCH failed."


def parse_clustalo_error(stderr):
    if not stderr:
        return "Clustal Omega failed for an unknown reason."
    s = stderr.lower()

    if "contains 1 sequence" in s or "nothing to align" in s:
        return "At least 2 sequences are required for alignment."
    if "empty sequence" in s or "zero length" in s:
        return "One or more sequences are empty. Validate your FASTA file first."
    if "only 1 seq" in s or "need at least 2" in s or "at least two" in s:
        return "At least 2 sequences are required for alignment."
    if "out of memory" in s or "bad_alloc" in s:
        return "Not enough memory. Try with fewer sequences."
    if "not a valid" in s or "unknown format" in s or "parse error" in s or "fatal" in s:
        first = next(
            (l.strip() for l in stderr.splitlines()
             if l.strip() and "fatal" in l.lower()),
            None
        )
        if first:
            clean = re.sub(r"FATAL:\s*File\s*'[^']+'\s*", "", first, flags=re.IGNORECASE).strip()
            if clean:
                return clean[:200]
        return "Could not parse the input file. Make sure it is a valid FASTA file."

    for line in stderr.splitlines():
        line = line.strip()
        if line and not line.startswith("Using") and not line.startswith("Clustal"):
            return _strip_paths(line[:200])

    return "Alignment failed."
