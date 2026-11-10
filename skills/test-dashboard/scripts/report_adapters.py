"""Convert supported native reports to dashboard v1 runs without executing tests.

Only explicitly selected report fields are copied. Native configuration, environment
variables, stdio and attachment locations are deliberately not exported.
"""
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


class AdapterError(ValueError):
    """A safe report diagnostic; messages never interpolate native input values."""


def _require(condition, message='Invalid native report structure.'):
    if not condition:
        raise AdapterError(message)


def _string(value, nonempty=False):
    _require(isinstance(value, str) and (not nonempty or bool(value.strip())))
    return value


def _list(value):
    _require(isinstance(value, list))
    return value


def _object(value):
    _require(isinstance(value, dict))
    return value


def _duration(value, xml=False):
    if xml:
        value = float(value) * 1000
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
             'Report duration must be finite and nonnegative.')
    return value


def _integer(value):
    _require(type(value) is int and value >= 0, 'Invalid report counter or attempt index.')
    return value


def _metadata(metadata):
    _object(metadata)
    for key in ('id', 'label', 'source'):
        _string(metadata.get(key), nonempty=True)
    for key in ('environment', 'revision', 'started_at'):
        if key in metadata:
            _string(metadata[key])
    if 'track' in metadata:
        _require(metadata['track'] in ('planned', 'exploratory'), 'Invalid source metadata.')
    for note in _list(metadata.get('notes', [])):
        _string(note)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate JSON object member in native report.')
        result[key] = value
    return result


def _run(metadata, suffix, label=None):
    run = {'id': metadata['id'] + ':' + suffix,
           'label': label or metadata['label'], 'state': 'completed',
           'inventory_known': False, 'cases': [], 'source': metadata['source'],
           'notes': list(metadata.get('notes', []))}
    for key in ('environment', 'track', 'revision', 'started_at'):
        if key in metadata:
            run[key] = metadata[key]
    return run


def _error_text(error):
    if not error:
        _require(error is None or isinstance(error, dict))
        return ''
    _object(error)
    for key in ('message', 'value', 'stack'):
        if key in error:
            _string(error[key])
    return error.get('message') or error.get('value') or error.get('stack') or ''


def _steps(steps, prefix=''):
    result = []
    for step in _list(steps):
        _object(step)
        name = prefix + _string(step['title'])
        item = {'name': name, 'status': 'failed' if step.get('error') else 'passed',
                'duration_ms': _duration(step['duration'])}
        error = _error_text(step.get('error'))
        if error:
            item['error'] = error
        result.append(item)
        result.extend(_steps(step.get('steps', []), name + ' / '))
    return result


