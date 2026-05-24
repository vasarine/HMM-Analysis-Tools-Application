"""
Shared helpers for the "upload a file or paste sequences" input that
several tools reuse.
"""

import os
from django import forms


INPUT_SOURCE_CHOICES = [
    ('upload', 'Upload file'),
    ('paste',  'Paste sequences'),
]

MAX_PASTE_BYTES = 5 * 1024 * 1024


def make_input_source_field():
    return forms.ChoiceField(
        label='Input',
        choices=INPUT_SOURCE_CHOICES,
        initial='upload',
        required=True,
        widget=forms.RadioSelect(attrs={'class': 'input-source-radio'}),
    )


def make_pasted_text_field(field_id='pasted_text', placeholder=None, rows=10):
    if placeholder is None:
        placeholder = (
            '>seq1\n'
            'MKVLWAALLVTFLAGCQAKVEQAVETEPEPELRQQTEWQ\n'
            '>seq2\n'
            'ACGTACGTACGTACGT\n'
        )
    return forms.CharField(
        label='Paste sequences',
        required=False,
        widget=forms.Textarea(attrs={
            'id':          field_id,
            'class':       'paste-textarea',
            'placeholder': placeholder,
            'rows':        rows,
            'spellcheck':  'false',
        }),
    )


def validate_input_source(form, file_field='fasta_file', paste_field='pasted_text',
                          source_field='input_source'):
    src = form.cleaned_data.get(source_field, 'upload')
    if src == 'paste':
        text = (form.cleaned_data.get(paste_field) or '').strip()
        if not text:
            form.add_error(paste_field, 'Please paste at least one sequence.')
        elif len(text.encode('utf-8')) > MAX_PASTE_BYTES:
            form.add_error(
                paste_field,
                f'Pasted text is too large (>{MAX_PASTE_BYTES // (1024 * 1024)} MB). '
                'Please use file upload instead.'
            )
    elif not form.cleaned_data.get(file_field):
        form.add_error(file_field, 'Please upload a file.')


def resolve_input_payload(form, file_field='fasta_file', paste_field='pasted_text',
                          source_field='input_source', default_ext='.fasta'):
    src = form.cleaned_data.get(source_field, 'upload')
    if src == 'paste':
        text = form.cleaned_data.get(paste_field) or ''
        return None, text, 'pasted_sequences' + default_ext, default_ext

    f = form.cleaned_data.get(file_field)
    if f is None:
        raise ValueError('No file uploaded')
    ext = os.path.splitext(f.name)[1].lower() or default_ext
    return f, None, f.name, ext


def write_input_payload(file_obj, text, dest_path):
    if file_obj is not None:
        with open(dest_path, 'wb+') as d:
            for chunk in file_obj.chunks():
                d.write(chunk)
    else:
        with open(dest_path, 'w', encoding='utf-8') as d:
            d.write(text or '')
