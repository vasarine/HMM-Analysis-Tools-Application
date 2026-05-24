import os
from django.db import models
from biologine_aplikacija.models import BaseHMMProject


class HMMBuildProject(BaseHMMProject):
    msa_file = models.FileField(upload_to="hmmbuild/")
    hmm_file = models.FileField(upload_to="hmmbuild/", null=True, blank=True)
    annotated_msa_file = models.FileField(upload_to="hmmbuild/", null=True, blank=True)
    matrix_file = models.FileField(upload_to="hmmbuild/", null=True, blank=True)
    parameters = models.JSONField(default=dict, blank=True)

    def get_result_files(self):
        name = self.name or 'result'
        candidates = [
            (self.hmm_file,           'HMM profile',   f'{name}.hmm', 'main'),
            (self.annotated_msa_file, 'Annotated MSA', f'{name}_annotated.sto', 'alignment'),
        ]
        result = []
        for field, label, filename, file_type in candidates:
            if not (field and field.name):
                continue
            try:
                if os.path.exists(field.path):
                    result.append({'label': label, 'url': field.url, 'filename': filename, 'type': file_type})
            except Exception:
                pass
        return result
