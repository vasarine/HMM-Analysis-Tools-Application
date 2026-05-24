from django import forms
from django.test import TestCase

from biologine_aplikacija.parameter_builder import (
    CommandBuilder, SCHEMAS_DIR, load_schema,
)
from biologine_aplikacija.parameter_builder.form_helpers import (
    build_form_fields_from_schema,
    extract_params_from_cleaned_data,
)


def _schema():
    return {
        "tool_id": "demo",
        "display_name": "Demo",
        "executable": "demo",
        "fixed_args": ["--force"],
        "parameters": [
            {"name": "threads", "label": "Threads", "flag": "--threads",
             "type": "number", "default": 1, "min": 1, "max": 16},
            {"name": "iter", "label": "Iter", "flag": "--iter",
             "type": "number", "default": 0, "min": 0, "max": 5},
            {"name": "outfmt", "label": "Output format", "flag": "--outfmt",
             "type": "select", "default": None,
             "options": ["fasta", "clustal", "stockholm"], "flag_style": "equals"},
            {"name": "auto", "label": "Auto", "flag": "--auto",
             "type": "boolean", "default": False},
            {"name": "verbose", "label": "Verbose", "flag": "--verbose",
             "type": "boolean", "default": True},
        ],
    }


def _ui_schema():
    return {
        "tool_id": "demo",
        "executable": "demo",
        "parameters": [
            {"name": "threads", "label": "Threads", "flag": "--threads",
             "type": "number", "default": 1, "min": 1, "max": 16},
            {"name": "full", "label": "Full", "flag": "--full",
             "type": "boolean", "default": False},
            {"name": "mode", "label": "Mode", "flag": "--mode",
             "type": "select", "default": "fast",
             "options": ["fast", "thorough"]},
            {"name": "seqtype", "label": "Seq type", "flag": "--seqtype",
             "type": "select", "default": None,
             "options": ["Protein", "DNA"]},
            {"name": "tag", "label": "Tag", "flag": "--tag",
             "type": "string", "default": ""},
            {"name": "outfmt", "label": "Output format", "flag": "--outfmt",
             "type": "select", "default": None,
             "options": ["fasta", "stockholm"], "ui_hidden": True},
        ],
    }


def _radio_schema():
    return {
        "tool_id": "demo",
        "executable": "demo",
        "parameters": [
            {
                "name": "cutoff_mode",
                "label": "Cutoff",
                "type": "radio_group",
                "default": "evalue",
                "options": [
                    {"value": "evalue", "label": "E-value", "flag": None},
                    {"value": "ga", "label": "GA", "flag": "--cut_ga"},
                    {"value": "nc", "label": "NC", "flag": "--cut_nc"},
                ],
            },
            {
                "name": "e_value",
                "label": "E-value",
                "flag": "-E",
                "type": "float",
                "default": 10.0,
                "depends_on": {"field": "cutoff_mode", "value": "evalue"},
            },
        ],
    }


class CommandBuilderTests(TestCase):

    def test_build_with_no_values_uses_defaults_and_io(self):
        b = CommandBuilder(_schema())
        cmd = b.build(io_flags={"-i": "in.fa", "-o": "out.sto"})
        self.assertEqual(cmd, ["demo", "--force", "-i", "in.fa", "-o", "out.sto"])

    def test_numeric_non_default_uses_separate_flag(self):
        b = CommandBuilder(_schema()).set("threads", 4)
        cmd = b.build()
        self.assertIn("--threads", cmd)
        idx = cmd.index("--threads")
        self.assertEqual(cmd[idx + 1], "4")

    def test_equals_style_select(self):
        b = CommandBuilder(_schema()).set("outfmt", "stockholm")
        cmd = b.build()
        self.assertIn("--outfmt=stockholm", cmd)

    def test_boolean_true_with_default_false_emits_bare_flag(self):
        b = CommandBuilder(_schema()).set("auto", True)
        cmd = b.build()
        self.assertIn("--auto", cmd)
        idx = cmd.index("--auto")
        if idx + 1 < len(cmd):
            self.assertNotEqual(cmd[idx + 1], "True")
            self.assertNotEqual(cmd[idx + 1], "true")

    def test_boolean_false_with_default_true_is_ignored(self):
        b = CommandBuilder(_schema()).set("verbose", False)
        cmd = b.build()
        self.assertNotIn("--verbose", cmd)

    def test_invalid_select_value_validate_and_build(self):
        b = CommandBuilder(_schema()).set("outfmt", "nope")
        errors = b.validate()
        self.assertTrue(any("outfmt" in e for e in errors))
        with self.assertRaises(ValueError):
            b.build()

    def test_number_out_of_range_raises(self):
        b = CommandBuilder(_schema()).set("threads", 999)
        self.assertTrue(b.validate())
        with self.assertRaises(ValueError):
            b.build()

    def test_unknown_parameter_raises(self):
        b = CommandBuilder(_schema())
        with self.assertRaises(ValueError):
            b.set("does_not_exist", 1)

    def test_io_flags_first_positional_last(self):
        b = CommandBuilder(_schema()).set("threads", 8).set("auto", True)
        cmd = b.build(
            io_flags={"-i": "in.fa", "-o": "out.sto"},
            positional_args=["tail-arg"],
        )
        self.assertEqual(cmd[0], "demo")
        self.assertEqual(cmd[1], "--force")
        self.assertEqual(cmd[2:6], ["-i", "in.fa", "-o", "out.sto"])
        self.assertEqual(cmd[-1], "tail-arg")
        self.assertIn("--threads", cmd[6:-1])
        self.assertIn("--auto", cmd[6:-1])

    def test_preview_returns_string(self):
        b = CommandBuilder(_schema()).set("threads", 2)
        text = b.preview(io_flags={"-i": "a", "-o": "b"})
        self.assertIn("demo", text)
        self.assertIn("--threads 2", text)


