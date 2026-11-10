"""Optional offline browser regression; requires Playwright and a local Chromium."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright is unavailable')
class DashboardBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import Error, sync_playwright

        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        executable = shutil.which('chromium') or shutil.which('google-chrome')
        options = {'headless': True}
        if executable:
            options['executable_path'] = executable
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except Error as error:
            if 'Executable doesn\'t exist' in str(error):
                raise unittest.SkipTest('optional Chromium is unavailable')
            raise
        cls.addClassCleanup(cls.browser.close)

    def test_long_project_name_keeps_report_content_reachable_and_keyboard_readable(self):
        title = '超长项目名称 <img src=x> uninterrupted_project_name_' * 45
        document = {'schema_version': 1, 'project': {'name': title}, 'runs': []}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, output = base / 'results.json', base / 'index.html'
            source.write_text(json.dumps(document), encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(ROOT / 'skills/test-dashboard/scripts/render_dashboard.py'),
                 '--input', str(source), '--output', str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for width, height in ((390, 844), (1440, 1000)):
                with self.subTest(width=width, height=height):
                    page = self.browser.new_page(viewport={'width': width, 'height': height})
                    try:
                        page.goto(output.as_uri(), wait_until='networkidle')
                        heading = page.locator('#project-name')
                        self.assertEqual(heading.text_content(), title)
                        self.assertEqual(page.locator('header img').count(), 0)
                        geometry = page.evaluate('''() => ({
                            titleHeight: document.querySelector('#project-name').getBoundingClientRect().height,
                            methodsTop: document.querySelector('#test-methods').getBoundingClientRect().top,
                            pageWidth: document.documentElement.scrollWidth,
                            viewportWidth: document.documentElement.clientWidth
                        })''')
                        self.assertLess(geometry['titleHeight'], height * .25, geometry)
                        self.assertLess(geometry['methodsTop'], height * .8, geometry)
                        self.assertLessEqual(geometry['pageWidth'], geometry['viewportWidth'], geometry)
                        heading.focus()
                        page.keyboard.press('End')
                        page.wait_for_function(
                            "document.querySelector('#project-name').scrollTop > 0")
                        self.assertGreater(heading.evaluate('(el) => el.scrollTop'), 0)
                        # Normal names should still take only their natural single line.
                        heading.evaluate("el => { el.textContent='CampusQuest'; el.scrollTop=0; }")
                        normal = heading.evaluate('''el => ({height:el.getBoundingClientRect().height,
                            lineHeight:parseFloat(getComputedStyle(el).lineHeight),
                            scrollHeight:el.scrollHeight,clientHeight:el.clientHeight})''')
                        self.assertLessEqual(normal['height'], normal['lineHeight'] + 1)
                        self.assertLessEqual(normal['scrollHeight'], normal['clientHeight'])
                    finally:
                        page.close()

    def test_long_title_keeps_details_and_close_accessible_on_resize(self):
        # A sticky heading must leave room for evidence and remain keyboard-readable.
        title = '长标题 <img src=x> & uninterrupted_' * 30
        document = {
            'schema_version': 1, 'project': {'name': 'Synthetic browser regression'},
            'snapshot': {'template_sha256': 'a' * 64, 'generated_at': '2026-10-29T00:00:00Z'},
            'runs': [{'id': 'synthetic', 'label': 'Synthetic only', 'state': 'completed',
                      'inventory_known': True, 'cases': [
                          {'id': 'long', 'name': title, 'suite': 'UI', 'status': 'failed',
                           'error': 'Synthetic evidence'}]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, output = base / 'results.json', base / 'index.html'
            source.write_text(json.dumps(document), encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(ROOT / 'skills/test-dashboard/scripts/render_dashboard.py'),
                 '--input', str(source), '--output', str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for start, finish in [((390, 844), (390, 844)),
                                  ((1440, 1000), (390, 844)),
                                  ((390, 844), (1440, 1000))]:
                with self.subTest(start=start, finish=finish):
                    page = self.browser.new_page(viewport={'width': start[0], 'height': start[1]})
                    try:
                        page.route('http://**/*', lambda route: route.abort())
                        page.route('https://**/*', lambda route: route.abort())
                        page.goto(output.as_uri(), wait_until='networkidle')
                        self.assertIn('aaaaaaaaaaaa', page.locator('#project-meta').inner_text())
                        self.assertIn('2026-10-29T00:00:00Z', page.locator('#project-meta').inner_text())
                        page.locator('.case button').click()
                        page.set_viewport_size({'width': finish[0], 'height': finish[1]})
                        heading = page.locator('#details-title')
                        self.assertEqual(heading.text_content(), title)
                        self.assertEqual(page.locator('#details img').count(), 0)
                        geometry = page.evaluate('''() => ({
                            header: document.querySelector('.dialog-header').getBoundingClientRect().height,
                            dialog: document.querySelector('#details').getBoundingClientRect().height,
                            bodyTop: document.querySelector('#details-content').getBoundingClientRect().top
                        })''')
                        self.assertLess(geometry['header'], geometry['dialog'] * .5, geometry)
                        self.assertLess(geometry['bodyTop'], finish[1] * .6, geometry)
                        heading.focus()
                        page.keyboard.press('End')
                        page.wait_for_function(
                            "document.querySelector('#details-title').scrollTop > 0")
                        self.assertGreater(heading.evaluate('(el) => el.scrollTop'), 0)
                        page.locator('#details').evaluate('(el) => el.scrollTop = el.scrollHeight')
                        close = page.locator('#close-details')
                        bounds = close.bounding_box()
                        self.assertIsNotNone(bounds)
                        self.assertGreaterEqual(bounds['y'], 0)
                        self.assertLessEqual(bounds['y'] + bounds['height'], finish[1])
                        close.click()
                        self.assertFalse(page.locator('#details').is_visible())
                        self.assertTrue(page.locator('.case button').evaluate(
                            '(el) => el === document.activeElement'))
                    finally:
                        page.close()
