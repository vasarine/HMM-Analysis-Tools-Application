from django import forms
from django.core.validators import FileExtensionValidator

from biologine_aplikacija.input_utils import (
    make_input_source_field, make_pasted_text_field, validate_input_source,
)

_MSA_PASTE_PLACEHOLDER = (
    '# STOCKHOLM 1.0\n'
    'seq1  MKVLWAALLVTFLAGCQA\n'
    'seq2  MKVLWAALV-TFLAGCQA\n'
)


class HMMBuildForm(forms.Form):
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
    msa_file = forms.FileField(
        label="Upload alignment (MSA) file",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=["sto", "stk", "aln", "phy", "fa", "fasta", "afa", "a2m"])],
        widget=forms.FileInput(attrs={
            "id": "msa_file",
            "accept": ".sto,.stk,.aln,.phy,.fa,.fasta,.afa,.a2m",
        }),
    )
    pasted_text = make_pasted_text_field(
        field_id='hb_pasted_text',
        placeholder=_MSA_PASTE_PLACEHOLDER,
        rows=10,
    )
    save_annotated_msa = forms.BooleanField(
        label="Also save the annotated source MSA (-O)",
        required=False,
        help_text=(
            "Save the input alignment back, annotated with consensus columns and "
            "the relative sequence weights HMMER assigned. Useful for inspecting "
            "what hmmbuild did to your input."
        ),
    )

    def __init__(self, *args, preloaded_msa=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._preloaded_msa = preloaded_msa
        if preloaded_msa:
            self.fields['input_source'].required = False

        from biologine_aplikacija.parameter_builder import load_schema
        from biologine_aplikacija.parameter_builder.form_helpers import (
            build_form_fields_from_schema,
        )
        self._schema = load_schema("hmmbuild")
        for name, field in build_form_fields_from_schema(self._schema).items():
            self.fields[name] = field

    def clean(self):
        cleaned_data = super().clean()
        if not self._preloaded_msa:
            validate_input_source(self, file_field='msa_file', paste_field='pasted_text')
        return cleaned_data