def _playwright(report, metadata):
    _object(report)
    groups = {}
    case_index = 0
    outcome_counts = dict.fromkeys(('expected', 'unexpected', 'flaky', 'skipped'), 0)
    has_missing_outcome = False

    def walk(suites, ancestors=()):
        nonlocal case_index, has_missing_outcome
        for suite in _list(suites):
            _object(suite)
            titles = ancestors + (_string(suite['title']),)
            for spec in _list(suite['specs']):
                _object(spec)
                _string(spec['title'], nonempty=True)
                _require(bool(_list(spec['tests'])))
                for test in spec['tests']:
                    _object(test)
                    case_index += 1
                    project = _string(test.get('projectName', ''))
                    project_id = _string(test.get('projectId', project))
                    if 'status' in test:
                        _require(test['status'] in outcome_counts, 'Unknown Playwright outcome.')
                        outcome_counts[test['status']] += 1
                    else:
                        has_missing_outcome = True
                    if 'expectedStatus' in test:
                        _require(test['expectedStatus'] in ('passed', 'failed', 'timedOut', 'skipped', 'interrupted'),
                                 'Unknown Playwright expected status.')
                    native_results = _list(test['results'])
                    if not native_results:
                        # A discovered test without an execution result is not a skip.
                        native_results = [{'retry': 0, 'status': None, 'workerIndex': -1}]
                    seen_attempts = set()
                    for result in native_results:
                        _object(result)
                        retry = _integer(result['retry'])
                        shard = result.get('shardIndex')
                        if shard is not None:
                            _integer(shard)
                        _require((retry, shard) not in seen_attempts, 'Duplicate Playwright attempt.')
                        seen_attempts.add((retry, shard))
                        key = (project_id, project, retry, shard)
                        if key not in groups:
                            suffix = 'project-{}-attempt-{}'.format(len(groups), retry)
                            label = '{} / {} / attempt {}'.format(
                                metadata['label'], project or 'default project', retry)
                            run = _run(metadata, suffix, label)
                            if shard is not None:
                                run['notes'].append('Playwright shardIndex: ' + str(shard))
                            if project:
                                run['environment'] = ' / '.join(filter(None, (
                                    metadata.get('environment'), project)))
                            if not run.get('started_at'):
                                start = result.get('startTime')
                                if not start and 'stats' in report:
                                    start = _object(report['stats']).get('startTime')
                                if start:
                                    run['started_at'] = _string(start)
                            groups[key] = run
                        run = groups[key]
                        native_status = result.get('status')
                        unstarted = native_status is None and result.get('workerIndex') == -1
                        _require(unstarted or native_status in (
                            'passed', 'failed', 'timedOut', 'skipped', 'interrupted'),
                            'Unknown or missing Playwright result status.')
                        status = {'passed': 'passed', 'failed': 'failed',
                                  'timedOut': 'failed', 'skipped': 'skipped',
                                  'interrupted': 'not_run', None: 'not_run'}[native_status]
                        if native_status == 'interrupted' or unstarted:
                            run['state'] = 'blocked'
                        case = {'id': metadata['id'] + ':case-' + str(case_index),
                                'name': spec['title'], 'suite': ' / '.join(titles),
                                'status': status,
                                'actual': 'Playwright status: ' + (native_status or 'not started')}
                        if 'duration' in result:
                            case['duration_ms'] = _duration(result['duration'])
                        else:
                            _require(unstarted, 'Missing Playwright result duration.')
                        if test.get('expectedStatus'):
                            case['expected'] = 'Playwright expectedStatus: ' + test['expectedStatus']
                        errors = [_error_text(error) for error in _list(result.get('errors', []))]
                        first_error = _error_text(result.get('error'))
                        if first_error and first_error not in errors:
                            errors.insert(0, first_error)
                        if any(errors):
                            case['error'] = '\n'.join(filter(None, errors))
                        if 'steps' in result:
                            steps = _steps(result['steps'])
                            if steps:
                                case['steps'] = steps
                        if _list(result.get('attachments', [])):
                            note = 'Native attachments are not integrated; paths and bodies were not exported.'
                            if note not in run['notes']:
                                run['notes'].append(note)
                        run['cases'].append(case)
            walk(suite.get('suites', []), titles)

    walk(report['suites'])
    if 'stats' in report:
        stats = _object(report['stats'])
        if 'duration' in stats:
            _duration(stats['duration'])
        if 'startTime' in stats:
            _string(stats['startTime'])
        counters = [key for key in outcome_counts if key in stats]
        if counters:
            _require(len(counters) == len(outcome_counts) and not has_missing_outcome,
                     'Incomplete Playwright summary.')
            _require(all(_integer(stats[key]) == outcome_counts[key] for key in counters),
                     'Playwright summary does not match report records.')
    runs = list(groups.values())
    global_errors = _list(report.get('errors', []))
    if global_errors:
        run = _run(metadata, 'global-errors', metadata['label'] + ' / global errors')
        run['state'] = 'collection_error'
        for error in global_errors:
            run['notes'].append(_error_text(error) or 'Native reporter recorded a global error.')
        runs.append(run)
    return runs or [_run(metadata, 'empty')]


