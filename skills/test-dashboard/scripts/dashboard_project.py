#!/usr/bin/env python3
"""Initialize project configuration or build a snapshot from existing reports.

This command never runs tests or imports a project's adapter. Project-specific
collectors must be invoked separately and write a normalized v1 JSON document.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
from urllib.parse import quote

from report_adapters import AdapterError, parse_source
from render_dashboard import ContractError, render, validate_document


SKILL_DIR = Path(__file__).resolve().parent.parent
LAYOUTS = ('auto', 'compact', 'grouped', 'workflow')
SOURCE_FIELDS = {'id', 'format', 'path', 'label', 'environment', 'track',
                 'revision', 'started_at', 'notes'}


class ProjectError(ValueError):
    """A diagnostic that does not interpolate untrusted input."""


def require(condition, message):
    if not condition:
        raise ProjectError(message)


def text(value, nonblank=False):
    return isinstance(value, str) and (not nonblank or bool(value.strip()))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'JSON contains duplicate object fields.')
        result[key] = value
    return result


def finite_float(value):
    result = float(value)
    require(math.isfinite(result), 'JSON contains a non-finite number.')
    return result


def invalid_constant(_value):
    raise ProjectError('JSON contains a non-finite number.')


def load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'),
                          object_pairs_hook=unique_object,
                          parse_float=finite_float, parse_constant=invalid_constant)
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError):
        raise ProjectError('Cannot read a valid UTF-8 JSON document.') from None


def relative_path(value, message, allow_parent=False):
    require(text(value, True), message)
    require(not any(ord(char) < 32 for char in value) and '\\' not in value, message)
    path = Path(value)
    require(not path.is_absolute() and not PureWindowsPath(value).drive, message)
    if not allow_parent:
        require('..' not in path.parts, message)
    return path


def contained_path(root, value):
    message = 'Source and output paths must stay inside the project root.'
    relative = relative_path(value, message)
    try:
        result = (root / relative).resolve()
        result.relative_to(root)
    except (ValueError, OSError, RuntimeError):
        raise ProjectError(message) from None
    return result


def read_config(config_path):
    config = load_json(config_path)
    require(isinstance(config, dict), 'Configuration must be an object.')
    require(set(config) <= {'config_version', 'project_root', 'project', 'output',
                            'layout', 'sources'}, 'Configuration has unsupported fields.')
    require(type(config.get('config_version')) is int and config['config_version'] == 1,
            'Configuration version must be integer 1.')
    project = config.get('project')
    require(isinstance(project, dict) and set(project) == {'name'}
            and text(project.get('name'), True), 'Project requires a nonblank name only.')
    root_path = relative_path(config.get('project_root'),
                              'Project root must be a relative directory.', allow_parent=True)
    root = (config_path.parent / root_path).resolve()
    require(root.is_dir(), 'Project root must be an existing directory.')
    layout = config.get('layout', 'auto')
    require(isinstance(layout, str) and layout in LAYOUTS, 'Unsupported dashboard layout.')
    output = contained_path(root, config.get('output', 'reports/test-dashboard/index.html'))
    require(not output.is_dir() and output != root, 'Output must be a file location.')
    sources = config.get('sources')
    require(isinstance(sources, list), 'Sources must be an array.')
    seen = set()
    paths = []
    for source in sources:
        require(isinstance(source, dict) and set(source) <= SOURCE_FIELDS,
                'Source has unsupported fields or is not an object.')
        for field in ('id', 'format', 'path', 'label'):
            require(text(source.get(field), True), 'Source requires id, format, path and label.')
        require(source['id'] not in seen, 'Source identifiers must be unique.')
        seen.add(source['id'])
        for field in ('environment', 'revision', 'started_at'):
            require(field not in source or text(source[field]), 'Invalid source metadata.')
        require('track' not in source or (isinstance(source['track'], str)
                and source['track'] in ('planned', 'exploratory')), 'Invalid source track.')
        require('notes' not in source or (isinstance(source['notes'], list)
                and all(text(note) for note in source['notes'])), 'Invalid source notes.')
        paths.append(contained_path(root, source['path']))
    require(output != config_path.resolve() and output not in paths,
            'Output must not overwrite configuration or source reports.')
    return config, root, output, layout, paths


def git_context(root):
    """Do not attribute a parent repository's revision to a nested project."""
    if not (root / '.git').exists():
        return {}
    environment = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    # Shell Git context must not redirect a build to another worktree or index.
    for key in list(environment):
        if key.startswith('GIT_') and key != 'GIT_OPTIONAL_LOCKS':
            environment.pop(key)
    command = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
               '-C', str(root)]
    try:
        revision = subprocess.run(command + ['rev-parse', '--verify', 'HEAD'],
                                  capture_output=True, text=True, timeout=10,
                                  env=environment, check=False)
        status = subprocess.run(command + ['status', '--porcelain', '--untracked-files=normal'],
                                capture_output=True, text=True, timeout=10,
                                env=environment, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}
    result = {}
    sha = revision.stdout.strip()
    if revision.returncode == 0 and len(sha) in (40, 64) and all(c in '0123456789abcdef' for c in sha):
        result['revision'] = sha
    if status.returncode == 0:
        result['dirty'] = bool(status.stdout.strip())
    return result


