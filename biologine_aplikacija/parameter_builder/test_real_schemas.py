from django.test import TestCase

from biologine_aplikacija.parameter_builder import CommandBuilder, load_schema
from biologine_aplikacija.parameter_builder.form_helpers import (
    extract_params_from_cleaned_data,
)

REAL_SCHEMAS = ['hmmbuild', 'hmmsearch', 'hmmemit',
                'clustalo', 'mafft', 'muscle', 'kalign']


def _alternate_value(param):
    ptype = param.get('type')
    default = param.get('default')

    if ptype == 'boolean':
        return not bool(default)

    if ptype == 'radio_group':
        options = param.get('options') or []
        for opt in options:
            if isinstance(opt, dict) and opt.get('value') != default:
                return opt['value']
        return None

    if ptype == 'select':
        options = param.get('options') or []
        flat = [(o['value'] if isinstance(o, dict) else o) for o in options]
        for v in flat:
            if v != default:
                return v
        return None

    if ptype in ('number', 'float'):
        lo = param.get('min', 0)
        hi = param.get('max')
        candidate = (default + 1) if default is not None else (lo + 1 if lo is not None else 1)
        if hi is not None and candidate > hi:
            candidate = hi
        if lo is not None and candidate < lo:
            candidate = lo
        if candidate == default:
            candidate = candidate + (1 if (hi is None or candidate + 1 <= hi) else -1)
        return candidate if ptype == 'number' else float(candidate)

    if ptype == 'string':
        return 'TEST_VALUE'

    if ptype == 'file':
        return None

    return None


