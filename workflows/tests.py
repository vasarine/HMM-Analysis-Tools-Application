from django.test import TestCase

from biologine_aplikacija.parameter_builder.form_helpers import (
    extract_params_from_cleaned_data,
)
from .views import _normalize_hmmsearch_hmm_source


class NormalizeHmmsearchHmmSourceTests(TestCase):
    def test_hmmbuild_upstream_forces_previous_step(self):
        steps = [
            {'tool_type': 'hmmbuild', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'pfam'}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[1]['config']['hmm_source'], 'previous_step')

    def test_hmmbuild_upstream_overrides_upload_and_library(self):
        for stale in ('pfam', 'interpro', 'upload'):
            steps = [
                {'tool_type': 'hmmbuild', 'config': {}},
                {'tool_type': 'hmmsearch', 'config': {'hmm_source': stale}},
            ]
            _normalize_hmmsearch_hmm_source(steps)
            self.assertEqual(
                steps[1]['config']['hmm_source'], 'previous_step',
                f"hmm_source={stale!r} should be rewritten to previous_step",
            )

    def test_no_upstream_drops_stale_previous_step(self):
        steps = [
            {'tool_type': 'fasta_validate', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'previous_step'}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[1]['config']['hmm_source'], 'external_accession')

    def test_non_hmmbuild_predecessor_drops_previous_step(self):
        steps = [
            {'tool_type': 'clustal_omega', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'previous_step'}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[1]['config']['hmm_source'], 'external_accession')

    def test_hmmsearch_as_first_step_upgrades_legacy_pfam(self):
        steps = [
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'pfam'}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[0]['config']['hmm_source'], 'external_accession')

    def test_legacy_pfam_and_interpro_upgrade_for_hmmemit(self):
        for legacy in ('pfam', 'interpro'):
            steps = [
                {'tool_type': 'fasta_validate', 'config': {}},
                {'tool_type': 'hmmemit', 'config': {'hmm_source': legacy}},
            ]
            _normalize_hmmsearch_hmm_source(steps)
            self.assertEqual(
                steps[1]['config']['hmm_source'], 'external_accession',
                f"legacy hmmemit hmm_source={legacy!r} should upgrade",
            )

    def test_empty_config_is_safe_with_hmmbuild_upstream(self):
        steps = [
            {'tool_type': 'hmmbuild', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[1]['config']['hmm_source'], 'previous_step')

    def test_reorder_then_normalise_picks_correct_source(self):
        steps = [
            {'tool_type': 'fasta_validate', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'previous_step'}},
            {'tool_type': 'hmmbuild', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'pfam'}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[1]['config']['hmm_source'], 'external_accession')
        self.assertEqual(steps[3]['config']['hmm_source'], 'previous_step')

    def test_hmmbuild_upstream_forces_db_mode_upload(self):
        steps = [
            {'tool_type': 'hmmbuild', 'config': {}},
            {'tool_type': 'hmmsearch', 'config': {'hmm_source': 'pfam', 'db_mode': 'previous_step'}},
        ]
        _normalize_hmmsearch_hmm_source(steps)
        self.assertEqual(steps[1]['config']['hmm_source'], 'previous_step')
        self.assertEqual(steps[1]['config']['db_mode'], 'upload')


class CircularChainValidationTests(TestCase):
    def test_circular_chain_flagged(self):
        from .validation import validate_workflow
        steps = [
            {'tool_type': 'fasta_validate', 'config': {}},
            {'tool_type': 'clustal_omega', 'config': {}},
            {'tool_type': 'hmmbuild', 'config': {}},
            {'tool_type': 'hmmsearch',
             'config': {'hmm_source': 'previous_step', 'db_mode': 'previous_step'}},
        ]
        result = validate_workflow(steps)
        codes = {i.code for i in result.issues}
        self.assertIn('circular_chain_db', codes,
                      f"Expected circular_chain_db in {codes}")

    def test_normal_chain_no_circular_warning(self):
        from .validation import validate_workflow
        steps = [
            {'tool_type': 'fasta_validate', 'config': {}},
            {'tool_type': 'clustal_omega', 'config': {}},
            {'tool_type': 'hmmbuild', 'config': {}},
            {'tool_type': 'hmmsearch',
             'config': {'hmm_source': 'previous_step', 'db_mode': 'upload'}},
        ]
        result = validate_workflow(steps)
        codes = {i.code for i in result.issues}
        self.assertNotIn('circular_chain_db', codes)


class DependsOnBackendFilterTests(TestCase):
    def test_hidden_dependent_value_is_excluded(self):
        schema = {
            'parameters': [
                {'name': 'mode', 'type': 'select', 'options': ['off', 'on'], 'default': 'off'},
                {'name': 'matrix', 'type': 'string',
                 'depends_on': {'field': 'mode', 'value': 'on'}, 'default': None},
            ],
        }
        cleaned = {'param_mode': 'off', 'param_matrix': 'BLOSUM62'}
        result = extract_params_from_cleaned_data(cleaned, schema)
        self.assertNotIn('matrix', result)
        self.assertNotIn('mode', result)

    def test_visible_dependent_value_passes_through(self):
        schema = {
            'parameters': [
                {'name': 'mode', 'type': 'select', 'options': ['off', 'on'], 'default': 'off'},
                {'name': 'matrix', 'type': 'string',
                 'depends_on': {'field': 'mode', 'value': 'on'}, 'default': None},
            ],
        }
        cleaned = {'param_mode': 'on', 'param_matrix': 'BLOSUM62'}
        result = extract_params_from_cleaned_data(cleaned, schema)
        self.assertEqual(result.get('matrix'), 'BLOSUM62')
        self.assertEqual(result.get('mode'), 'on')

    def test_chained_dependency_filters_grandchild(self):
        schema = {
            'parameters': [
                {'name': 'A', 'type': 'select', 'options': ['off', 'on'], 'default': 'off'},
                {'name': 'B', 'type': 'select', 'options': ['x', 'y'], 'default': 'x',
                 'depends_on': {'field': 'A', 'value': 'on'}},
                {'name': 'C', 'type': 'string', 'default': None,
                 'depends_on': {'field': 'B', 'value': 'y'}},
            ],
        }
        cleaned = {'param_A': 'off', 'param_B': 'y', 'param_C': 'value'}
        result = extract_params_from_cleaned_data(cleaned, schema)
        self.assertNotIn('B', result)
        self.assertNotIn('C', result)
