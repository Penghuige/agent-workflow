"""Memory APIs reuse the offline engine without changing project snapshots."""

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DashboardMemoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.skill = self.base / 'engine'
        shutil.copytree(ROOT / 'skills/test-dashboard', self.skill,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.renderer = load_module('memory_renderer', self.skill / 'scripts/render_dashboard.py')
        adapter = load_module('memory_adapter', self.skill / 'scripts/report_adapters.py')
        with patch.dict(sys.modules, {'render_dashboard': self.renderer, 'report_adapters': adapter}):
            self.builder = load_module('memory_builder', self.skill / 'scripts/dashboard_project.py')
        self.project = self.base / 'project'
        self.project.mkdir()
        self.config = self.project / '.test-dashboard/config.json'
        self.config.parent.mkdir()
        self.output = self.project / 'reports/dashboard/index.html'

    def document(self):
        return {'schema_version': 1, 'project': {'name': 'Synthetic <project>'},
                'runs': [{'id': 'run', 'label': 'Synthetic run', 'state': 'completed',
                          'inventory_known': True, 'cases': [
                              {'id': 'case', 'name': 'Synthetic case', 'suite': 'memory',
                               'status': 'failed', 'error': '<script>unsafe()</script>'}]}]}

    def configure(self, sources):
        self.config.write_text(json.dumps({
            'config_version': 1, 'project_root': '..', 'project': {'name': 'Configured project'},
            'output': 'reports/dashboard/index.html', 'layout': 'auto', 'sources': sources,
        }), encoding='utf-8')

    def tree(self):
        return {str(path.relative_to(self.project)): path.read_bytes()
                for path in self.project.rglob('*') if path.is_file()}

    def api(self, module, name):
        value = getattr(module, name, None)
        self.assertTrue(callable(value), 'Missing memory API: ' + name)
        return value

    def data(self, html):
        return json.loads(html.split('id="dashboard-data" type="application/json">', 1)[1]
                         .split('</script>', 1)[0])

    def test_collect_does_not_write_or_print_and_preserves_source_errors(self):
        collect = self.api(self.builder, 'collect_project')
        good = self.project / 'good.json'
        good.write_text(json.dumps(self.document()), encoding='utf-8')
        bad = self.project / 'bad.xml'
        bad.write_text('<invalid', encoding='utf-8')
        self.configure([{'id': name, 'format': kind, 'path': path, 'label': name}
                        for name, kind, path in [('good', 'normalized', 'good.json'),
                                                 ('bad', 'junit', 'bad.xml'),
                                                 ('missing', 'junit', 'absent.xml')]])
        before = self.tree()
        output = io.StringIO()
        with redirect_stdout(output):
            document = collect(self.config)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.output.parent.exists())
        self.assertEqual(document['project']['name'], 'Configured project')
        self.assertEqual([run['state'] for run in document['runs']],
                         ['completed', 'collection_error', 'not_integrated'])
        self.assertEqual([source['status'] for source in document['snapshot']['sources']],
                         ['collected', 'collection_error', 'not_integrated'])
        self.assertEqual(document['runs'][0]['cases'][0]['status'], 'failed')
        self.assertNotIn('layout', document)
        self.assertIn('template_sha256', document['snapshot'])

    def test_render_html_is_pure_and_uses_existing_attachment_contract(self):
        render_html = self.api(self.renderer, 'render_html')
        self.output.parent.mkdir(parents=True)
        (self.output.parent / 'trace.zip').write_bytes(b'synthetic evidence')
        document = self.document()
        document['runs'][0]['cases'][0]['attachments'] = [
            {'kind': 'trace', 'label': 'Recorded trace', 'path': 'trace.zip'}]
        original = copy.deepcopy(document)
        before = self.tree()
        html = render_html(document, self.output.parent, 'auto')
        self.assertEqual(document, original)
        self.assertEqual(self.tree(), before)
        parsed = self.data(html)
        attachment = parsed['runs'][0]['cases'][0]['attachments'][0]
        self.assertEqual(attachment['availability'], 'present')
        self.assertEqual(attachment['href'], 'trace.zip')
        self.assertEqual(parsed['layout'], 'compact')
        self.assertNotIn('<script>unsafe()</script>', html)

    def test_render_html_does_not_create_output_directory(self):
        render_html = self.api(self.renderer, 'render_html')
        html = render_html(self.document(), self.output.parent, 'workflow')
        self.assertEqual(self.data(html)['layout'], 'workflow')
        self.assertFalse(self.output.parent.exists())

    def test_file_renderer_and_memory_renderer_share_identical_output(self):
        render_html = self.api(self.renderer, 'render_html')
        document = self.document()
        source = self.project / 'input.json'
        source.write_text(json.dumps(document), encoding='utf-8')
        html = render_html(document, self.output.parent, 'grouped')
        self.renderer.render(source, self.output, 'grouped')
        self.assertEqual(self.output.read_text(encoding='utf-8'), html)

    def test_invalid_layout_or_document_fails_before_writing(self):
        render_html = self.api(self.renderer, 'render_html')
        with self.assertRaises(self.renderer.ContractError):
            render_html(self.document(), self.output.parent, 'unsupported')
        with self.assertRaises(self.renderer.ContractError):
            render_html({'schema_version': 1}, self.output.parent, 'auto')
        self.assertFalse(self.output.parent.exists())


if __name__ == '__main__':
    unittest.main()