class AllSchemasLoadTests(TestCase):

    def test_all_schemas_load(self):
        for sid in REAL_SCHEMAS:
            with self.subTest(schema=sid):
                schema = load_schema(sid)
                self.assertIn('parameters', schema, f'{sid} missing parameters')
                self.assertIn('executable', schema, f'{sid} missing executable')

    def test_every_parameter_has_required_keys(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            for param in schema.get('parameters', []):
                with self.subTest(schema=sid, param=param.get('name')):
                    self.assertIn('name', param)
                    self.assertIn('type', param)
                    if param['type'] not in ('radio_group',):
                        if not param.get('ui_hidden'):
                            self.assertIn('flag', param,
                                          f'{sid}.{param["name"]} missing flag')


class DependsOnReferencesTests(TestCase):

    def test_depends_on_targets_exist(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            names = {p['name'] for p in schema.get('parameters', [])}
            for param in schema.get('parameters', []):
                dep = param.get('depends_on')
                if not dep:
                    continue
                with self.subTest(schema=sid, param=param['name']):
                    self.assertIn(dep['field'], names,
                                  f'{sid}.{param["name"]} depends on '
                                  f'unknown field {dep["field"]!r}')

    def test_depends_on_values_match_controller_options(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            by_name = {p['name']: p for p in schema.get('parameters', [])}
            for param in schema.get('parameters', []):
                dep = param.get('depends_on')
                if not dep:
                    continue
                controller = by_name.get(dep['field'])
                if not controller:
                    continue
                allowed = [dep['value']] if 'value' in dep else dep.get('values', [])
                if controller['type'] in ('radio_group',):
                    valid = {o['value'] for o in controller.get('options', [])
                             if isinstance(o, dict)}
                elif controller['type'] in ('select',):
                    valid = set()
                    for o in controller.get('options', []):
                        valid.add(o['value'] if isinstance(o, dict) else o)
                elif controller['type'] == 'boolean':
                    valid = {'true', 'false', True, False}
                else:
                    continue
                for v in allowed:
                    with self.subTest(schema=sid, param=param['name'], depvalue=v):
                        self.assertIn(v, valid,
                                      f'{sid}.{param["name"]} depends_on value '
                                      f'{v!r} not in controller {dep["field"]} '
                                      f'choices {valid}')


class CommandBuildPerParamTests(TestCase):

    def test_every_simple_param_produces_its_flag(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            for param in schema.get('parameters', []):
                if param.get('ui_hidden'):
                    continue
                if param['type'] in ('file', 'radio_group'):
                    continue
                alt = _alternate_value(param)
                if alt is None:
                    continue
                with self.subTest(schema=sid, param=param['name'], value=alt):
                    b = CommandBuilder(schema).set(param['name'], alt)
                    cmd = b.build()
                    flag = param.get('flag')
                    if flag:
                        if param['type'] == 'boolean':
                            if alt is True:
                                self.assertIn(flag, cmd,
                                              f'{sid}.{param["name"]} bool=True'
                                              f' should add {flag}')
                            else:
                                self.assertNotIn(flag, cmd,
                                                 f'{sid}.{param["name"]} bool='
                                                 f'False should not add {flag}')
                        else:
                            self.assertTrue(
                                any(part.startswith(flag) for part in cmd),
                                f'{sid}.{param["name"]} should add {flag} '
                                f'(cmd={cmd})')

    def test_radio_group_options_produce_their_own_flags(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            for param in schema.get('parameters', []):
                if param['type'] != 'radio_group' or param.get('ui_hidden'):
                    continue
                default = param.get('default')
                for opt in param.get('options', []):
                    if not isinstance(opt, dict):
                        continue
                    if opt.get('value') == default:
                        continue
                    flag = opt.get('flag')
                    if not flag:
                        continue
                    with self.subTest(schema=sid, param=param['name'],
                                       value=opt['value']):
                        b = CommandBuilder(schema).set(param['name'], opt['value'])
                        cmd = b.build()
                        self.assertIn(flag, cmd,
                                      f'{sid}.{param["name"]}={opt["value"]!r}'
                                      f' should add {flag} (cmd={cmd})')


class DependsOnFilteringTests(TestCase):

    def test_unsatisfied_depends_on_drops_value(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            by_name = {p['name']: p for p in schema.get('parameters', [])}
            for param in schema.get('parameters', []):
                dep = param.get('depends_on')
                if not dep:
                    continue
                controller = by_name.get(dep['field'])
                if not controller:
                    continue
                allowed = [dep['value']] if 'value' in dep else dep.get('values', [])
                if controller['type'] == 'radio_group':
                    options = [o['value'] for o in controller.get('options', [])
                               if isinstance(o, dict)]
                    excluded = [v for v in options if v not in allowed]
                    if not excluded:
                        continue
                    bad_value = excluded[0]
                elif controller['type'] == 'boolean':
                    bad_value = not bool(allowed[0] in ('true', True))
                else:
                    continue

                alt = _alternate_value(param) or 'X'
                cleaned = {
                    f'param_{controller["name"]}': bad_value,
                    f'param_{param["name"]}': alt,
                }
                result = extract_params_from_cleaned_data(cleaned, schema)
                with self.subTest(schema=sid, controller=controller['name'],
                                   bad=bad_value, dependent=param['name']):
                    self.assertNotIn(
                        param['name'], result,
                        f'{sid}.{param["name"]} leaked when '
                        f'{controller["name"]}={bad_value!r}')

    def test_known_hmmer_cli_conflicts_are_unreachable(self):
        cases = [
            ('hmmbuild', 'effective_weighting', 'eset', 'eclust'),
            ('hmmbuild', 'effective_weighting', 'eset', 'enone'),
            ('hmmemit', 'sample_from_profile', 'alignment', 'profile'),
            ('hmmsearch', 'cutoff_mode', 'score_threshold', 'evalue'),
            ('hmmsearch', 'cutoff_mode', 'domT', 'evalue'),
            ('hmmsearch', 'cutoff_mode', 'incT', 'evalue'),
            ('hmmsearch', 'cutoff_mode', 'incdomT', 'evalue'),
            ('hmmsearch', 'cutoff_mode', 'e_value', 'score'),
            ('hmmsearch', 'cutoff_mode', 'domE', 'score'),
            ('hmmsearch', 'cutoff_mode', 'incE', 'score'),
            ('hmmsearch', 'cutoff_mode', 'incdomE', 'score'),
        ]
        for sid, controller, dependent, blocking_value in cases:
            schema = load_schema(sid)
            cleaned = {
                f'param_{controller}': blocking_value,
                f'param_{dependent}': _alternate_value(
                    next(p for p in schema['parameters'] if p['name'] == dependent)
                ) or 'X',
            }
            result = extract_params_from_cleaned_data(cleaned, schema)
            with self.subTest(schema=sid, dependent=dependent,
                              controller_value=blocking_value):
                self.assertNotIn(
                    dependent, result,
                    f'{sid}.{dependent} leaked when {controller}={blocking_value!r}',
                )

    def test_satisfied_depends_on_passes_value(self):
        for sid in REAL_SCHEMAS:
            schema = load_schema(sid)
            by_name = {p['name']: p for p in schema.get('parameters', [])}
            for param in schema.get('parameters', []):
                dep = param.get('depends_on')
                if not dep:
                    continue
                if param.get('type') == 'file':
                    continue
                alt = _alternate_value(param)
                if alt in (None, '', param.get('default')):
                    continue

                cleaned = {f'param_{param["name"]}': alt}
                cur = param
                ok = True
                while cur.get('depends_on'):
                    cdep = cur['depends_on']
                    cur_allowed = ([cdep['value']] if 'value' in cdep
                                    else cdep.get('values', []))
                    if not cur_allowed:
                        ok = False
                        break
                    parent = by_name.get(cdep['field'])
                    if not parent:
                        ok = False
                        break
                    required = cur_allowed[0]
                    if parent['type'] == 'boolean':
                        required = (required in ('true', True))
                    cleaned[f'param_{parent["name"]}'] = required
                    cur = parent
                if not ok:
                    continue

                result = extract_params_from_cleaned_data(cleaned, schema)
                with self.subTest(schema=sid, dependent=param['name']):
                    self.assertIn(
                        param['name'], result,
                        f'{sid}.{param["name"]} dropped even though chain '
                        f'is satisfied (cleaned={cleaned})')
