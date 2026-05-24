from django import forms
from django.core.validators import FileExtensionValidator

from biologine_aplikacija.input_utils import (
    make_input_source_field, make_pasted_text_field, validate_input_source,
)

ALIGNMENT_EXTENSIONS = ["fasta", "fa", "afa", "sto", "stk", "aln", "phy", "txt"]
ALIGNMENT_ACCEPT = ".fasta,.fa,.afa,.sto,.stk,.aln,.phy,.txt"


class ClustalOmegaForm(forms.Form):
    TOOL_CHOICES = [
        ('clustalo', 'Clustal Omega'),
        ('mafft', 'MAFFT'),
        ('muscle', 'MUSCLE'),
        ('kalign', 'Kalign'),
    ]

    ALL_OUTPUT_CHOICES = [
        ('stockholm', 'Stockholm (.sto)'),
        ('clustal', 'Clustal (.aln)'),
        ('fasta', 'Aligned FASTA (.fasta)'),
        ('phylip', 'Phylip (.phy)'),
        ('vienna', 'Vienna (.vie)'),
    ]

    tool = forms.ChoiceField(
        label="MSA Tool",
        choices=TOOL_CHOICES,
        initial='clustalo',
        widget=forms.RadioSelect(attrs={"class": "msa-tool-radio"}),
    )
    name = forms.CharField(
        label="Project Name",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "id": "co_name",
            "placeholder": "Enter project name",
        }),
    )
    input_source = make_input_source_field()
    fasta_file = forms.FileField(
        label="Upload FASTA file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=["fasta", "fa", "fna", "faa", "txt"])],
        widget=forms.FileInput(attrs={
            "id": "co_fasta_file",
            "accept": ".fasta,.fa,.fna,.faa,.txt",
        }),
    )
    pasted_text = make_pasted_text_field(field_id='co_pasted_text')
    output_format = forms.ChoiceField(
        label="Output format",
        choices=ALL_OUTPUT_CHOICES,
        initial="stockholm",
        widget=forms.RadioSelect(attrs={"class": "co-radio"}),
    )

    def __init__(self, *args, preloaded_fasta=False, tool='clustalo', **kwargs):
        super().__init__(*args, **kwargs)
        self._preloaded_fasta = preloaded_fasta
        if preloaded_fasta:
            self.fields['input_source'].required = False

        self._tool = tool
        self.fields['tool'].initial = tool

        from biologine_aplikacija.parameter_builder import load_schema
        from biologine_aplikacija.parameter_builder.form_helpers import (
            build_form_fields_from_schema,
        )
        self._schema = load_schema(tool)
        for name, field in build_form_fields_from_schema(self._schema).items():
            self.fields[name] = field

        supported = self._schema.get('supported_output_formats', ['fasta'])
        self.fields['output_format'].choices = [
            (val, lbl) for (val, lbl) in self.ALL_OUTPUT_CHOICES if val in supported
        ]
        if self.fields['output_format'].initial not in supported:
            self.fields['output_format'].initial = supported[0]

    def clean(self):
        cleaned_data = super().clean()
        if not self._preloaded_fasta:
            validate_input_source(self)
        return cleaned_data


class FormatConvertForm(forms.Form):
    name = forms.CharField(
        label="Project Name",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "id": "fc_name",
            "placeholder": "Enter project name",
        }),
    )
    input_source = make_input_source_field()
    input_file = forms.FileField(
        label="Upload alignment file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=ALIGNMENT_EXTENSIONS)],
        widget=forms.FileInput(attrs={
            "id": "fc_input_file",
            "accept": ALIGNMENT_ACCEPT,
        }),
    )
    pasted_text = make_pasted_text_field(field_id='fc_pasted_text')
    output_format = forms.ChoiceField(
        label="Output format",
        choices=[
            ("stockholm", "Stockholm (.sto)"),
            ("fasta", "Aligned FASTA (.fasta)"),
            ("clustal", "Clustal (.aln)"),
        ],
        initial="stockholm",
        widget=forms.RadioSelect(attrs={"class": "fc-radio"}),
    )

    def __init__(self, *args, preloaded_alignment=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._preloaded_alignment = preloaded_alignment
        if preloaded_alignment:
            self.fields['input_source'].required = False

    def clean(self):
        cleaned_data = super().clean()
        if not self._preloaded_alignment:
            validate_input_source(self, file_field='input_file')
        return cleaned_data
