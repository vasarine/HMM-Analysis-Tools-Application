"""
Builds command parameters from a tool's JSON schema.
Checks the values and puts them together into the final command.
"""

import json


class CommandBuilder:
    def __init__(self, schema):
        self.schema = schema
        self._params = {p['name']: p for p in schema.get('parameters', [])}
        self._values = {}

    @classmethod
    def from_file(cls, schema_path):
        with open(schema_path) as f:
            return cls(json.load(f))

    def set(self, name, value):
        if name not in self._params:
            raise ValueError(f"Unknown parameter: {name}")
        self._values[name] = value
        return self

    def set_many(self, values, ignore_unknown=False):
        for name, value in values.items():
            if ignore_unknown and name not in self._params:
                continue
            self.set(name, value)
        return self

    def _controller_value(self, controller_name):
        if controller_name in self._values:
            return self._values[controller_name]
        ctrl_param = self._params.get(controller_name)
        return ctrl_param.get('default') if ctrl_param else None

    def _depends_on_satisfied(self, name, _seen=None):
        param = self._params.get(name)
        if not param:
            return True
        depends_on = param.get('depends_on')
        if not depends_on:
            return True
        controller_name = depends_on['field']
        controller_value = self._controller_value(controller_name)
        if isinstance(controller_value, bool):
            controller_value = "true" if controller_value else "false"
        if 'value' in depends_on:
            allowed = [depends_on['value']]
        else:
            allowed = list(depends_on.get('values', []))
        if controller_value not in allowed:
            return False
        if _seen is None:
            _seen = set()
        if controller_name in _seen:
            return True
        _seen.add(controller_name)
        if controller_name in self._params:
            return self._depends_on_satisfied(controller_name, _seen)
        return True

    def validate(self, strict=False):
        errors = []
        for name, value in self._values.items():
            param = self._params[name]
            ptype = param['type']
            if value is None:
                continue
            if ptype in ('number', 'float'):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"{name}: expected number, got {value!r}")
                    continue
                if 'min' in param and value < param['min']:
                    errors.append(f"{name}: value {value} below min {param['min']}")
                if 'max' in param and value > param['max']:
                    errors.append(f"{name}: value {value} above max {param['max']}")
            elif ptype == 'boolean':
                if not isinstance(value, bool):
                    errors.append(f"{name}: expected boolean, got {value!r}")
            elif ptype == 'select':
                options = param.get('options', [])
                if value not in options:
                    errors.append(f"{name}: {value!r} not in options {options}")
            elif ptype == 'radio_group':
                allowed = [o['value'] for o in param.get('options', [])]
                if value not in allowed:
                    errors.append(f"{name}: {value!r} not in options {allowed}")
            elif ptype == 'string':
                if not isinstance(value, str):
                    errors.append(f"{name}: expected string, got {value!r}")
            elif ptype == 'file':
                if not isinstance(value, str):
                    errors.append(f"{name}: expected file path string, got {value!r}")
            if strict and not self._depends_on_satisfied(name):
                depends_on = param['depends_on']
                ctrl = depends_on['field']
                expected = depends_on.get('value', depends_on.get('values'))
                errors.append(
                    f"{name}: depends_on {ctrl}={expected!r} is not satisfied "
                    f"(controller is {self._controller_value(ctrl)!r})"
                )
        return errors

    def build(self, positional_args=None, io_flags=None, strict=False):
        errors = self.validate(strict=strict)
        if errors:
            raise ValueError("; ".join(errors))

        command = [self.schema['executable']]
        command.extend(self.schema.get('fixed_args', []))

        io_flag_keys = set(io_flags.keys()) if io_flags else set()

        if io_flags:
            for flag, value in io_flags.items():
                command.append(flag)
                command.append(str(value))

        for param in self.schema.get('parameters', []):
            name = param['name']
            default = param.get('default')
            value = self._values.get(name, default)
            if value is None or value == default:
                continue

            ptype = param['type']

            if ptype == 'radio_group':
                for opt in param.get('options', []):
                    if opt['value'] == value:
                        opt_flag = opt.get('flag')
                        if opt_flag:
                            command.append(opt_flag)
                        break
                continue

            flag = param['flag']
            if flag in io_flag_keys:
                continue

            style = param.get('flag_style', 'separate')

            if ptype == 'boolean':
                if value is True:
                    command.append(flag)
            else:
                if style == 'equals':
                    command.append(f"{flag}={value}")
                else:
                    command.append(flag)
                    command.append(str(value))

        if positional_args:
            command.extend(positional_args)

        return command

    def preview(self, **kwargs):
        return ' '.join(self.build(**kwargs))
