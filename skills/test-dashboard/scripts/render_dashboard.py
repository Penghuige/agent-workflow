#!/usr/bin/env python3
"""Render a validated, offline test dashboard using only the standard library."""

import argparse
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit


PLACEHOLDER = '__TEST_DASHBOARD_DATA__'
STATUSES = ('passed', 'failed', 'skipped', 'not_run')
STATES = ('completed', 'running', 'blocked', 'collection_error', 'not_integrated')
KINDS = ('screenshot', 'video', 'trace', 'log', 'report')


class ContractError(ValueError):
    """A safe diagnostic containing a trusted field location and explanation."""


def reject(location, reason):
    raise ContractError('{}: {}'.format(location, reason))


def require_object(value, location):
    if not isinstance(value, dict):
        reject(location, 'must be an object')


def require_list(value, location):
    if not isinstance(value, list):
        reject(location, 'must be an array')


def require_text(value, location):
    if not isinstance(value, str):
        reject(location, 'must be a string')


def require_identifier(value, location):
    require_text(value, location)
    if not value.strip():
        reject(location, 'must be a nonblank identifier')


def required(owner, key, location):
    field = '{}.{}'.format(location, key)
    if key not in owner:
        reject(field, 'required field is missing')
    return owner[key], field


def text_fields(owner, location, required_names=(), optional_names=()):
    for name in required_names:
        value, field = required(owner, name, location)
        require_text(value, field)
    for name in optional_names:
        if name in owner:
            require_text(owner[name], '{}.{}'.format(location, name))


def enum_field(owner, key, choices, location, required_field=True):
    if key not in owner and not required_field:
        return
    value, field = required(owner, key, location)
    require_text(value, field)
    if value not in choices:
        reject(field, 'unsupported value')


def duration(owner, location):
    if 'duration_ms' in owner:
        value = owner['duration_ms']
        if (type(value) not in (int, float) or value < 0
                or (isinstance(value, float) and not math.isfinite(value))):
            reject(location + '.duration_ms', 'must be a finite nonnegative number')


def attachment_location(value, location, output_dir):
    """Validate both the URL spelling and repeatedly decoded path before linking."""
    require_text(value, location)
    decoded = value
    while True:
        if (not decoded or decoded != decoded.strip() or '\\' in decoded
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in decoded)):
            reject(location, 'unsafe attachment path')
        try:
            next_value = unquote(decoded, errors='strict')
        except UnicodeError:
            reject(location, 'invalid encoded attachment path')
        if next_value == decoded:
            break
        decoded = next_value
    try:
        original = urlsplit(value)
        parsed = urlsplit(decoded)
        if '..' in parsed.path.split('/'):
            reject(location, 'attachment path traversal is forbidden')
        if original.scheme:
            if (original.scheme.lower() not in ('http', 'https')
                    or parsed.scheme.lower() != original.scheme.lower()
                    or not original.hostname or not parsed.hostname
                    or '@' in original.netloc or '@' in parsed.netloc
                    or original.username is not None or parsed.username is not None
                    or '%' in original.netloc):
                reject(location, 'only HTTP(S) URLs without credentials are allowed')
            # Accessing port also rejects malformed ports without echoing the URL.
            original.port
            parsed.port
            return 'unverified', value
        if (parsed.scheme or original.netloc or parsed.netloc or decoded.startswith('/')
                or parsed.query or parsed.fragment):
            reject(location, 'local attachment must be a relative file path')
        target = (output_dir / decoded).resolve()
        try:
            target.relative_to(output_dir)
        except ValueError:
            reject(location, 'attachment escapes the output directory')
        if target.is_file():
            return 'present', quote(decoded, safe='/')
        return 'missing', ''
    except (ValueError, OSError, RuntimeError) as error:
        if isinstance(error, ContractError):
            raise
        reject(location, 'invalid attachment location')


