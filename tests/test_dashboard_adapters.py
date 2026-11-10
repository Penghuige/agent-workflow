"""Synthetic report fixtures exercise the public adapter boundary only."""
import importlib.util
import copy
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/test-dashboard/scripts/report_adapters.py'
spec = importlib.util.spec_from_file_location('report_adapters', SCRIPT)
adapters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapters)


def playwright_result(status='passed', retry=0, **extra):
    result = {'status': status, 'retry': retry, 'duration': 125,
              'workerIndex': 0, 'startTime': '2026-10-29T01:00:00Z',
              'errors': [], 'attachments': [], 'stdout': [], 'stderr': []}
    result.update(extra)
    return result


def playwright_report(tests=None, **extra):
    if tests is None:
        tests = [{'projectId': 'chromium', 'projectName': 'Chrome',
                  'expectedStatus': 'passed', 'status': 'expected',
                  'results': [playwright_result()]}]
    report = {'config': {'secret': 'DO_NOT_COPY_CONFIG'}, 'errors': [],
              'suites': [{'title': 'profile.spec.ts', 'specs': [
                  {'id': 'profile.save', 'title': 'Save profile', 'tests': tests}]}],
              'stats': {'startTime': '2026-10-29T01:00:00Z', 'duration': 500,
                        'expected': 1, 'unexpected': 0, 'flaky': 0, 'skipped': 0}}
    report.update(extra)
    return report


class ReportAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source-report'
        self.metadata = {'id': 'browser', 'label': 'Browser flows',
                         'source': 'reports/native-report', 'environment': 'local',
                         'notes': ['Synthetic fixture']}

    def parse(self, format_name, report):
        text = json.dumps(report) if isinstance(report, dict) else report
        self.path.write_text(text, encoding='utf-8')
        return adapters.parse_source(format_name, self.path, self.metadata)

    def test_playwright_preserves_projects_retries_errors_and_real_steps(self):
        tests = [
            {'projectId': 'chromium', 'projectName': 'Chrome', 'expectedStatus': 'passed',
             'status': 'flaky', 'results': [
                 playwright_result('failed', error={'message': 'HTTP 500'},
                                   errors=[{'message': 'HTTP 500'}], steps=[
                     {'title': 'Save', 'duration': 100, 'error': {'message': 'HTTP 500'}}],
                                   attachments=[{'path': '/private/secret/trace.zip',
                                                 'name': 'trace', 'contentType': 'application/zip'}]),
                 playwright_result('passed', retry=1, duration=75)]},
            {'projectId': 'firefox', 'projectName': 'Firefox', 'expectedStatus': 'skipped',
             'status': 'skipped', 'results': [playwright_result('skipped', duration=0)]}]
        report = playwright_report(tests)
        report['stats'].update(expected=0, flaky=1, skipped=1)
        runs = self.parse('playwright', report)
        self.assertEqual(len(runs), 3)
        cases = [run['cases'][0] for run in runs]
        self.assertEqual([case['status'] for case in cases], ['failed', 'passed', 'skipped'])
        self.assertEqual([case['duration_ms'] for case in cases], [125, 75, 0])
        self.assertIn('HTTP 500', cases[0]['error'])
        self.assertEqual(cases[0]['steps'], [
            {'name': 'Save', 'status': 'failed', 'duration_ms': 100, 'error': 'HTTP 500'}])
        self.assertNotIn('steps', cases[1])
        self.assertTrue(all(run['inventory_known'] is False for run in runs))
        self.assertEqual(len({run['id'] for run in runs}), 3)
        self.assertTrue(all('revision' not in run for run in runs))
        self.assertIn('Chrome', runs[0]['environment'])
        self.assertIn('Firefox', runs[2]['environment'])
        rendered = json.dumps(runs)
        self.assertNotIn('DO_NOT_COPY_CONFIG', rendered)
        self.assertNotIn('/private/secret', rendered)
        self.assertTrue(any('attachment' in note.lower() for note in runs[0]['notes']))

    def test_junit_nested_suites_preserve_outcomes_duplicates_and_metadata(self):
        self.metadata.update(revision='reported-rev', track='exploratory')
        runs = self.parse('junit', '''<testsuites tests="5" failures="1" errors="1" skipped="1">
          <testsuite name="python" tests="5" failures="1" errors="1" skipped="1">
            <properties><property name="secret" value="DO_NOT_COPY_PROPERTIES"/></properties>
            <testcase classname="api.TestSave" name="save" time="0.125"/>
            <testsuite name="nested" tests="4" failures="1" errors="1" skipped="1">
              <testcase classname="api.TestSave" name="save" time="0.5">
                <failure message="HTTP 500">AssertionError: HTTP 500</failure>
              </testcase>
              <testcase name="setup"><error message="fixture failed">setup traceback</error></testcase>
              <testcase name="optional"><skipped message="service unavailable"/></testcase>
              <testcase name="save"><system-out>DO_NOT_COPY_STDOUT</system-out></testcase>
            </testsuite>
          </testsuite>
        </testsuites>''')
        cases = [case for run in runs for case in run['cases']]
        self.assertEqual([case['status'] for case in cases],
                         ['passed', 'failed', 'failed', 'skipped', 'passed'])
        self.assertEqual(cases[0]['duration_ms'], 125)
        self.assertEqual(cases[1]['duration_ms'], 500)
        self.assertNotIn('duration_ms', cases[2])
        self.assertIn('AssertionError: HTTP 500', cases[1]['error'])
        self.assertIn('fixture failed', cases[2]['error'])
        self.assertIn('service unavailable', cases[3]['actual'])
        self.assertEqual(len({case['id'] for case in cases}), 5)
        self.assertTrue(all(run['revision'] == 'reported-rev' for run in runs))
        self.assertTrue(all(run['track'] == 'exploratory' for run in runs))
        self.assertTrue(all(run['inventory_known'] is False for run in runs))
        self.assertTrue(all('steps' not in case for case in cases))
        self.assertNotIn('DO_NOT_COPY', json.dumps(runs))

    def test_empty_reports_are_explicit_unknown_inventory(self):
        for format_name, report in [('playwright', {'suites': []}),
                                    ('junit', '<testsuites/>'),
                                    ('junit', '<testsuite name="empty" tests="0"/>')]:
            with self.subTest(format=format_name, report=report):
                runs = self.parse(format_name, report)
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]['cases'], [])
                self.assertEqual(runs[0]['state'], 'completed')
                self.assertFalse(runs[0]['inventory_known'])

    def test_junit_container_can_mix_direct_cases_and_nested_suites(self):
        self.metadata.update(revision='reported-node22-revision', environment='Historical Node 22')
        runs = self.parse('junit', '''<testsuites name="Node run" tests="6" failures="1" errors="1" skipped="1">
          <testsuite name="outer" tests="2" failures="1">
            <testcase name="duplicate" time="0.01"/>
            <testsuite name="inner" tests="1" failures="1">
              <testcase name="assertion"><failure message="Expected value">assertion stack</failure></testcase>
            </testsuite>
          </testsuite>
          <testcase name="direct" time="0.125"/>
          <testcase name="setup" time="0.5"><error message="Setup failed">setup stack</error></testcase>
          <testsuite name="optional" tests="1" skipped="1">
            <testcase name="optional"><skipped message="Not available"/></testcase>
          </testsuite>
          <testcase name="duplicate" time="0"/>
        </testsuites>''')
        cases = [case for run in runs for case in run['cases']]
        self.assertCountEqual([case['name'] for case in cases],
                              ['duplicate', 'assertion', 'direct', 'setup', 'optional', 'duplicate'])
        self.assertEqual(len({case['id'] for case in cases}), 6)
        self.assertEqual(len({run['id'] for run in runs}), len(runs))
        by_name = {case['name']: case for case in cases}
        self.assertEqual(by_name['direct']['status'], 'passed')
        self.assertEqual(by_name['direct']['duration_ms'], 125)
        self.assertEqual(by_name['setup']['status'], 'failed')
        self.assertEqual(by_name['setup']['duration_ms'], 500)
        self.assertIn('Setup failed\nsetup stack', by_name['setup']['error'])
        self.assertIn('Expected value\nassertion stack', by_name['assertion']['error'])
        self.assertEqual(by_name['optional']['status'], 'skipped')
        self.assertEqual(by_name['optional']['actual'], 'Not available')
        self.assertTrue(all(run['revision'] == 'reported-node22-revision' for run in runs))
        self.assertTrue(all(run['environment'] == 'Historical Node 22' for run in runs))

    def test_junit_container_with_only_direct_cases_preserves_root_identity(self):
        runs = self.parse('junit', '''<testsuites name="Node direct" timestamp="2026-10-29T12:00:00Z"
                                       tests="2" failures="0" errors="0" skipped="1" time="0.2">
          <testcase name="standalone" classname="node.test" time="0.125"/>
          <testcase name="pending"><skipped message="Pending feature"/></testcase>
        </testsuites>''')
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['label'], 'Browser flows / Node direct')
        self.assertEqual(runs[0]['started_at'], '2026-10-29T12:00:00Z')
        self.assertEqual([case['name'] for case in runs[0]['cases']], ['standalone', 'pending'])
        self.assertEqual([case['status'] for case in runs[0]['cases']], ['passed', 'skipped'])
        self.assertEqual(runs[0]['cases'][0]['suite'], 'Node direct / node.test')
        self.assertEqual(runs[0]['cases'][0]['duration_ms'], 125)

    def test_junit_pure_suite_container_keeps_run_labels_and_identity(self):
        runs = self.parse('junit', '''<testsuites name="Container label is not a suite">
          <testsuite name="first"><testcase name="a"/></testsuite>
          <testsuite name="second"><testcase name="b"/></testsuite>
        </testsuites>''')
        self.assertEqual([run['id'] for run in runs], ['browser:suite-0', 'browser:suite-1'])
        self.assertEqual([run['label'] for run in runs],
                         ['Browser flows / first', 'Browser flows / second'])
        self.assertEqual([run['cases'][0]['suite'] for run in runs], ['first', 'second'])

    def test_junit_mixed_and_direct_container_summaries_are_checked_against_original_tree(self):
        for body in ('<testcase name="direct"><failure/></testcase>',
                     '<testcase name="direct"><failure/></testcase>'
                     '<testsuite tests="1"><testcase name="nested"/></testsuite>'):
            for attributes in ('tests="0"', 'failures="0"', 'errors="1"', 'skipped="1"'):
                with self.subTest(body=body, attributes=attributes):
                    with self.assertRaises(adapters.AdapterError) as error:
                        self.parse('junit', '<testsuites ' + attributes + '>' + body + '</testsuites>')
                    self.assertIn('summary', str(error.exception))

    def test_playwright_interrupted_missing_results_and_global_errors_stay_visible(self):
        report = playwright_report([
            {'projectName': 'Chrome', 'status': 'unexpected',
             'results': [playwright_result('interrupted')]},
            {'projectName': 'Chrome', 'status': 'skipped', 'results': []}],
            errors=[{'message': 'Global setup failed'}])
        report.pop('stats')
        runs = self.parse('playwright', report)
        cases = [case for run in runs for case in run['cases']]
        self.assertEqual(len(cases), 2)
        self.assertTrue(all(case['status'] == 'not_run' for case in cases))
        self.assertTrue(any(run['state'] == 'blocked' for run in runs))
        errors = [run for run in runs if run['state'] == 'collection_error']
        self.assertEqual(len(errors), 1)
        self.assertIn('Global setup failed', '\n'.join(errors[0]['notes']))

    def test_playwright_undefined_status_is_only_not_run_for_unstarted_worker(self):
        report = playwright_report()
        result = report['suites'][0]['specs'][0]['tests'][0]['results'][0]
        result.pop('status')
        result['workerIndex'] = -1
        report['stats'].update(expected=0, skipped=1)
        report['suites'][0]['specs'][0]['tests'][0]['status'] = 'skipped'
        runs = self.parse('playwright', report)
        self.assertEqual(runs[0]['state'], 'blocked')
        self.assertEqual(runs[0]['cases'][0]['status'], 'not_run')
        result['workerIndex'] = 0
        with self.assertRaises(adapters.AdapterError):
            self.parse('playwright', report)

    def test_playwright_expected_failure_keeps_actual_failed_and_timeout(self):
        report = playwright_report([
            {'projectName': 'Chrome', 'status': 'expected', 'expectedStatus': 'failed',
             'results': [playwright_result('failed')]},
            {'projectName': 'Chrome', 'status': 'unexpected', 'expectedStatus': 'passed',
             'results': [playwright_result('timedOut')]}])
        report['stats'].update(unexpected=1)
        runs = self.parse('playwright', report)
        self.assertEqual([case['status'] for case in runs[0]['cases']], ['failed', 'failed'])
        self.assertIn('failed', runs[0]['cases'][0]['expected'])
        self.assertIn('timedOut', runs[0]['cases'][1]['actual'])

    def test_playwright_rejects_unknown_status_bad_structure_and_inconsistent_summary(self):
        changes = [
            lambda report: report.update(suites={}),
            lambda report: report['suites'][0].update(specs=None),
            lambda report: report['suites'][0]['specs'][0].update(tests=[]),
            lambda report: report['suites'][0]['specs'][0]['tests'][0].update(status='mystery'),
            lambda report: report['suites'][0]['specs'][0]['tests'][0]['results'][0].update(status='mystery'),
            lambda report: report['stats'].update(unexpected=1),
            lambda report: report['stats'].update(expected=0),
            lambda report: report['stats'].update(expected=True),
            lambda report: report['stats'].update(duration=-1),
            lambda report: report['suites'][0]['specs'][0]['tests'][0]['results'][0].update(retry=-1),
            lambda report: report['suites'][0]['specs'][0]['tests'][0]['results'].append(playwright_result()),
            lambda report: report['suites'][0]['specs'][0]['tests'][0]['results'][0].update(error='not-an-object'),
        ]
        for change in changes:
            report = playwright_report()
            change(report)
            with self.subTest(change=changes.index(change)):
                with self.assertRaises(adapters.AdapterError):
                    self.parse('playwright', report)
        for report in ({}, [], {'suites': [{'title': 'missing structure'}]}):
            with self.subTest(report=report):
                with self.assertRaises(adapters.AdapterError):
                    self.parse('playwright', json.dumps(report))

    def test_playwright_rejects_non_finite_negative_and_non_numeric_durations(self):
        for duration in (-1, float('nan'), float('inf'), True, '1'):
            report = playwright_report()
            report['suites'][0]['specs'][0]['tests'][0]['results'][0]['duration'] = duration
            with self.subTest(duration=duration):
                with self.assertRaises(adapters.AdapterError):
                    self.parse('playwright', report)
        report = playwright_report()
        report['suites'][0]['specs'][0]['tests'][0]['results'][0]['steps'] = [
            {'title': 'Step', 'duration': -1}]
        with self.assertRaises(adapters.AdapterError):
            self.parse('playwright', report)

    def test_junit_rejects_entities_bad_structure_unknown_outcomes_and_summary_mismatches(self):
        reports = [
            '<!DOCTYPE testsuite [<!ENTITY secret "DO_NOT_ECHO_SECRET">]><testsuite/>',
            '<!DOCTYPE testsuite SYSTEM "file:///DO_NOT_ECHO_SECRET"><testsuite/>',
            '<report/>', '<testsuites><unsupported/></testsuites>',
            '<testsuites><testcase name="bad" status="unknown"/></testsuites>',
            '<testsuite><testcase/></testsuite>',
            '<testsuite tests="1"/>',
            '<testsuite failures="0"><testcase name="x"><failure/></testcase></testsuite>',
            '<testsuite errors="1"><testcase name="x"/></testsuite>',
            '<testsuite skipped="1"><testcase name="x"/></testsuite>',
            '<testsuite><testcase name="x"><flakyFailure/></testcase></testsuite>',
            '<testsuite><testcase name="x" status="unknown"/></testsuite>',
            '<testsuite><testcase name="x"><failure/><skipped/></testcase></testsuite>',
            '<testsuites tests="0"><testsuite><testcase name="x"/></testsuite></testsuites>',
            '<testsuite tests="-1"/>', '<testsuite time="-1"/>',
        ]
        for report in reports:
            with self.subTest(report=report):
                with self.assertRaises(adapters.AdapterError) as error:
                    self.parse('junit', report)
                self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))

    def test_junit_rejects_non_finite_negative_and_overflowing_durations(self):
        for duration in ('-1', 'NaN', 'Infinity', 'invalid', '1e308'):
            with self.subTest(duration=duration):
                with self.assertRaises(adapters.AdapterError):
                    self.parse('junit', '<testsuite><testcase name="x" time="' + duration + '"/></testsuite>')

    def test_parser_errors_never_echo_raw_input_paths_or_bad_metadata(self):
        for format_name, report in [('playwright', '{"DO_NOT_ECHO_SECRET": broken'),
                                    ('junit', '<DO_NOT_ECHO_SECRET>')]:
            with self.subTest(format=format_name):
                with self.assertRaises(adapters.AdapterError) as error:
                    self.parse(format_name, report)
                self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))
        with self.assertRaises(adapters.AdapterError) as error:
            adapters.parse_source('playwright', self.path.parent / 'DO_NOT_ECHO_SECRET', self.metadata)
        self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))
        self.metadata['notes'] = 'DO_NOT_ECHO_SECRET'
        with self.assertRaises(adapters.AdapterError) as error:
            self.parse('playwright', {'suites': []})
        self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))

    def test_metadata_is_copied_without_mutation_or_inferred_revision(self):
        before = copy.deepcopy(self.metadata)
        runs = self.parse('playwright', playwright_report())
        runs[0]['notes'].append('Caller changed result')
        self.assertEqual(self.metadata, before)
        self.assertNotIn('revision', runs[0])

    def test_playwright_shards_and_nested_actual_steps_are_retained(self):
        report = playwright_report()
        test = report['suites'][0]['specs'][0]['tests'][0]
        test['results'] = [playwright_result(shardIndex=1, steps=[
            {'title': 'Checkout', 'duration': 100, 'steps': [
                {'title': 'Save', 'duration': 50}]}]),
            playwright_result(shardIndex=2)]
        runs = self.parse('playwright', report)
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0]['id'], runs[1]['id'])
        self.assertIn('1', '\n'.join(runs[0]['notes']))
        self.assertIn('2', '\n'.join(runs[1]['notes']))
        self.assertEqual([step['name'] for step in runs[0]['cases'][0]['steps']],
                         ['Checkout', 'Checkout / Save'])

    def test_playwright_rejects_ambiguous_duplicate_json_keys_and_malformed_steps(self):
        with self.assertRaises(adapters.AdapterError):
            self.parse('playwright', '{"suites": [{}], "suites": []}')
        report = playwright_report()
        report['suites'][0]['specs'][0]['tests'][0]['results'][0]['steps'] = {}
        with self.assertRaises(adapters.AdapterError):
            self.parse('playwright', report)

    def test_junit_namespaced_and_extension_formats_are_explicitly_unsupported(self):
        for report in ('<testsuite xmlns="urn:custom"><testcase name="x"/></testsuite>',
                       '<testsuite><rerunFailure/></testsuite>'):
            with self.subTest(report=report):
                with self.assertRaises(adapters.AdapterError):
                    self.parse('junit', report)


if __name__ == '__main__':
    unittest.main()
