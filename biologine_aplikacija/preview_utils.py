import os
from dataclasses import dataclass


@dataclass
class PreviewResult:
    text: str = ''
    is_truncated: bool = False
    shown_lines: int = 0
    max_lines: int = 50
    max_chars: int = 10000
    label: str = ''
    note: str = ''
    error: str = ''


def _preview_note(result):
    if result.error:
        return result.error
    if result.is_truncated:
        return (
            f"Preview limited to first {result.shown_lines} lines. "
            "Download the file to view the full result."
        )
    return ''


def _error_result(error_msg, max_lines, max_chars, label):
    result = PreviewResult(max_lines=max_lines, max_chars=max_chars, label=label)
    result.error = error_msg
    result.note = error_msg
    return result


def _bounded_preview(line_iter, max_lines, max_chars, label):
    lines = []
    total_chars = 0
    truncated = False

    for line in line_iter:
        if len(lines) >= max_lines or total_chars >= max_chars:
            truncated = True
            break

        remaining = max_chars - total_chars
        if len(line) > remaining:
            line = line[:remaining]
            truncated = True

        lines.append(line)
        total_chars += len(line) + 1
        if truncated:
            break

    result = PreviewResult(
        text='\n'.join(lines),
        is_truncated=truncated,
        shown_lines=len(lines),
        max_lines=max_lines,
        max_chars=max_chars,
        label=label,
    )
    result.note = _preview_note(result)
    return result


def _resolve_path(file_obj):
    try:
        return file_obj.path, None
    except AttributeError:
        return str(file_obj), None
    except (NotImplementedError, ValueError):
        return None, 'Preview is not available for this file storage.'


def read_file_preview(file_obj, max_lines=50, max_chars=10000, label=''):
    if not file_obj:
        result = PreviewResult(max_lines=max_lines, max_chars=max_chars, label=label)
        result.note = 'No preview file is available.'
        return result

    path, err = _resolve_path(file_obj)
    if err:
        return _error_result(err, max_lines, max_chars, label)
    if not path or not os.path.exists(path):
        return _error_result('Preview file is missing.', max_lines, max_chars, label)

    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return _bounded_preview(
                (line.rstrip('\n\r') for line in handle),
                max_lines, max_chars, label,
            )
    except OSError:
        return _error_result('Preview could not be read.', max_lines, max_chars, label)


def read_text_preview(text, max_lines=50, max_chars=10000, label=''):
    if not text:
        result = PreviewResult(max_lines=max_lines, max_chars=max_chars, label=label)
        result.note = 'No preview text is available.'
        return result

    return _bounded_preview(str(text).splitlines(), max_lines, max_chars, label)


def read_file_full(file_obj, label=''):
    if not file_obj:
        result = PreviewResult(label=label)
        result.note = 'No file is available.'
        return result
    path, err = _resolve_path(file_obj)
    if err:
        result = PreviewResult(label=label)
        result.error = err
        result.note = err
        return result
    if not path or not os.path.exists(path):
        result = PreviewResult(label=label)
        result.error = 'File is missing.'
        result.note = 'File is missing.'
        return result
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            text = handle.read()
        lines_count = text.count('\n') + 1 if text else 0
        return PreviewResult(
            text=text.rstrip('\n'),
            is_truncated=False,
            shown_lines=lines_count,
            label=label,
        )
    except OSError:
        result = PreviewResult(label=label)
        result.error = 'File could not be read.'
        result.note = 'File could not be read.'
        return result


def build_preview_note(preview, of_label='the file'):
    if preview is None or not preview.shown_lines:
        return ''
    if preview.is_truncated:
        return (
            f"Showing first {preview.shown_lines} lines of {of_label}. "
            "Download the full file to view all results."
        )
    return f"Showing all {preview.shown_lines} lines of {of_label}."


def stats_preview(lines, label='Summary'):
    text = '\n'.join(str(line) for line in lines if line)
    result = PreviewResult(
        text=text,
        is_truncated=False,
        shown_lines=len(text.splitlines()) if text else 0,
        label=label,
    )
    result.note = 'Summary preview only. Input sequence content is not shown here.'
    return result