def attachments(owner, location, output_dir):
    if 'attachments' not in owner:
        return
    values = owner['attachments']
    require_list(values, location + '.attachments')
    for index, value in enumerate(values):
        field = '{}.attachments[{}]'.format(location, index)
        require_object(value, field)
        enum_field(value, 'kind', KINDS, field)
        text_fields(value, field, required_names=('label', 'path'))
        value['availability'], value['href'] = attachment_location(value['path'], field + '.path', output_dir)


def validate_case(case, location, output_dir):
    require_object(case, location)
    text_fields(case, location, required_names=('id', 'name', 'suite'),
                optional_names=('expected', 'actual', 'error', 'command'))
    require_identifier(case['id'], location + '.id')
    enum_field(case, 'status', STATUSES, location)
    duration(case, location)
    attachments(case, location, output_dir)
    if 'steps' in case:
        require_list(case['steps'], location + '.steps')
        for index, step in enumerate(case['steps']):
            field = '{}.steps[{}]'.format(location, index)
            require_object(step, field)
            text_fields(step, field, required_names=('name',),
                        optional_names=('expected', 'actual', 'error', 'command'))
            enum_field(step, 'status', STATUSES, field)
            duration(step, field)
            attachments(step, field, output_dir)
    if 'findings' in case:
        require_list(case['findings'], location + '.findings')
        for index, finding in enumerate(case['findings']):
            field = '{}.findings[{}]'.format(location, index)
            require_object(finding, field)
            text_fields(finding, field, required_names=('title', 'basis'),
                        optional_names=('expected', 'actual', 'severity'))
            enum_field(finding, 'status', ('candidate', 'confirmed'), field)
            attachments(finding, field, output_dir)


def validate_document(document, output_dir):
    require_object(document, '$')
    version, field = required(document, 'schema_version', '$')
    if type(version) is not int or version != 1:
        reject(field, 'must be integer 1')
    project, field = required(document, 'project', '$')
    require_object(project, field)
    text_fields(project, field, required_names=('name',), optional_names=('revision',))
    if 'dirty' in project and type(project['dirty']) is not bool:
        reject(field + '.dirty', 'must be a boolean')
    if 'gaps' in document:
        require_list(document['gaps'], '$.gaps')
        for index, gap in enumerate(document['gaps']):
            field = '$.gaps[{}]'.format(index)
            require_object(gap, field)
            text_fields(gap, field, required_names=('title', 'reason', 'suggestion'),
                        optional_names=('basis',))
            enum_field(gap, 'state', ('suggested', 'accepted', 'not_applicable'), field,
                       required_field=False)
            enum_field(gap, 'confidence', ('evidence', 'hypothesis'), field,
                       required_field=False)
    runs, field = required(document, 'runs', '$')
    require_list(runs, field)
    run_ids = set()
    for index, run in enumerate(runs):
        field = '$.runs[{}]'.format(index)
        require_object(run, field)
        text_fields(run, field, required_names=('id', 'label'),
                    optional_names=('revision', 'environment', 'started_at', 'source', 'charter', 'seed'))
        require_identifier(run['id'], field + '.id')
        if run['id'] in run_ids:
            reject(field + '.id', 'must be unique within the document')
        run_ids.add(run['id'])
        enum_field(run, 'state', STATES, field)
        enum_field(run, 'track', ('planned', 'exploratory'), field, required_field=False)
        inventory, inventory_field = required(run, 'inventory_known', field)
        if type(inventory) is not bool:
            reject(inventory_field, 'must be a boolean')
        if run.get('track', 'planned') == 'exploratory' and inventory:
            reject(inventory_field, 'must be false for exploratory runs')
        if 'notes' in run:
            require_list(run['notes'], field + '.notes')
            for note_index, note in enumerate(run['notes']):
                require_text(note, '{}.notes[{}]'.format(field, note_index))
        cases, case_field = required(run, 'cases', field)
        require_list(cases, case_field)
        case_ids = set()
        for case_index, case in enumerate(cases):
            case_field = '{}.cases[{}]'.format(field, case_index)
            validate_case(case, case_field, output_dir)
            if case['id'] in case_ids:
                reject(case_field + '.id', 'must be unique within this run')
            case_ids.add(case['id'])
    if 'methods' in document:
        require_list(document['methods'], '$.methods')
        method_ids = set()
        for index, method in enumerate(document['methods']):
            field = '$.methods[{}]'.format(index)
            require_object(method, field)
            text_fields(method, field, required_names=('id', 'name', 'reason'),
                        optional_names=('category', 'evidence'))
            require_identifier(method['id'], field + '.id')
            if method['id'] in method_ids:
                reject(field + '.id', 'must be unique within methods')
            method_ids.add(method['id'])
            enum_field(method, 'state', ('present', 'suggested', 'not_applicable', 'unknown'),
                       field, required_field=False)
            if 'run_ids' in method:
                require_list(method['run_ids'], field + '.run_ids')
                referenced_run_ids = set()
                for run_index, run_id in enumerate(method['run_ids']):
                    run_field = '{}.run_ids[{}]'.format(field, run_index)
                    require_text(run_id, run_field)
                    if run_id not in run_ids:
                        reject(run_field, 'must reference an existing run')
                    if run_id in referenced_run_ids:
                        reject(run_field, 'must be unique within method run_ids')
                    referenced_run_ids.add(run_id)