class BuildFormFieldsTests(TestCase):

    def test_field_types_match_schema_types(self):
        fields = build_form_fields_from_schema(_ui_schema())
        self.assertIsInstance(fields["param_threads"], forms.IntegerField)
        self.assertIsInstance(fields["param_full"], forms.BooleanField)
        self.assertIsInstance(fields["param_mode"], forms.ChoiceField)
        self.assertIsInstance(fields["param_seqtype"], forms.ChoiceField)
        self.assertIsInstance(fields["param_tag"], forms.CharField)

    def test_ui_hidden_param_is_skipped(self):
        fields = build_form_fields_from_schema(_ui_schema())
        self.assertNotIn("param_outfmt", fields)

    def test_integer_field_min_max_from_schema(self):
        fields = build_form_fields_from_schema(_ui_schema())
        threads = fields["param_threads"]
        self.assertEqual(threads.min_value, 1)
        self.assertEqual(threads.max_value, 16)
        self.assertEqual(threads.initial, 1)

    def test_choice_field_options_match_schema(self):
        fields = build_form_fields_from_schema(_ui_schema())
        mode_choices = [c[0] for c in fields["param_mode"].choices]
        self.assertEqual(mode_choices, ["fast", "thorough"])
        self.assertEqual(fields["param_mode"].initial, "fast")

    def test_select_with_null_default_has_empty_choice(self):
        fields = build_form_fields_from_schema(_ui_schema())
        seqtype_choices = [c[0] for c in fields["param_seqtype"].choices]
        self.assertEqual(seqtype_choices[0], "")
        self.assertEqual(fields["param_seqtype"].initial, "")

    def test_all_fields_use_param_prefix(self):
        fields = build_form_fields_from_schema(_ui_schema(), prefix="x_")
        self.assertIn("x_threads", fields)
        self.assertNotIn("param_threads", fields)


class ExtractParamsTests(TestCase):

    def test_drops_default_values(self):
        cleaned = {
            "name": "Project",
            "param_threads": 1,
            "param_full": False,
            "param_mode": "fast",
            "param_seqtype": "",
            "param_tag": "",
        }
        result = extract_params_from_cleaned_data(cleaned, _ui_schema())
        self.assertEqual(result, {})

    def test_strips_prefix_and_keeps_overrides(self):
        cleaned = {
            "param_threads": 4,
            "param_full": True,
            "param_mode": "thorough",
            "param_seqtype": "Protein",
            "param_tag": "x",
        }
        result = extract_params_from_cleaned_data(cleaned, _ui_schema())
        self.assertEqual(result, {
            "threads": 4,
            "full": True,
            "mode": "thorough",
            "seqtype": "Protein",
            "tag": "x",
        })

    def test_choice_default_compared_as_string_not_tuple(self):
        cleaned = {"param_mode": "fast"}
        result = extract_params_from_cleaned_data(cleaned, _ui_schema())
        self.assertNotIn("mode", result)

    def test_ui_hidden_field_never_extracted(self):
        cleaned = {"param_outfmt": "fasta"}
        result = extract_params_from_cleaned_data(cleaned, _ui_schema())
        self.assertEqual(result, {})

    def test_empty_string_treated_as_blank(self):
        cleaned = {"param_seqtype": "", "param_tag": ""}
        result = extract_params_from_cleaned_data(cleaned, _ui_schema())
        self.assertEqual(result, {})

    def test_none_treated_as_blank(self):
        cleaned = {"param_threads": None}
        result = extract_params_from_cleaned_data(cleaned, _ui_schema())
        self.assertEqual(result, {})


class RadioGroupBuilderTests(TestCase):

    def test_default_radio_emits_no_flag(self):
        b = CommandBuilder(_radio_schema())
        cmd = b.build()
        for flag in ("--cut_ga", "--cut_nc"):
            self.assertNotIn(flag, cmd)

    def test_radio_picks_option_flag(self):
        b = CommandBuilder(_radio_schema()).set("cutoff_mode", "ga")
        cmd = b.build()
        self.assertIn("--cut_ga", cmd)
        self.assertNotIn("--cut_nc", cmd)

    def test_radio_with_null_flag_option_emits_nothing(self):
        b = CommandBuilder(_radio_schema()).set("cutoff_mode", "evalue")
        cmd = b.build()
        self.assertEqual(cmd, ["demo"])

    def test_invalid_radio_value_is_validation_error(self):
        b = CommandBuilder(_radio_schema()).set("cutoff_mode", "nope")
        errors = b.validate()
        self.assertTrue(any("cutoff_mode" in e for e in errors))
        with self.assertRaises(ValueError):
            b.build()


