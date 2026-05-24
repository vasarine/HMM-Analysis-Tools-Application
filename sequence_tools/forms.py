from django import forms
from django.core.validators import FileExtensionValidator

from biologine_aplikacija.input_utils import (
    make_input_source_field, make_pasted_text_field, validate_input_source,
)

FASTA_EXTENSIONS = ["fasta", "fa", "fna", "ffn", "faa", "frn", "txt"]
FASTA_ACCEPT = ".fasta,.fa,.fna,.ffn,.faa,.frn,.txt"


class FASTAValidateForm(forms.Form):
    name = forms.CharField(
        label="Project Name",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "id": "name",
            "placeholder": "Enter project name",
        }),
    )
    input_source = make_input_source_field()
    fasta_file = forms.FileField(
        label="Upload FASTA file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=FASTA_EXTENSIONS)],
        widget=forms.FileInput(attrs={
            "id": "fasta_file",
            "accept": FASTA_ACCEPT,
        }),
    )
    pasted_text = make_pasted_text_field(field_id='pasted_text')

    def clean(self):
        cleaned_data = super().clean()
        validate_input_source(self)
        return cleaned_data


class SequenceCleanForm(forms.Form):
    name = forms.CharField(
        label="Project Name",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "id": "sc_name",
            "placeholder": "Enter project name",
        }),
    )
    input_source = make_input_source_field()
    fasta_file = forms.FileField(
        label="Upload FASTA file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=FASTA_EXTENSIONS)],
        widget=forms.FileInput(attrs={
            "id": "sc_fasta_file",
            "accept": FASTA_ACCEPT,
        }),
    )
    pasted_text = make_pasted_text_field(field_id='sc_pasted_text')

    SEQUENCE_TYPE_CHOICES = [
        ('auto', 'Auto-detect'),
        ('protein', 'Protein'),
        ('dna', 'DNA'),
        ('rna', 'RNA'),
    ]
    sequence_type = forms.ChoiceField(
        label="Sequence type",
        choices=SEQUENCE_TYPE_CHOICES,
        initial='auto',
        required=True,
        widget=forms.RadioSelect(attrs={"class": "sc-type-radio"}),
    )

    INVALID_CHAR_CHOICES = [
        ('replace', 'Replace with X / N'),
        ('remove',  'Remove silently'),
        ('reject',  'Reject sequence'),
    ]
    invalid_char_strategy = forms.ChoiceField(
        label="Invalid characters",
        choices=INVALID_CHAR_CHOICES,
        initial='replace',
        required=True,
        widget=forms.RadioSelect(attrs={"class": "sc-invalid-radio"}),
    )

    remove_gaps = forms.BooleanField(
        required=False,
        initial=False,
        label="Remove gap characters",
        help_text="Strips -, ., ~ from all sequences. Leave off if the input is a multiple sequence alignment.",
    )
    remove_stop_chars = forms.BooleanField(
        required=False,
        initial=False,
        label="Remove stop codon characters (*)",
        help_text="Strips * from protein sequences.",
    )
    uppercase = forms.BooleanField(
        required=False,
        initial=True,
        label="Convert to uppercase",
        help_text="Standardizes sequence case.",
    )
    remove_duplicate_ids = forms.BooleanField(
        required=False,
        initial=True,
        label="Remove duplicate IDs",
        help_text="Keeps the first sequence when IDs repeat.",
    )
    remove_duplicate_seqs = forms.BooleanField(
        required=False,
        initial=False,
        label="Remove identical sequences",
        help_text="Keeps the first occurrence of each unique sequence.",
    )
    min_length = forms.IntegerField(
        required=False,
        min_value=1,
        label="Min length",
        widget=forms.NumberInput(attrs={
            "id": "sc_min_length",
            "placeholder": "e.g. 50",
            "class": "figma-input-short",
        }),
    )
    max_length = forms.IntegerField(
        required=False,
        min_value=1,
        label="Max length",
        widget=forms.NumberInput(attrs={
            "id": "sc_max_length",
            "placeholder": "e.g. 5000",
            "class": "figma-input-short",
        }),
    )
    max_ambiguity_percent = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=100,
        label="Max ambiguity (%)",
        help_text="Drop sequences whose N (DNA/RNA) or X (protein) content exceeds this percentage.",
        widget=forms.NumberInput(attrs={
            "id": "sc_max_ambiguity",
            "placeholder": "e.g. 20",
            "class": "figma-input-short",
        }),
    )

    def __init__(self, *args, preloaded_fasta=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._preloaded_fasta = preloaded_fasta
        if preloaded_fasta:
            self.fields['input_source'].required = False

    def clean(self):
        cleaned_data = super().clean()
        if not self._preloaded_fasta:
            validate_input_source(self)
        min_len = cleaned_data.get('min_length')
        max_len = cleaned_data.get('max_length')
        if min_len and max_len and min_len > max_len:
            raise forms.ValidationError("Min length cannot be greater than max length.")
        return cleaned_data
