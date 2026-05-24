import os
from django.db import models
from biologine_aplikacija.models import BaseHMMProject


class HMMSearchProject(BaseHMMProject):
    fasta_file = models.FileField(upload_to="hmmsearch/")
    hmm_file = models.FileField(upload_to="hmmsearch/", null=True, blank=True)
    out_file = models.FileField(upload_to="hmmsearch/", null=True, blank=True)
    tblout_file = models.FileField(upload_to="hmmsearch/", null=True, blank=True)
    domtbl_file = models.FileField(upload_to="hmmsearch/", null=True, blank=True)

    tblout_text = models.TextField(null=True, blank=True)
    domtbl_text = models.TextField(null=True, blank=True)
    hits_msa_file = models.FileField(upload_to="hmmsearch/", null=True, blank=True)
    pfamtbl_file = models.FileField(upload_to="hmmsearch/", null=True, blank=True)

    hmm_source = models.CharField(max_length=20, default='upload', null=True, blank=True)
    external_hmm_id = models.CharField(max_length=50, null=True, blank=True)
    external_hmm_name = models.CharField(max_length=255, null=True, blank=True)
    parameters = models.JSONField(default=dict, blank=True)

    def get_result_files(self):
        name = self.name or 'result'
        candidates = [
            (self.out_file,      'Search results',       f'{name}.out',      'main'),
            (self.tblout_file,   'Per-sequence hits',    f'{name}.tblout',   'table'),
            (self.domtbl_file,   'Per-domain hits',      f'{name}.domtbl',   'table'),
            (self.hits_msa_file, 'Hits alignment',       f'{name}.sto',      'alignment'),
            (self.pfamtbl_file,  'Pfam-format table',    f'{name}.pfamtbl',  'table'),
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
