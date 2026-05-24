from django import forms
from django.core.validators import FileExtensionValidator
import re


class HMMSearchForm(forms.Form):
    HMM_SOURCE_CHOICES = [
        ('upload', 'Upload file'),
        ('library', 'Use Pfam/InterPro library'),
    ]

    name = forms.CharField(
        label="Project Name",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "id": "name",
            "placeholder": "Enter project name",
        }),
    )
    fasta_file = forms.FileField(
        label="Upload FASTA sequence file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=["fasta", "fa", "fna", "faa", "ffn", "frn", "txt"])],
        widget=forms.FileInput(attrs={
            "id": "fasta_file",
            "accept": ".fasta,.fa,.fna,.faa,.ffn,.frn,.txt",
        }),
    )

    pasted_fasta = forms.CharField(
        label="Paste FASTA sequences",
        required=False,
        widget=forms.Textarea(attrs={
            "id": "pasted_fasta",
            "class": "hf-paste-area",
            "placeholder": ">seq1\nMKTAYIAKQRQISFVKSHFSRQLEERLG...\n\n>seq2\nACGTTACG...",
            "rows": 8,
        }),
    )

    hmm_source = forms.ChoiceField(
        label="HMM Source",
        choices=HMM_SOURCE_CHOICES,
        initial='upload',
        required=True,
        widget=forms.RadioSelect(attrs={
            "id": "hmm_source",
            "class": "hmm-source-radio",
        }),
    )

    hmm_file = forms.FileField(
        label="Upload HMM model file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=["hmm"])],
        widget=forms.FileInput(attrs={
            "id": "hmm_file",
            "accept": ".hmm",
        }),
    )

    external_hmm_id = forms.CharField(
        label="Pfam/InterPro ID",
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={
            "id": "external_hmm_id",
            "placeholder": "Search... (e.g., PF00001, IPR000001, kinase)",
            "autocomplete": "off",
            "class": "hmm-autocomplete",
        }),
    )
    save_hits_msa = forms.BooleanField(
        label="Also save alignment of significant hits (-A)",
        required=False,
        help_text=(
            "Saves a multiple alignment of every hit that passes inclusion thresholds. "
            "Useful when you want to feed the recovered family back into hmmbuild."
        ),
    )
    save_pfamtbl = forms.BooleanField(
        label="Also save Pfam-format tabular output (--pfamtblout)",
        required=False,
        help_text=(
            "An additional tabular file in the format used by the Pfam database. "
            "Combines per-target and per-domain rows in one place. "
            "Useful if you need Pfam-style parsing downstream."
        ),
    )

    def __init__(self, *args, preloaded_hmm=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._preloaded_hmm = preloaded_hmm

        from biologine_aplikacija.parameter_builder import load_schema
        from biologine_aplikacija.parameter_builder.form_helpers import (
            build_form_fields_from_schema,
        )
        self._schema = load_schema("hmmsearch")
        for name, field in build_form_fields_from_schema(self._schema).items():
            self.fields[name] = field

    def clean(self):
        cleaned_data = super().clean()
        hmm_source = cleaned_data.get('hmm_source')

        pasted_fasta = (cleaned_data.get('pasted_fasta') or '').strip()
        if not cleaned_data.get('fasta_file') and not pasted_fasta:
            raise forms.ValidationError({
                'fasta_file': 'Upload a FASTA file or paste sequences below.'
            })
        if pasted_fasta and not pasted_fasta.lstrip().startswith('>'):
            raise forms.ValidationError({
                'pasted_fasta': 'Pasted content must be FASTA - start each entry with a > header line.'
            })

        if hmm_source == 'upload':
            if not cleaned_data.get('hmm_file') and not self._preloaded_hmm:
                raise forms.ValidationError({
                    'hmm_file': 'Please upload an HMM file or select library.'
                })

        elif hmm_source == 'library':
            external_hmm_id = (cleaned_data.get('external_hmm_id') or '').upper().strip()
            if not external_hmm_id:
                raise forms.ValidationError({
                    'external_hmm_id': 'Please enter a Pfam or InterPro ID.'
                })
            if not (re.match(r'^PF\d{5}$', external_hmm_id) or re.match(r'^IPR\d{6}$', external_hmm_id)):
                raise forms.ValidationError({
                    'external_hmm_id': 'Invalid format. Use Pfam (PF00001) or InterPro (IPR000001) ID.'
                })

        return cleaned_data
