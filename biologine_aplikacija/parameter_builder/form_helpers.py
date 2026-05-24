"""
Builds Django form fields from a tool's JSON schema, so the parameter
forms don't have to be written by hand.
"""

import json

from django import forms


def _build_field(param):
    ptype = param['type']
    label = param.get('label', param['name'])
    help_text = param.get('help_text', '')
    default = param.get('default')

    if ptype == 'boolean':
        return forms.BooleanField(
            label=label, required=False, initial=bool(default), help_text=help_text,
        )
    if ptype == 'number':
        kwargs = {
            'label': label, 'required': False, 'initial': default, 'help_text': help_text,
        }
        if 'min' in param:
            kwargs['min_value'] = param['min']
        if 'max' in param:
            kwargs['max_value'] = param['max']
        return forms.IntegerField(**kwargs)
    if ptype == 'float':
        kwargs = {
            'label': label, 'required': False, 'initial': default, 'help_text': help_text,
        }
        if 'min' in param:
            kwargs['min_value'] = param['min']
        if 'max' in param:
            kwargs['max_value'] = param['max']
        return forms.FloatField(**kwargs)
    if ptype == 'select':
        options = param.get('options', [])
        choices = [(o, o) for o in options]
        if default is None:
            choices = [("", "-")] + choices
            initial = ""
        else:
            initial = default
        return forms.ChoiceField(
            label=label, required=False, choices=choices, initial=initial,
            help_text=help_text,
        )
    if ptype == 'radio_group':
        options = param.get('options', [])
        choices = [(o['value'], o.get('label', o['value'])) for o in options]
        return forms.ChoiceField(
            label=label, required=True, choices=choices, initial=default,
            widget=forms.RadioSelect, help_text=help_text,
        )
    if ptype == 'string':
        return forms.CharField(
            label=label, required=False, initial=default or '', help_text=help_text,
        )
    if ptype == 'file':
        return forms.FileField(
            label=label, required=False, help_text=help_text,
        )
    raise ValueError(f"Unsupported parameter type: {ptype}")


def _apply_depends_on(field, depends_on, prefix):
    if not depends_on:
        return
    controller_name = f"{prefix}{depends_on['field']}"
    if 'value' in depends_on:
        values = [depends_on['value']]
    else:
        values = list(depends_on.get('values', []))
    field.widget.attrs['data-depends-on-field'] = controller_name
    field.widget.attrs['data-depends-on-values'] = json.dumps(values)


def build_form_fields_from_schema(schema, prefix="param_"):
    fields = {}
    for param in schema.get('parameters', []):
        if param.get('ui_hidden'):
            continue
        field = _build_field(param)
        _apply_depends_on(field, param.get('depends_on'), prefix)
        if param.get('section'):
            field.widget.attrs['data_section'] = param['section']
        fields[f"{prefix}{param['name']}"] = field
    return fields


def _depends_on_satisfied(param, cleaned_data, prefix, schema=None, _seen=None):
    depends_on = param.get('depends_on')
    if not depends_on:
        return True
    controller_name = f"{prefix}{depends_on['field']}"
    controller_value = cleaned_data.get(controller_name)
    if isinstance(controller_value, bool):
        controller_value = "true" if controller_value else "false"
    if 'value' in depends_on:
        allowed = [depends_on['value']]
    else:
        allowed = list(depends_on.get('values', []))
    if controller_value not in allowed:
        return False
    if schema is not None:
        if _seen is None:
            _seen = set()
        if depends_on['field'] in _seen:
            return True
        _seen.add(depends_on['field'])
        for other in schema.get('parameters', []):
            if other.get('name') == depends_on['field']:
                if not _depends_on_satisfied(other, cleaned_data, prefix, schema, _seen):
                    return False
                break
    return True


def extract_params_from_cleaned_data(cleaned_data, schema, prefix="param_"):
    result = {}
    for param in schema.get('parameters', []):
        if param.get('ui_hidden'):
            continue
        if param.get('type') == 'file':
            continue
        name = param['name']
        field_name = f"{prefix}{name}"
        if field_name not in cleaned_data:
            continue
        if not _depends_on_satisfied(param, cleaned_data, prefix, schema):
            continue
        value = cleaned_data[field_name]
        if value is None or value == "":
            continue
        if value == param.get('default'):
            continue
        result[name] = value
    return result


def extract_file_uploads(cleaned_data, schema, save_dir, prefix="param_", filename_prefix=""):
    import os
    result = {}
    for param in schema.get('parameters', []):
        if param.get('ui_hidden') or param.get('type') != 'file':
            continue
        if not _depends_on_satisfied(param, cleaned_data, prefix, schema):
            continue
        name = param['name']
        field_name = f"{prefix}{name}"
        uploaded = cleaned_data.get(field_name)
        if not uploaded:
            continue
        safe_name = os.path.basename(getattr(uploaded, 'name', name))
        save_name = f"{filename_prefix}{safe_name}" if filename_prefix else safe_name
        path = os.path.join(save_dir, save_name)
        with open(path, 'wb+') as dest:
            for chunk in uploaded.chunks():
                dest.write(chunk)
        result[name] = path
    return result