class RadioGroupFormFieldsTests(TestCase):

    def test_radio_group_becomes_choicefield_with_radio_widget(self):
        fields = build_form_fields_from_schema(_radio_schema())
        f = fields["param_cutoff_mode"]
        self.assertIsInstance(f, forms.ChoiceField)
        self.assertIsInstance(f.widget, forms.RadioSelect)
        self.assertEqual([c[0] for c in f.choices], ["evalue", "ga", "nc"])
        self.assertEqual(f.initial, "evalue")

    def test_depends_on_attrs_added_to_widget(self):
        fields = build_form_fields_from_schema(_radio_schema())
        attrs = fields["param_e_value"].widget.attrs
        self.assertEqual(attrs.get("data-depends-on-field"), "param_cutoff_mode")
        import json as _json
        self.assertEqual(_json.loads(attrs["data-depends-on-values"]), ["evalue"])


class DependsOnExtractTests(TestCase):

    def test_extract_skips_dependent_when_controller_mismatch(self):
        cleaned = {"param_cutoff_mode": "ga", "param_e_value": 0.001}
        result = extract_params_from_cleaned_data(cleaned, _radio_schema())
        self.assertEqual(result, {"cutoff_mode": "ga"})

    def test_extract_keeps_dependent_when_controller_matches(self):
        cleaned = {"param_cutoff_mode": "evalue", "param_e_value": 0.001}
        result = extract_params_from_cleaned_data(cleaned, _radio_schema())
        self.assertEqual(result, {"e_value": 0.001})

    def test_extract_skips_default_radio_value(self):
        cleaned = {"param_cutoff_mode": "evalue"}
        result = extract_params_from_cleaned_data(cleaned, _radio_schema())
        self.assertNotIn("cutoff_mode", result)


class IgnoreUnknownTests(TestCase):

    def test_set_many_strict_raises_on_unknown(self):
        b = CommandBuilder(_schema())
        with self.assertRaises(ValueError):
            b.set_many({"threads": 4, "not_a_param": 1})

    def test_set_many_ignore_unknown_silently_drops_legacy_keys(self):
        b = CommandBuilder(_schema())
        b.set_many({"threads": 4, "not_a_param": 1}, ignore_unknown=True)
        cmd = b.build()
        self.assertIn("--threads", cmd)


class StrictDependsOnTests(TestCase):

    def test_strict_rejects_unsatisfied_depends_on(self):
        b = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        b.set("architecture", "hand")
        b.set("symfrac", 0.7)
        with self.assertRaises(ValueError) as cm:
            b.build(positional_args=["out.hmm", "in.sto"], strict=True)
        self.assertIn("depends_on", str(cm.exception))

    def test_non_strict_default_keeps_old_lenient_behavior(self):
        b = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        b.set("architecture", "hand")
        b.set("symfrac", 0.7)
        cmd = b.build(positional_args=["out.hmm", "in.sto"])
        self.assertIn("--symfrac", cmd)

    def test_strict_passes_when_depends_on_is_satisfied(self):
        b = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        b.set("architecture", "fast")
        b.set("symfrac", 0.7)
        cmd = b.build(positional_args=["out.hmm", "in.sto"], strict=True)
        self.assertIn("--symfrac", cmd)

    def test_strict_uses_default_controller_value_when_unset(self):
        b = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        b.set("singlemx_mode", "on")
        b.set("mxfile", "/tmp/m.txt")
        with self.assertRaises(ValueError):
            b.build(positional_args=["o", "i"], strict=True)


class FileTypeBuilderTests(TestCase):

    def test_file_param_emits_flag_and_path(self):
        b = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        b.set("singlemx_mode", "on")
        b.set("mxfile", "/tmp/my_matrix.txt")
        cmd = b.build(positional_args=["o", "i"])
        idx = cmd.index("--mxfile")
        self.assertEqual(cmd[idx + 1], "/tmp/my_matrix.txt")

    def test_file_param_validation_rejects_non_string(self):
        b = CommandBuilder.from_file(SCHEMAS_DIR / "hmmbuild.json")
        b.set("mxfile", 123)
        errors = b.validate()
        self.assertTrue(any("mxfile" in e for e in errors))

    def test_file_field_built_as_filefield(self):
        schema = load_schema("hmmbuild")
        fields = build_form_fields_from_schema(schema)
        self.assertIsInstance(fields["param_mxfile"], forms.FileField)

    def test_extract_params_skips_file_fields(self):
        schema = load_schema("hmmbuild")

        class FakeUpload:
            name = "matrix.txt"

        cleaned = {
            "param_singlemx_mode": "on",
            "param_mxfile": FakeUpload(),
        }
        result = extract_params_from_cleaned_data(cleaned, schema)
        self.assertNotIn("mxfile", result)
