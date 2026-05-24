import os
import tempfile

from django.test import TestCase

from biologine_aplikacija.parameter_builder import load_schema
from .views import (
    _BIOPYTHON_FMT,
    _HMMER_INFORMATS,
    _prevalidate_alignment,
)


STOCKHOLM_OK = """# STOCKHOLM 1.0
seq1 ACDEFGHIKLMNPQRSTVWY
seq2 ACDEFGHIKLMNPQRSTVWY
//
"""

FASTA_UNALIGNED = """>seq1
ACDEFGHIKLMNP
>seq2
ACDEFGHIKLMNPQRSTVWY
"""

CLUSTAL_OK = """CLUSTAL W (1.83) multiple sequence alignment

seq1            ACDEFGHIKL
seq2            ACDEFGHIKL
                **********
"""


def _tmp_file(contents):
    fd, path = tempfile.mkstemp(suffix='.txt')
    os.close(fd)
    with open(path, 'w') as f:
        f.write(contents)
    return path


class InformatSchemaConsistencyTests(TestCase):
    def test_schema_informat_choices_match_view_whitelist(self):
        schema = load_schema('hmmbuild')
        informat_param = next(
            p for p in schema['parameters'] if p['name'] == 'informat'
        )
        schema_options = set(informat_param['options'])
        self.assertEqual(
            schema_options, _HMMER_INFORMATS,
            f"hmmbuild.json --informat options drift from view whitelist:\n"
            f"  schema only: {schema_options - _HMMER_INFORMATS}\n"
            f"  view only:   {_HMMER_INFORMATS - schema_options}",
        )

    def test_biopython_map_keys_subset_of_whitelist(self):
        self.assertTrue(set(_BIOPYTHON_FMT.keys()).issubset(_HMMER_INFORMATS))


class PrevalidateAlignmentTests(TestCase):
    def test_stockholm_autodetect_accepts(self):
        path = _tmp_file(STOCKHOLM_OK)
        try:
            ok, err = _prevalidate_alignment(path)
            self.assertTrue(ok, err)
        finally:
            os.remove(path)

    def test_stockholm_explicit_informat_accepts(self):
        path = _tmp_file(STOCKHOLM_OK)
        try:
            ok, err = _prevalidate_alignment(path, informat='stockholm')
            self.assertTrue(ok, err)
        finally:
            os.remove(path)

    def test_clustal_autodetect_accepts(self):
        path = _tmp_file(CLUSTAL_OK)
        try:
            ok, err = _prevalidate_alignment(path)
            self.assertTrue(ok, err)
        finally:
            os.remove(path)

    def test_unaligned_fasta_rejected_with_friendly_message(self):
        path = _tmp_file(FASTA_UNALIGNED)
        try:
            ok, err = _prevalidate_alignment(path, informat='afa')
            self.assertFalse(ok)
            self.assertIn('not aligned', err.lower())
        finally:
            os.remove(path)

    def test_unknown_informat_rejected_loudly(self):
        path = _tmp_file(STOCKHOLM_OK)
        try:
            ok, err = _prevalidate_alignment(path, informat='nonsense')
            self.assertFalse(ok)
            self.assertIn('nonsense', err)
        finally:
            os.remove(path)

    def test_formats_without_biopython_parser_trust_hmmer(self):
        path = _tmp_file(STOCKHOLM_OK)
        try:
            for fmt in ('a2m', 'psiblast', 'selex'):
                ok, err = _prevalidate_alignment(path, informat=fmt)
                self.assertTrue(ok, f"informat={fmt} should be trusted: {err}")
        finally:
            os.remove(path)

    def test_empty_file_rejected(self):
        path = _tmp_file('')
        try:
            ok, err = _prevalidate_alignment(path, informat='stockholm')
            self.assertFalse(ok)
            self.assertIn('empty', err.lower())
        finally:
            os.remove(path)

    def test_missing_file_rejected(self):
        ok, err = _prevalidate_alignment('/nonexistent/path.sto')
        self.assertFalse(ok)

    def test_single_sequence_rejected_by_default(self):
        single_sto = "# STOCKHOLM 1.0\n\nseq1 MKTAYIAKQRQ\n//\n"
        path = _tmp_file(single_sto)
        try:
            ok, err = _prevalidate_alignment(path)
            self.assertFalse(ok)
            self.assertIn('at least 2', err.lower())
            self.assertIn('singlemx', err.lower())
        finally:
            os.remove(path)

    def test_single_sequence_accepted_with_singlemx(self):
        single_sto = "# STOCKHOLM 1.0\n\nseq1 MKTAYIAKQRQ\n//\n"
        path = _tmp_file(single_sto)
        try:
            ok, err = _prevalidate_alignment(path, singlemx=True)
            self.assertTrue(ok, f"--singlemx with a single sequence should pass: {err}")
        finally:
            os.remove(path)