def _junit(root, metadata):
    _require(root.tag in ('testsuite', 'testsuites'), 'Unsupported JUnit root element.')
    runs = []
    case_index = 0

    def walk(suite, ancestors=()):
        nonlocal case_index
        title = suite.get('name') or 'JUnit'
        titles = ancestors + (title,)
        cases = []
        for node in suite:
            if node.tag == 'testcase':
                _string(node.get('name'), nonempty=True)
                _require(node.get('status') in (None, 'run'), 'Unsupported JUnit testcase status.')
                _require(all(child.tag in ('failure', 'error', 'skipped', 'properties',
                                           'system-out', 'system-err') for child in node),
                         'Unsupported JUnit testcase evidence.')
                case_index += 1
                outcomes = [child for child in node if child.tag in ('failure', 'error', 'skipped')]
                _require(len(outcomes) <= 1, 'Conflicting JUnit testcase outcomes.')
                status = 'passed'
                if any(child.tag in ('failure', 'error') for child in outcomes):
                    status = 'failed'
                elif outcomes:
                    status = 'skipped'
                case = {'id': metadata['id'] + ':case-' + str(case_index),
                        'name': node.attrib['name'],
                        'suite': ' / '.join(titles + tuple(filter(None, (node.get('classname'),)))),
                        'status': status}
                if node.get('time') is not None:
                    case['duration_ms'] = _duration(node.get('time'), xml=True)
                details = ['\n'.join(filter(None, (child.get('message'), child.text)))
                           for child in outcomes]
                if any(details):
                    case['error' if status == 'failed' else 'actual'] = '\n'.join(filter(None, details))
                cases.append(case)
            else:
                _require(node.tag in ('testsuite', 'properties', 'system-out', 'system-err'),
                         'Unsupported JUnit suite element.')
        if cases or not any(child.tag == 'testsuite' for child in suite):
            run = _run(metadata, 'suite-' + str(len(runs)), metadata['label'] + ' / ' + ' / '.join(titles))
            run['cases'] = cases
            if 'started_at' not in run and suite.get('timestamp'):
                run['started_at'] = suite.get('timestamp')
            runs.append(run)
        for child in suite:
            if child.tag == 'testsuite':
                walk(child, titles)

        check_summary(suite)

    def check_summary(suite):
        counts = {'tests': sum(1 for _ in suite.iter('testcase')),
                  'failures': sum(1 for _ in suite.iter('failure')),
                  'errors': sum(1 for _ in suite.iter('error')),
                  'skipped': sum(1 for _ in suite.iter('skipped')), 'disabled': 0}
        for key, count in counts.items():
            value = suite.get(key)
            if value is not None:
                _require(bool(re.fullmatch(r'[0-9]+', value)), 'Invalid JUnit summary counter.')
                _require(int(value) == count, 'JUnit summary does not match report records.')
        if suite.get('time') is not None:
            _duration(suite.get('time'), xml=True)

    if root.tag == 'testsuite':
        walk(root)
    elif root.tag == 'testsuites':
        for child in root:
            _require(child.tag in ('testsuite', 'testcase'), 'Unsupported JUnit container element.')
        direct_cases = root.findall('testcase')
        if direct_cases:
            # Node's reporter can mix standalone cases and suites. Group only the
            # direct cases; container counters still describe the original tree.
            attributes = {key: root.attrib[key] for key in ('name', 'timestamp') if key in root.attrib}
            standalone = ET.Element('testsuite', attributes)
            standalone.extend(direct_cases)
            walk(standalone)
        for suite in root:
            if suite.tag == 'testsuite':
                walk(suite)
        check_summary(root)
    return runs or [_run(metadata, 'empty')]


def parse_source(format_name, path, metadata):
    """Return independent v1 runs for a Playwright JSON or JUnit XML report."""
    if format_name not in ('playwright', 'junit'):
        raise AdapterError('Unsupported report format.')
    try:
        _metadata(metadata)
        text = Path(path).read_text(encoding='utf-8')
        if format_name == 'junit':
            _require(not re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.IGNORECASE),
                     'JUnit DTD and entity declarations are not supported.')
            return _junit(ET.fromstring(text), metadata)
        return _playwright(json.loads(text, object_pairs_hook=_unique_object), metadata)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError,
            OverflowError, RecursionError, ET.ParseError) as exc:
        if isinstance(exc, AdapterError):
            raise
        raise AdapterError('Invalid native report.') from None