def empty_run(source, state, note):
    run = {'id': quote(source['id'], safe='') + ':unavailable', 'label': source['label'],
           'state': state, 'inventory_known': False, 'cases': [],
           'source': source['path'], 'notes': list(source.get('notes', [])) + [note]}
    for field in ('environment', 'track', 'revision', 'started_at'):
        if field in source:
            run[field] = source[field]
    return run


def namespace(document, source):
    prefix = quote(source['id'], safe='') + ':'
    for run in document['runs']:
        run['id'] = prefix + run['id']
        for case in run['cases']:
            case['id'] = prefix + case['id']
    for method in document.get('methods', []):
        method['id'] = prefix + method['id']
        if 'run_ids' in method:
            method['run_ids'] = [prefix + run_id for run_id in method['run_ids']]
    return document


def collect(source, path, output_dir):
    record = {'id': source['id'], 'format': source['format'], 'path': source['path'],
              'sha256': None, 'methods_provided': False, 'gaps_provided': False}
    try:
        if not path.exists():
            record['status'] = 'not_integrated'
            return {'runs': [empty_run(source, record['status'], 'Source report is missing.')]}, record
        if path.is_file():
            record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        if source['format'] not in ('playwright', 'junit', 'normalized'):
            record['status'] = 'not_integrated'
            return {'runs': [empty_run(source, record['status'], 'Source format is not supported.')]}, record
        if source['format'] == 'normalized':
            document = load_json(path)
            validate_document(document, output_dir)
            for run in document['runs']:
                run.setdefault('source', source['path'])
                for field in ('environment', 'track', 'revision', 'started_at'):
                    if field in source:
                        run.setdefault(field, source[field])
                if source.get('notes'):
                    run.setdefault('notes', []).extend(source['notes'])
            validate_document(document, output_dir)
            document = namespace(document, source)
        else:
            metadata = {key: value for key, value in source.items() if key not in ('format', 'path')}
            metadata['id'] = quote(source['id'], safe='')
            metadata['source'] = source['path']
            document = {'schema_version': 1, 'project': {'name': 'Source'},
                        'runs': parse_source(source['format'], path, metadata)}
            validate_document(document, output_dir)
        record['status'] = 'collected'
        for field in ('methods', 'gaps'):
            record[field + '_provided'] = field in document
        return document, record
    except (AdapterError, ContractError, ProjectError, OSError, UnicodeError,
            ValueError, TypeError, KeyError, RecursionError, OverflowError):
        record['status'] = 'collection_error'
        return {'runs': [empty_run(source, record['status'],
                                  'Source could not be collected or does not satisfy v1.')]}, record


