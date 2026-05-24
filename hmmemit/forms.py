from django import forms
from django.core.validators import FileExtensionValidator
import re


class HMMEmitForm(forms.Form):
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

    hmm_source = forms.ChoiceField(
        label="HMM Source",
        choices=HMM_SOURCE_CHOICES,
        initial='upload',
        required=True,
        widget=forms.RadioSelect(attrs={
            "id": "hmm_source",
        }),
    )

    hmm_file = forms.FileField(
        label="Upload HMM file",
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
            "placeholder": "e.g., PF00001 or IPR000001",
            "class": "autocomplete-input",
        }),
    )

    num_seqs = forms.IntegerField(
        label="Number of sequences to generate",
        required=True,
        min_value=1,
        max_value=1000,
        initial=1,
        widget=forms.NumberInput(attrs={
            "id": "num_seqs",
            "min": 1,
            "max": 1000,
            "value": 1,
        }),
    )
    seed = forms.IntegerField(
        label="Random seed (optional)",
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={
            "id": "seed",
            "min": 0,
            "placeholder": "e.g., 42",
        }),
    )

    def __init__(self, *args, **kwargs):
        self.preloaded_hmm = kwargs.pop('preloaded_hmm', False)
        super().__init__(*args, **kwargs)

        from biologine_aplikacija.parameter_builder import load_schema
        from biologine_aplikacija.parameter_builder.form_helpers import (
            build_form_fields_from_schema,
        )
        self._schema = load_schema("hmmemit")
        for name, field in build_form_fields_from_schema(self._schema).items():
            self.fields[name] = field

    def clean(self):
        cleaned_data = super().clean()
        hmm_source = cleaned_data.get('hmm_source')

        if hmm_source == 'upload':
            if not cleaned_data.get('hmm_file') and not self.preloaded_hmm:
                raise forms.ValidationError({
                    'hmm_file': 'Please upload an HMM file or select a different source.'
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
            cleaned_data['external_hmm_id'] = external_hmm_id

        return cleaned_data
