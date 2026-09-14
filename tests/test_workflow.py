import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home, self.repo = base / 'home', base / 'repo'
        self.home.mkdir()
        self.repo.mkdir()
        (self.repo / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/workflow.py', self.repo / 'scripts/workflow.py')
        for name in ('install.sh', 'sync.sh'):
            shutil.copy2(ROOT / name, self.repo / name)
        self.put(self.repo / 'AGENTS.md', 'rules\n')
        self.put(self.repo / 'skills/demo/SKILL.md', 'skill\n')
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / '.codex'))
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'initial')
        self.source = self.home / '.cc-switch/skills/demo'

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], env=self.env)

    def run_script(self, command, *args, ok=True):
        result = subprocess.run(['bash', str(self.repo / (command + '.sh')), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return result.stdout

    def bootstrap(self):
        self.run_script('install')

    def snapshot(self):
        return {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}

    def test_empty_and_repeat_install(self):
        self.bootstrap()
        self.assertFalse(self.source.is_symlink())
        self.assertFalse((self.home / '.claude/CLAUDE.md').is_symlink())
        self.assertEqual((self.home / '.agents/skills/demo').resolve(), self.source)
        self.assertEqual((self.home / '.codex/AGENTS.md').resolve(), self.home / '.claude/CLAUDE.md')
        self.assertFalse((self.home / '.codex/skills').exists())
        self.assertNotIn('installed:', self.run_script('install'))

    def test_existing_sources_and_codex_skill_preserved(self):
        self.put(self.source / 'SKILL.md', 'local')
        self.put(self.home / '.claude/CLAUDE.md', 'local rules')
        self.put(self.home / '.codex/skills/untouched', 'keep')
        self.bootstrap()
        self.assertEqual((self.source / 'SKILL.md').read_text(), 'local')
        self.assertEqual((self.home / '.claude/CLAUDE.md').read_text(), 'local rules')
        self.assertEqual((self.home / '.codex/skills/untouched').read_text(), 'keep')

    def test_existing_host_entities_and_links_preserved(self):
        for variant in ('entity', 'different', 'broken'):
            with self.subTest(variant=variant):
                dst = self.home / '.agents/AGENTS.md'
                dst.parent.mkdir(exist_ok=True)
                if variant == 'entity':
                    dst.write_text('keep')
                else:
                    other = self.home / variant
                    if variant == 'different':
                        other.write_text('keep')
                    dst.symlink_to(other)
                self.run_script('install', ok=False)
                self.assertEqual(os.readlink(dst), str(other)) if dst.is_symlink() else self.assertEqual(dst.read_text(), 'keep')
                dst.unlink()

    def test_legacy_same_source(self):
        self.source.parent.mkdir(parents=True)
        self.source.symlink_to(self.repo / 'skills/demo')
        rules = self.home / '.claude/CLAUDE.md'
        rules.parent.mkdir()
        rules.symlink_to(self.repo / 'AGENTS.md')
        before = self.snapshot()
        self.assertIn('layout mismatch', self.run_script('install', ok=False))
        self.assertIn('same-source', self.run_script('sync', '--apply', ok=False))
        self.assertEqual(before, self.snapshot())

    def test_preview_apply_full_resources_and_backup(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed\n')
        for folder in ('scripts', 'references', 'assets', 'agents'):
            self.put(self.source / folder / 'nested/example.txt', folder)
        before = self.snapshot()
        out = self.run_script('sync')
        self.assertIn('NEW publication path', out)
        self.assertEqual(before, self.snapshot())
        index = (self.repo / '.git/index').read_bytes()
        self.run_script('sync', '--apply')
        self.assertEqual(index, (self.repo / '.git/index').read_bytes())
        for folder in ('scripts', 'references', 'assets', 'agents'):
            self.assertEqual((self.repo / 'skills/demo' / folder / 'nested/example.txt').read_text(), folder)
        backups = list((self.home / '.local/state/agent-workflow/backups').glob('*/skills/demo/SKILL.md'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'skill\n')

    def test_unrelated_dirty_allowed(self):
        self.bootstrap()
        self.put(self.repo / 'unrelated', 'dirty')
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync', '--apply')

    def test_related_dirty_prevents_all_writes(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.put(self.home / '.claude/CLAUDE.md', 'changed rules')
        self.put(self.repo / 'skills/demo/SKILL.md', 'repo change')
        before = self.snapshot()
        self.assertIn('git staged/unstaged', self.run_script('sync', '--apply', ok=False))
        self.assertEqual(before, self.snapshot())

    def test_staged_and_untracked_conflicts(self):
        self.bootstrap()
        self.put(self.source / 'scripts/new.py', 'new')
        self.put(self.repo / 'skills/demo/scripts/new.py', 'existing')
        before = self.snapshot()
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual(before, self.snapshot())
        self.git('add', 'skills/demo/scripts/new.py')
        before = self.snapshot()
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual(before, self.snapshot())

    def test_missing_source_zero_writes(self):
        self.bootstrap()
        self.put(self.home / '.claude/CLAUDE.md', 'changed rules')
        (self.source / 'SKILL.md').unlink()
        before = self.snapshot()
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual(before, self.snapshot())

    def test_target_extra_is_unresolved(self):
        self.bootstrap()
        self.put(self.repo / 'skills/demo/extra.txt', 'keep')
        self.put(self.source / 'SKILL.md', 'changed')
        before = self.snapshot()
        self.assertIn('unresolved', self.run_script('sync', '--apply', ok=False))
        self.assertEqual(before, self.snapshot())

    def test_source_inner_and_external_symlinks_rejected(self):
        self.bootstrap()
        for target in (self.source / 'SKILL.md', self.repo / 'AGENTS.md'):
            link = self.source / 'assets/link'
            link.parent.mkdir(exist_ok=True)
            link.symlink_to(target)
            before = self.snapshot()
            self.assertIn('symlink in skill tree', self.run_script('sync', '--apply', ok=False))
            self.assertEqual(before, self.snapshot())
            link.unlink()

    def test_source_root_symlink_rejected(self):
        self.bootstrap()
        alternate = self.home / 'other'
        self.source.rename(alternate)
        self.source.symlink_to(alternate)
        self.run_script('sync', '--apply', ok=False)

    def test_destination_symlink_and_type_conflict(self):
        self.bootstrap()
        self.put(self.source / 'assets/new.txt', 'new')
        target = self.repo / 'skills/demo/assets'
        target.symlink_to(self.home)
        self.run_script('sync', '--apply', ok=False)
        target.unlink()
        target.write_text('file')
        before = self.snapshot()
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual(before, self.snapshot())

    def test_unreviewed_and_cache_rejected(self):
        self.bootstrap()
        for path in ('extra.txt', 'scripts/__pycache__/a.pyc', 'assets/.env', 'assets/private.key'):
            file = self.source / path
            self.put(file, 'not publishable')
            self.assertIn('unreviewed publication path', self.run_script('sync', '--apply', ok=False))
            file.unlink()
            while file.parent != self.source and not list(file.parent.iterdir()):
                parent = file.parent
                parent.rmdir()
                file = parent

    def test_hardlinked_destination_preserves_unrelated_file(self):
        self.bootstrap()
        target = self.repo / 'skills/demo/SKILL.md'
        unrelated = self.home / 'unrelated.txt'
        os.link(target, unrelated)
        self.put(self.source / 'SKILL.md', 'changed')
        before = self.snapshot()
        self.assertIn('hard-linked', self.run_script('sync', '--apply', ok=False))
        self.assertEqual(unrelated.read_text(), 'skill\n')
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'root bypasses directory permission checks')
    def test_unreadable_resource_prevents_false_success(self):
        self.bootstrap()
        resource = self.source / 'references/hidden.md'
        self.put(resource, 'resource')
        resource.parent.chmod(0)
        self.addCleanup(resource.parent.chmod, 0o700)
        before = self.snapshot()
        self.assertIn('Preflight failed', self.run_script('sync', '--apply', ok=False))
        self.assertEqual(before, self.snapshot())

    def test_deleted_tracked_declaration_is_a_conflict(self):
        self.bootstrap()
        (self.repo / 'skills/demo/SKILL.md').unlink()
        self.put(self.source / 'SKILL.md', 'changed')
        self.put(self.home / '.claude/CLAUDE.md', 'changed rules')
        before = self.snapshot()
        self.assertIn('missing SKILL.md', self.run_script('sync', '--apply', ok=False))
        self.assertEqual(before, self.snapshot())

    def test_custom_codex_home(self):
        self.env['CODEX_HOME'] = str(self.home / 'custom-codex')
        self.bootstrap()
        self.assertTrue((self.home / 'custom-codex/AGENTS.md').is_symlink())
        self.assertFalse((self.home / '.codex').exists())


if __name__ == '__main__':
    unittest.main()