def fingerprint(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise ProjectError('Cannot read central dashboard build assets.') from None


def _collect_project(config, root, output, paths):
    project = dict(config['project'])
    project.update(git_context(root))
    document = {'schema_version': 1, 'project': project, 'runs': []}
    records = []
    for source, path in zip(config['sources'], paths):
        collected, record = collect(source, path, output.parent)
        document['runs'].extend(collected['runs'])
        for field in ('methods', 'gaps'):
            if field in collected:
                document.setdefault(field, []).extend(collected[field])
        records.append(record)
    document['snapshot'] = {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'template_sha256': fingerprint(SKILL_DIR / 'assets/dashboard.html'),
        'renderer_sha256': fingerprint(SKILL_DIR / 'scripts/render_dashboard.py'),
        'adapter_sha256': fingerprint(SKILL_DIR / 'scripts/report_adapters.py'),
        'builder_sha256': fingerprint(Path(__file__).resolve()), 'sources': records}
    validate_document(document, output.parent)
    return document


def collect_project(config_path):
    """Read and validate existing reports without writing HTML or printing.

    This has the same source/error and attachment contracts as ``build_project``.
    It may run read-only Git queries for project context, but never runs tests or
    a project's collector. The returned document belongs to the caller.
    """
    config, root, output, _layout, paths = read_config(config_path)
    return _collect_project(config, root, output, paths)


def build_project(config_path):
    config, root, output, layout, paths = read_config(config_path)
    document = _collect_project(config, root, output, paths)
    with tempfile.TemporaryDirectory(prefix='test-dashboard-build-') as temporary:
        normalized = Path(temporary) / 'results.json'
        normalized.write_text(json.dumps(document, ensure_ascii=True, allow_nan=False), encoding='utf-8')
        render(normalized, output, layout)
    print('Dashboard snapshot built; this does not indicate test success.')
    for index, record in enumerate(document['snapshot']['sources']):
        print('Source {}: {}'.format(index + 1, record['status']))
    return document


def init_project(root, name, with_adapter=False):
    require(text(name, True), 'Project name must be nonblank.')
    require(root.is_dir() and not root.is_symlink(), 'Project root must be an existing directory.')
    directory = root / '.test-dashboard'
    targets = [directory / 'config.json']
    if with_adapter:
        targets.append(directory / 'adapter.py')
    require(not directory.is_symlink() and (not directory.exists() or directory.is_dir()),
            'Project dashboard directory conflicts with an existing target.')
    require(not any(path.exists() or path.is_symlink() for path in targets),
            'Initialization target already exists; no files were written.')
    config = load_json(SKILL_DIR / 'assets/project/config.json')
    config['project']['name'] = name
    contents = [json.dumps(config, ensure_ascii=False, indent=2) + '\n']
    if with_adapter:
        try:
            contents.append((SKILL_DIR / 'assets/project/adapter.py').read_text(encoding='utf-8'))
        except (OSError, UnicodeError):
            raise ProjectError('Cannot read the central adapter example.') from None
    created = []
    made_directory = False
    try:
        if not directory.exists():
            directory.mkdir()
            made_directory = True
        for target, content in zip(targets, contents):
            with target.open('x', encoding='utf-8') as stream:
                created.append(target)
                stream.write(content)
    except OSError:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        if made_directory:
            directory.rmdir()
        raise ProjectError('Initialization failed; only newly created files were removed.') from None
    print('Project dashboard configuration created. Replace example report paths before building.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    initialize = subcommands.add_parser('init', help='Create project configuration without overwriting files.')
    initialize.add_argument('--root', required=True, type=Path)
    initialize.add_argument('--name', required=True)
    initialize.add_argument('--with-adapter', action='store_true')
    build = subcommands.add_parser('build', help='Build from existing reports; never execute tests.')
    build.add_argument('--config', required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == 'init':
            init_project(arguments.root, arguments.name, arguments.with_adapter)
        else:
            build_project(arguments.config.absolute())
    except (ProjectError, ContractError, OSError, ValueError, RecursionError, OverflowError):
        print('error: dashboard configuration, input, or rendering failed; existing output was preserved.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