def auto_layout(document):
    """Usability defaults, not universal thresholds or an assertion of coverage."""
    runs = document['runs']
    cases = [case for run in runs for case in run['cases']]
    if (len(runs) > 1 or len({run.get('environment', '') for run in runs}) > 1
            or len({run.get('track', 'planned') for run in runs}) > 1
            or any(run['state'] != 'completed' for run in runs)
            or len(cases) > 8 or len({case['suite'] for case in cases}) > 1):
        return 'grouped'
    if any(case.get('steps') for case in cases):
        return 'workflow'
    return 'compact'


def reject_constant(_value):
    reject('$', 'non-finite JSON numbers are forbidden')


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        reject_constant(value)
    return number


def render_html(document, output_dir, layout):
    """Return report HTML without writing files or modifying the caller's data.

    Local attachment paths are checked against the intended report directory,
    exactly as for an offline HTML snapshot. The directory need not exist.
    """
    if not isinstance(layout, str) or layout not in ('auto', 'compact', 'grouped', 'workflow'):
        reject('layout', 'unsupported value')
    document = copy.deepcopy(document)
    output_dir = output_dir.resolve()
    validate_document(document, output_dir)
    document['layout'] = auto_layout(document) if layout == 'auto' else layout
    try:
        template = (Path(__file__).resolve().parent.parent / 'assets/dashboard.html').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        reject('template', 'cannot read dashboard template as UTF-8')
    if template.count(PLACEHOLDER) != 1:
        reject('template', 'must contain exactly one dashboard data placeholder')
    encoded = json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    for character, escaped in (('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026')):
        encoded = encoded.replace(character, escaped)
    return template.replace(PLACEHOLDER, encoded)


def render(input_path, output_path, layout):
    try:
        document = json.loads(input_path.read_text(encoding='utf-8'),
                              parse_constant=reject_constant, parse_float=finite_float)
    except ContractError:
        raise
    except json.JSONDecodeError as error:
        reject('$', 'invalid JSON at line {}, column {}'.format(error.lineno, error.colno))
    except ValueError:
        reject('$', 'invalid JSON numeric value')
    except (OSError, UnicodeError):
        reject('$', 'cannot read input JSON as UTF-8')
    html = render_html(document, output_path.parent, layout)
    temporary = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output_path.parent,
                                         prefix='.test-dashboard-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(html)
        os.replace(temporary, output_path)
    except OSError:
        reject('output', 'cannot write dashboard atomically')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--layout', choices=('auto', 'compact', 'grouped', 'workflow'), default='auto')
    args = parser.parse_args()
    try:
        render(args.input, args.output, args.layout)
    except ContractError as error:
        print('error: {}'.format(error), file=sys.stderr)
        return 1
    except (RecursionError, OverflowError):
        print('error: $: JSON structure or number exceeds supported limits', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
