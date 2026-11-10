import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / 'skills/test-dashboard/scripts/render_dashboard.py'
TEMPLATE = '<!doctype html><script id="dashboard-data" type="application/json">__TEST_DASHBOARD_DATA__</script>'


def result_document():
    return {
        'schema_version': 1,
        'project': {'name': 'Synthetic project', 'revision': 'abc123', 'dirty': False},
        'runs': [{
            'id': 'local-attempt-0', 'label': 'Browser flow', 'environment': 'local',
            'revision': 'abc123', 'state': 'completed', 'inventory_known': True,
            'notes': ['Synthetic fixture only'],
            'cases': [{
                'id': 'profile.rename', 'name': 'Save profile', 'suite': 'Browser',
                'status': 'failed', 'duration_ms': 1350,
                'expected': 'Saved', 'actual': 'Save failed', 'error': 'HTTP 500',
                'command': 'never execute this fixture command',
                'steps': [
                    {'name': 'Open profile', 'status': 'passed'},
                    {'name': 'Save', 'status': 'failed', 'expected': 'Saved',
                     'actual': 'Save failed', 'error': 'HTTP 500',
                     'attachments': [{'kind': 'log', 'label': 'Request', 'path': 'request.log'}]},
                    {'name': 'Verify persisted value', 'status': 'not_run'}],
                'attachments': [{'kind': 'screenshot', 'label': 'Failure', 'path': 'failure.png'}]
            }]
        }]
    }


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.script = self.base / 'skill/scripts/render_dashboard.py'
        self.script.parent.mkdir(parents=True)
        shutil.copy2(RENDERER, self.script)
        self.template = self.base / 'skill/assets/dashboard.html'
        self.template.parent.mkdir()
        self.template.write_text(TEMPLATE, encoding='utf-8')
        self.input = self.base / 'results.json'
        self.output = self.base / 'reports/index.html'
        self.output.parent.mkdir()

    def render(self, document, layout=None):
        self.input.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        command = [sys.executable, str(self.script), '--input', str(self.input), '--output', str(self.output)]
        if layout:
            command.extend(['--layout', layout])
        return subprocess.run(command, capture_output=True, text=True)

    def read_data(self):
        html = self.output.read_text(encoding='utf-8')
        return json.loads(html.split('type="application/json">', 1)[1].split('</script>', 1)[0])

    def test_preserves_failure_steps_and_evidence(self):
        document = result_document()
        (self.output.parent / 'request.log').write_text('Synthetic HTTP 500', encoding='utf-8')
        result = self.render(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read_data()
        case = data['runs'][0]['cases'][0]
        self.assertEqual(case['steps'][1]['error'], 'HTTP 500')
        self.assertEqual(case['steps'][2]['status'], 'not_run')
        self.assertEqual(case['command'], document['runs'][0]['cases'][0]['command'])
        self.assertEqual(case['attachments'][0]['availability'], 'missing')
        self.assertEqual(case['attachments'][0]['href'], '')
        self.assertEqual(case['steps'][1]['attachments'][0]['availability'], 'present')
        self.assertEqual(data['project'], document['project'])
        self.assertEqual(data['layout'], 'workflow')

    def test_keeps_inventory_and_retry_context_without_inventing_totals(self):
        document = result_document()
        retry = copy.deepcopy(document['runs'][0])
        retry.update(id='ci-attempt-1', environment='ci', inventory_known=False)
        retry['cases'][0].update(status='passed')
        document['runs'].append(retry)
        result = self.render(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read_data()
        self.assertEqual([run['inventory_known'] for run in data['runs']], [True, False])
        self.assertEqual([run['environment'] for run in data['runs']], ['local', 'ci'])
        self.assertEqual([run['cases'][0]['id'] for run in data['runs']], ['profile.rename'] * 2)
        self.assertEqual(set(data), {'schema_version', 'project', 'runs', 'layout'})
        self.assertEqual(data['layout'], 'grouped')

    def test_auto_layout_uses_records_runs_suites_and_execution_state(self):
        document = result_document()
        case = document['runs'][0]['cases'][0]
        case.pop('steps')
        self.assertEqual(self.render(document).returncode, 0)
        self.assertEqual(self.read_data()['layout'], 'compact')
        document['runs'][0]['state'] = 'blocked'
        self.assertEqual(self.render(document).returncode, 0)
        self.assertEqual(self.read_data()['layout'], 'grouped')
        document['runs'][0]['state'] = 'completed'
        for index in range(9):
            extra = copy.deepcopy(case)
            extra.update(id='case-' + str(index), suite='Other suite')
            document['runs'][0]['cases'].append(extra)
        self.assertEqual(self.render(document).returncode, 0)
        self.assertEqual(self.read_data()['layout'], 'grouped')
        for layout in ('compact', 'grouped', 'workflow'):
            self.assertEqual(self.render(document, layout).returncode, 0)
            self.assertEqual(self.read_data()['layout'], layout)

    def test_embedded_json_cannot_close_script_or_create_html(self):
        document = result_document()
        payload = '</script><img src=x onerror=alert(1)>&\u2028\u2029'
        document['project']['name'] = payload
        document['runs'][0]['cases'][0]['error'] = payload
        result = self.render(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = self.output.read_text(encoding='utf-8')
        self.assertNotIn('<img', html)
        self.assertNotIn('&', html)
        self.assertNotIn('\u2028', html)
        self.assertNotIn('\u2029', html)
        self.assertEqual(self.read_data()['project']['name'], payload)
        self.assertEqual(html.count('</script>'), 1)

    def test_invalid_contract_is_rejected_without_overwriting_output(self):
        changes = [
            (lambda d: d.pop('project'), 'project'),
            (lambda d: d.update(schema_version=True), 'schema_version'),
            (lambda d: d.update(schema_version=2), 'schema_version'),
            (lambda d: d['project'].update(dirty='false'), 'project.dirty'),
            (lambda d: d['runs'][0].update(inventory_known=1), 'runs[0].inventory_known'),
            (lambda d: d['runs'][0].update(state='success'), 'runs[0].state'),
            (lambda d: d['runs'][0].update(notes='a note'), 'runs[0].notes'),
            (lambda d: d['runs'][0].update(notes=[True]), 'runs[0].notes[0]'),
            (lambda d: d['runs'][0]['cases'][0].update(duration_ms=-1), 'duration_ms'),
            (lambda d: d['runs'][0]['cases'][0].update(duration_ms=True), 'duration_ms'),
            (lambda d: d['runs'][0]['cases'][0].update(duration_ms='10'), 'duration_ms'),
            (lambda d: d['runs'][0]['cases'][0].update(duration_ms=float('inf')), '$'),
            (lambda d: d['runs'][0]['cases'][0].update(duration_ms=float('nan')), '$'),
            (lambda d: d['runs'][0]['cases'][0].update(status='broken'), 'status'),
            (lambda d: d['runs'][0]['cases'][0].update(error={}), 'error'),
            (lambda d: d['runs'][0]['cases'][0].update(steps=['planned']), 'steps[0]'),
            (lambda d: d['runs'][0]['cases'][0]['steps'][0].pop('status'), 'steps[0].status'),
            (lambda d: d['runs'][0]['cases'][0].update(attachments={}), 'attachments'),
            (lambda d: d['runs'][0]['cases'][0]['attachments'][0].update(kind='executable'), 'kind'),
            (lambda d: d['runs'][0]['cases'][0]['attachments'][0].pop('label'), 'label'),
            (lambda d: d['runs'].append(copy.deepcopy(d['runs'][0])), 'runs[1].id'),
            (lambda d: d['runs'][0]['cases'].append(copy.deepcopy(d['runs'][0]['cases'][0])), 'cases[1].id'),
        ]
        for mutate, field in changes:
            with self.subTest(field=field):
                document = result_document()
                mutate(document)
                self.output.write_text('existing report', encoding='utf-8')
                result = self.render(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)
                self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing report')
                self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_blank_identifiers_are_rejected_without_overwriting_output(self):
        for kind in ('run', 'case', 'method'):
            for identifier in ('', ' \t\n'):
                with self.subTest(kind=kind, identifier=identifier):
                    document = result_document()
                    if kind == 'run':
                        document['runs'][0]['id'] = identifier
                        field = 'runs[0].id'
                    elif kind == 'case':
                        document['runs'][0]['cases'][0]['id'] = identifier
                        field = 'runs[0].cases[0].id'
                    else:
                        document['methods'] = [{'id': identifier, 'name': 'Unit',
                                                'reason': 'Configured assertions'}]
                        field = 'methods[0].id'
                    self.output.write_text('existing report', encoding='utf-8')
                    result = self.render(document)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(field, result.stderr)
                    self.assertIn('nonblank', result.stderr)
                    self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing report')
                    self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_nonblank_identifiers_are_preserved_without_normalization(self):
        document = result_document()
        document['runs'][0]['id'] = ' local run '
        document['runs'][0]['cases'][0]['id'] = ' case one '
        document['methods'] = [{'id': ' unit method ', 'name': 'Unit',
                                'reason': 'Configured assertions', 'run_ids': [' local run ']}]
        result = self.render(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read_data()
        self.assertEqual(data['runs'][0]['id'], ' local run ')
        self.assertEqual(data['runs'][0]['cases'][0]['id'], ' case one ')
        self.assertEqual(data['methods'][0]['id'], ' unit method ')
        self.assertEqual(data['methods'][0]['run_ids'], [' local run '])

    def test_unsafe_attachment_values_are_rejected_without_echoing_them(self):
        paths = [
            '../SECRET', '%2e%2e/SECRET', '%252e%252e%252fSECRET', '/SECRET', '//SECRET',
            'C:/SECRET', 'a\\SECRET', 'a%5cSECRET', 'a\nSECRET', 'a%00SECRET',
            'javascript:SECRET', 'data:text/html,SECRET', 'file:///SECRET',
            'https://user:SECRET@example.invalid/log',
            'https://user%3ASECRET%40example.invalid/log',
            'https://example.invalid/%2e%2e/SECRET', 'safe/../SECRET',
        ]
        for path in paths:
            with self.subTest(path=path):
                document = result_document()
                document['runs'][0]['cases'][0]['attachments'][0]['path'] = path
                result = self.render(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('attachments[0].path', result.stderr)
                self.assertNotIn('SECRET', result.stderr)
                self.assertFalse(self.output.exists())

    def test_attachments_respect_real_paths_and_never_trust_supplied_availability(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'evidence.txt').write_text('Synthetic evidence', encoding='utf-8')
        (self.output.parent / 'escape').symlink_to(outside, target_is_directory=True)
        document = result_document()
        attachment = document['runs'][0]['cases'][0]['attachments'][0]
        for path in ('escape/evidence.txt', '%65scape/evidence.txt', 'escape/missing.txt'):
            attachment['path'] = path
            result = self.render(document)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('attachments[0].path', result.stderr)
        (self.output.parent / 'local evidence.txt').write_text('Synthetic', encoding='utf-8')
        attachment.update(path='local%20evidence.txt', availability='missing', href='javascript:poison')
        self.assertEqual(self.render(document).returncode, 0)
        data = self.read_data()['runs'][0]['cases'][0]['attachments'][0]
        self.assertEqual(data['path'], 'local%20evidence.txt')
        self.assertEqual(data['availability'], 'present')
        self.assertEqual(data['href'], 'local%20evidence.txt')
        attachment.update(path='https://reports.example.invalid/test?view=1', availability='present')
        self.assertEqual(self.render(document).returncode, 0)
        self.assertEqual(self.read_data()['runs'][0]['cases'][0]['attachments'][0]['availability'], 'unverified')
        self.assertEqual(self.read_data()['runs'][0]['cases'][0]['attachments'][0]['href'], attachment['path'])

    def test_exploration_preserves_candidates_and_confirmed_findings_with_evidence(self):
        document = result_document()
        exploration = copy.deepcopy(document['runs'][0])
        exploration.update(id='exploration-1', track='exploratory', inventory_known=False,
                           charter='Try unusual profile values', seed='synthetic-42')
        exploration['cases'][0]['status'] = 'passed'
        exploration['cases'][0]['findings'] = [
            {'title': 'Save button flicker', 'status': 'candidate', 'basis': 'Observed once',
             'attachments': [{'kind': 'video', 'label': 'Recording', 'path': 'not-recorded.webm'}]},
            {'title': 'Whitespace retained', 'status': 'confirmed', 'basis': 'Reproduced twice',
             'expected': 'Trim whitespace', 'actual': 'Whitespace retained', 'severity': 'minor'}]
        document['runs'].append(exploration)
        result = self.render(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read_data()
        self.assertNotIn('track', data['runs'][0])
        observed = data['runs'][1]
        self.assertEqual(observed['charter'], exploration['charter'])
        self.assertEqual(observed['seed'], 'synthetic-42')
        self.assertFalse(observed['inventory_known'])
        self.assertEqual(observed['cases'][0]['status'], 'passed')
        findings = observed['cases'][0]['findings']
        self.assertEqual([finding['status'] for finding in findings], ['candidate', 'confirmed'])
        self.assertEqual(findings[0]['attachments'][0]['availability'], 'missing')
        self.assertEqual(findings[0]['attachments'][0]['href'], '')
        self.assertEqual(data['runs'][0]['cases'][0]['id'], observed['cases'][0]['id'])

    def test_invalid_exploration_contract_is_rejected(self):
        for mutation, field in [
            (lambda r: r.update(inventory_known=True), 'inventory_known'),
            (lambda r: r.update(track='random'), 'track'),
            (lambda r: r.update(charter=[]), 'charter'),
            (lambda r: r.update(seed=42), 'seed'),
            (lambda r: r['cases'][0].update(findings={}), 'findings'),
            (lambda r: r['cases'][0]['findings'][0].pop('basis'), 'findings[0].basis'),
            (lambda r: r['cases'][0]['findings'][0].update(status='failed'), 'findings[0].status'),
            (lambda r: r['cases'][0]['findings'][0].update(severity=2), 'findings[0].severity'),
            (lambda r: r['cases'][0]['findings'][0].update(attachments=[
                {'kind': 'log', 'label': 'bad', 'path': '../outside'}]), 'findings[0].attachments[0].path'),
        ]:
            with self.subTest(field=field):
                document = result_document()
                run = document['runs'][0]
                run.update(track='exploratory', inventory_known=False)
                run['cases'][0]['findings'] = [
                    {'title': 'Candidate', 'status': 'candidate', 'basis': 'Observed once'}]
                mutation(run)
                result = self.render(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)

    def test_atomic_replace_failure_cleans_only_its_temporary_file(self):
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_text('keep', encoding='utf-8')
        result = self.render(result_document())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('output', result.stderr)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_bad_json_or_template_preserves_previous_output(self):
        self.output.write_text('previous', encoding='utf-8')
        self.template.write_text('missing placeholder', encoding='utf-8')
        result = self.render(result_document())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'previous')
        self.input.write_text('{"secret": INVALID_SECRET}', encoding='utf-8')
        result = subprocess.run([sys.executable, str(self.script), '--input', str(self.input),
                                 '--output', str(self.output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('INVALID_SECRET', result.stderr)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'previous')

    def test_unknown_extensions_are_retained_as_data_without_executing_commands(self):
        marker = self.base / 'must-not-exist'
        document = result_document()
        document['runs'][0]['cases'][0]['command'] = 'touch ' + str(marker)
        document['extension'] = {'command': 'touch ' + str(marker), 'nested': ['extension data']}
        result = self.render(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_data()['extension'], document['extension'])
        self.assertFalse(marker.exists())

    def test_gap_suggestions_remain_distinct_from_execution_and_preserve_user_decisions(self):
        document = result_document()
        self.assertEqual(self.render(document).returncode, 0)
        self.assertNotIn('gaps', self.read_data())
        document['gaps'] = [
            {'title': 'Keyboard navigation', 'reason': 'No accessibility evidence collected',
             'suggestion': 'Try keyboard only profile editing'},
            {'title': 'Long input', 'reason': 'Boundary remains unverified',
             'suggestion': 'Add boundary checks', 'state': 'accepted',
             'confidence': 'evidence', 'basis': 'Existing specification states 100 characters'},
            {'title': 'Offline mode', 'reason': 'Outside product scope',
             'suggestion': 'Confirm scope', 'state': 'not_applicable', 'confidence': 'hypothesis'}]
        self.assertEqual(self.render(document).returncode, 0)
        data = self.read_data()
        self.assertEqual(data['gaps'], document['gaps'])
        self.assertEqual(data['runs'][0]['cases'][0]['status'], 'failed')
        for mutate, field in [
            (lambda d: d.update(gaps={}), 'gaps'),
            (lambda d: d['gaps'].append('not a record'), 'gaps[3]'),
            (lambda d: d['gaps'][0].pop('reason'), 'gaps[0].reason'),
            (lambda d: d['gaps'][0].update(suggestion=[]), 'gaps[0].suggestion'),
            (lambda d: d['gaps'][0].update(state='completed'), 'gaps[0].state'),
            (lambda d: d['gaps'][0].update(confidence='certain'), 'gaps[0].confidence'),
            (lambda d: d['gaps'][0].update(basis=42), 'gaps[0].basis'),
        ]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(document)
                mutate(invalid)
                self.output.write_text('previous', encoding='utf-8')
                result = self.render(invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)
                self.assertEqual(self.output.read_text(encoding='utf-8'), 'previous')

    def test_numeric_overflow_in_json_extension_is_a_safe_error(self):
        document = result_document()
        document['extension'] = {'number': 'EXPONENT_PLACEHOLDER'}
        self.input.write_text(json.dumps(document).replace('"EXPONENT_PLACEHOLDER"', '1e999'), encoding='utf-8')
        self.output.write_text('previous', encoding='utf-8')
        result = subprocess.run([sys.executable, str(self.script), '--input', str(self.input),
                                 '--output', str(self.output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('$', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'previous')

    def test_method_overview_preserves_configuration_without_implying_execution(self):
        document = result_document()
        self.assertEqual(self.render(document).returncode, 0)
        self.assertNotIn('methods', self.read_data())
        document['methods'] = []
        self.assertEqual(self.render(document).returncode, 0)
        self.assertEqual(self.read_data()['methods'], [])
        document['methods'] = [
            {'id': 'unit', 'name': 'Unit checks', 'reason': 'Existing local assertions',
             'state': 'present', 'category': 'Automated', 'evidence': 'Synthetic runner config',
             'run_ids': ['local-attempt-0']},
            {'id': 'accessibility', 'name': 'Keyboard navigation', 'reason': 'Inspect input workflows',
             'state': 'suggested'},
            {'id': 'load', 'name': 'Load checks', 'reason': 'Workload requirements unassessed'},
            {'id': 'offline', 'name': 'Offline checks', 'reason': 'Outside stated scope',
             'state': 'not_applicable'}]
        self.assertEqual(self.render(document).returncode, 0)
        data = self.read_data()
        self.assertEqual(data['methods'], document['methods'])
        self.assertEqual(data['runs'][0]['cases'][0]['status'], 'failed')
        for mutate, field in [
            (lambda d: d.update(methods={}), 'methods'),
            (lambda d: d['methods'].append('not a record'), 'methods[4]'),
            (lambda d: d['methods'][0].pop('reason'), 'methods[0].reason'),
            (lambda d: d['methods'][0].update(name=[]), 'methods[0].name'),
            (lambda d: d['methods'][0].update(state='passed'), 'methods[0].state'),
            (lambda d: d['methods'][0].update(category=42), 'methods[0].category'),
            (lambda d: d['methods'][0].update(evidence={}), 'methods[0].evidence'),
            (lambda d: d['methods'][0].update(run_ids='local-attempt-0'), 'methods[0].run_ids'),
            (lambda d: d['methods'][0].update(run_ids=[42]), 'methods[0].run_ids[0]'),
            (lambda d: d['methods'][0].update(run_ids=['not-a-run']), 'methods[0].run_ids[0]'),
            (lambda d: d['methods'].append(copy.deepcopy(d['methods'][0])), 'methods[4].id'),
        ]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(document)
                mutate(invalid)
                self.output.write_text('previous', encoding='utf-8')
                result = self.render(invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)
                self.assertEqual(self.output.read_text(encoding='utf-8'), 'previous')

    def test_duplicate_method_run_references_are_rejected_without_overwriting_output(self):
        document = result_document()
        run_id = document['runs'][0]['id']
        document['methods'] = [{'id': 'unit', 'name': 'Unit',
                                'reason': 'Configured assertions', 'run_ids': [run_id, run_id]}]
        self.output.write_text('existing report', encoding='utf-8')
        result = self.render(document)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('methods[0].run_ids[1]', result.stderr)
        self.assertIn('unique', result.stderr)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing report')
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])


if __name__ == '__main__':
    unittest.main()
