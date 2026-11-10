"""Project setup/build crosses the same CLI seam used by real projects."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DashboardProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.skill = self.base / 'central-skill'
        shutil.copytree(ROOT / 'skills/test-dashboard', self.skill,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.cli = self.skill / 'scripts/dashboard_project.py'
        self.project = self.base / 'project'
        self.project.mkdir()
        self.config = self.project / '.test-dashboard/config.json'
        self.output = self.project / 'reports/test-dashboard/index.html'

    def command(self, *args):
        return subprocess.run([sys.executable, str(self.cli), *map(str, args)],
                              capture_output=True, text=True)

    def write_config(self, sources=None, **updates):
        document = {
            'config_version': 1, 'project_root': '..', 'project': {'name': 'Synthetic project'},
            'output': 'reports/test-dashboard/index.html', 'layout': 'auto',
            'sources': [] if sources is None else sources,
        }
        document.update(updates)
        self.config.parent.mkdir(exist_ok=True)
        self.config.write_text(json.dumps(document), encoding='utf-8')
        return document

    def source(self, ident, format_name, path):
        return {'id': ident, 'format': format_name, 'path': path, 'label': ident}

    def input(self, relative, value):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def normalized(self):
        return {
            'schema_version': 1, 'project': {'name': 'Source must not override project'},
            'runs': [{'id': 'round', 'label': 'Manual inventory', 'state': 'completed',
                      'revision': 'historical', 'inventory_known': True,
                      'cases': [{'id': 'same-case', 'name': 'Planned but not executed',
                                 'suite': 'Manual', 'status': 'not_run'}]}],
            'methods': [{'id': 'method', 'name': 'Manual review', 'reason': 'Synthetic inventory',
                         'state': 'present', 'run_ids': ['round']}],
        }

    def data(self):
        html = self.output.read_text(encoding='utf-8')
        return json.loads(html.split('id="dashboard-data" type="application/json">', 1)[1]
                         .split('</script>', 1)[0])

    def test_init_creates_only_project_config_and_optional_adapter(self):
        result = self.command('init', '--root', self.project, '--name', 'Name with "quotes"')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.config.read_text())
        self.assertEqual(config['project']['name'], 'Name with "quotes"')
        self.assertEqual({s['format'] for s in config['sources']}, {'playwright', 'junit'})
        self.assertFalse((self.config.parent / 'adapter.py').exists())
        self.assertFalse(list(self.project.rglob('*.html')))
        second = self.base / 'another-project'
        second.mkdir()
        result = self.command('init', '--root', second, '--name', 'Another', '--with-adapter')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((second / '.test-dashboard/adapter.py').is_file())

    def test_init_conflicts_preserve_files_without_partial_creation(self):
        self.config.parent.mkdir()
        adapter = self.config.parent / 'adapter.py'
        adapter.write_text('user-owned adapter')
        result = self.command('init', '--root', self.project, '--name', 'New', '--with-adapter')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        self.assertEqual(adapter.read_text(), 'user-owned adapter')
        self.config.write_text('user-owned config')
        result = self.command('init', '--root', self.project, '--name', 'New')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), 'user-owned config')

    def test_missing_and_unsupported_sources_remain_visible(self):
        self.input('reports/unknown.json', {})
        self.write_config([self.source('missing', 'playwright', 'reports/missing.json'),
                           self.source('unsupported', 'unknown-format', 'reports/unknown.json')])
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.data()
        self.assertEqual(len(data['runs']), 2)
        self.assertTrue(all(r['state'] == 'not_integrated' and not r['cases']
                            and r['inventory_known'] is False for r in data['runs']))
        self.assertTrue(all('revision' not in r for r in data['runs']))
        self.assertIn('not_integrated', result.stdout)

    def test_bad_source_does_not_hide_other_valid_results_or_echo_input(self):
        bad = self.project / 'reports/bad.xml'
        bad.parent.mkdir()
        bad.write_text('<testsuite SECRET_VALUE', encoding='utf-8')
        self.input('reports/good.json', self.normalized())
        self.write_config([self.source('bad', 'junit', 'reports/bad.xml'),
                           self.source('good', 'normalized', 'reports/good.json')])
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SECRET_VALUE', result.stdout + result.stderr)
        data = self.data()
        self.assertEqual({r['state'] for r in data['runs']}, {'completed', 'collection_error'})
        self.assertEqual(sum(len(r['cases']) for r in data['runs']), 1)

    def test_normalized_sources_are_namespaced_and_keep_provenance(self):
        self.input('reports/a.json', self.normalized())
        self.input('reports/b.json', self.normalized())
        self.write_config([self.source('a', 'normalized', 'reports/a.json'),
                           self.source('b', 'normalized', 'reports/b.json')])
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.data()
        self.assertEqual(data['project']['name'], 'Synthetic project')
        run_ids = {r['id'] for r in data['runs']}
        self.assertEqual(len(run_ids), 2)
        self.assertEqual(len({m['id'] for m in data['methods']}), 2)
        self.assertTrue(all(m['run_ids'][0] in run_ids for m in data['methods']))
        self.assertTrue(all(r['revision'] == 'historical' for r in data['runs']))
        self.assertTrue(all(r['cases'][0]['status'] == 'not_run' for r in data['runs']))
        self.assertNotIn('gaps', data)

    def test_shared_template_refresh_records_version_and_updates_snapshot(self):
        self.write_config()
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.data()['snapshot']
        template = self.skill / 'assets/dashboard.html'
        self.assertEqual(first['template_sha256'], hashlib.sha256(template.read_bytes()).hexdigest())
        self.assertTrue(first['generated_at'])
        for field in ['renderer_sha256', 'adapter_sha256', 'builder_sha256']:
            self.assertEqual(len(first[field]), 64)
        self.assertFalse(list(self.project.rglob('*.css')))
        self.assertFalse(list(self.project.rglob('*.js')))
        template.write_text(template.read_text() + '\n<!-- shared template refresh -->\n')
        old_html = self.output.read_bytes()
        self.assertNotIn(b'shared template refresh', old_html)
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shared template refresh', self.output.read_text())
        self.assertNotEqual(self.data()['snapshot']['template_sha256'], first['template_sha256'])

    def test_normalized_source_metadata_is_a_fallback_and_notes_are_preserved(self):
        doc = self.normalized()
        doc['runs'][0]['notes'] = ['Original evidence']
        self.input('reports/metadata.json', doc)
        source = self.source('manual', 'normalized', 'reports/metadata.json')
        source.update(environment='isolated-local', revision='must-not-replace-history',
                      started_at='2026-10-29T00:00:00Z', notes=['Collection restriction'])
        self.write_config([source])
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.data()['runs'][0]
        self.assertEqual(run['revision'], 'historical')
        self.assertEqual(run['environment'], 'isolated-local')
        self.assertEqual(run['source'], 'reports/metadata.json')
        self.assertEqual(run['started_at'], '2026-10-29T00:00:00Z')
        self.assertEqual(run['notes'], ['Original evidence', 'Collection restriction'])

    def test_invalid_config_and_escaping_paths_preserve_existing_html(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'data.json').write_text('{}')
        (self.project / 'escape').symlink_to(outside, target_is_directory=True)
        variants = [
            {'sources': [self.source('a', 'normalized', '../outside/data.json')]},
            {'sources': [self.source('a', 'normalized', 'escape/data.json')]},
            {'output': '../outside/index.html'},
            {'sources': [self.source('a', 'junit', 'reports/a.xml'),
                         self.source('a', 'junit', 'reports/b.xml')]},
            {'command': 'never execute SECRET_VALUE'},
            {'layout': 'not-a-layout'},
        ]
        for updates in variants:
            with self.subTest(updates=updates):
                self.write_config(**updates)
                self.output.parent.mkdir(parents=True, exist_ok=True)
                self.output.write_text('existing snapshot')
                result = self.command('build', '--config', self.config)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_text(), 'existing snapshot')
                self.assertNotIn('SECRET_VALUE', result.stdout + result.stderr)
        self.assertFalse((outside / 'index.html').exists())

    def test_invalid_normalized_data_is_a_collection_error(self):
        doc = self.normalized()
        doc['runs'][0]['cases'][0]['status'] = 'SECRET_INVALID'
        self.input('reports/invalid.json', doc)
        self.write_config([self.source('bad', 'normalized', 'reports/invalid.json')])
        result = self.command('build', '--config', self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SECRET_INVALID', result.stdout + result.stderr)
        self.assertEqual(self.data()['runs'][0]['state'], 'collection_error')
        self.assertEqual(self.data()['runs'][0]['cases'], [])

    def test_no_renderer_template_does_not_replace_existing_html(self):
        self.write_config()
        self.output.parent.mkdir(parents=True)
        self.output.write_text('previous snapshot')
        (self.skill / 'assets/dashboard.html').write_text('missing data placeholder')
        result = self.command('build', '--config', self.config)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), 'previous snapshot')
