import os
from django.db import models
from biologine_aplikacija.models import BaseHMMProject


class HMMEmitProject(BaseHMMProject):
    hmm_file = models.FileField(upload_to="hmmemit/", null=True, blank=True)
    output_file = models.FileField(upload_to="hmmemit/", null=True, blank=True)

    hmm_source = models.CharField(max_length=20, default='upload', null=True, blank=True)
    external_hmm_id = models.CharField(max_length=50, null=True, blank=True)
    external_hmm_name = models.CharField(max_length=255, null=True, blank=True)
    parameters = models.JSONField(default=dict, blank=True)

    def get_result_files(self):
        name = self.name or 'result'
        if self.output_file and self.output_file.name:
            try:
                if os.path.exists(self.output_file.path):
                    return [{'label': 'Emitted sequences', 'url': self.output_file.url, 'filename': f'{name}.fa', 'type': 'main'}]
            except Exception:
                pass
        return []
