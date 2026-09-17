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
        self.run_script('sync')
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

    def test_relative_codex_home_rejected(self):
        self.env['CODEX_HOME'] = 'relative/path'
        out = self.run_script('install', ok=False)
        self.assertIn('CODEX_HOME', out)

    def test_credential_name_matching(self):
        self.bootstrap()
        self.put(self.source / 'scripts/tokenizer.py', 'ok')
        self.run_script('sync')
        self.run_script('sync', '--apply')  # tokenizer 不应被凭据规则误伤
        self.assertEqual((self.repo / 'skills/demo/scripts/tokenizer.py').read_text(), 'ok')
        for bad in ('assets/token.json', 'assets/secrets.env', 'assets/my-secret.txt'):
            self.put(self.source / bad, 'x')
            self.assertIn('unreviewed publication path', self.run_script('sync', '--apply', ok=False))
            (self.source / bad).unlink()

    def test_preview_apply_binding(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'v1')
        self.run_script('sync')  # preview
        self.put(self.source / 'SKILL.md', 'v2-sneaked')  # preview 之后源被改
        before = self.snapshot()
        out = self.run_script('sync', '--apply', ok=False)
        self.assertIn('changed since preview', out)
        self.assertEqual(before, self.snapshot())
        self.run_script('sync')  # 重新 preview
        self.run_script('sync', '--apply')  # 内容一致后放行
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'v2-sneaked')

    def test_apply_rollback_on_mid_write_failure(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('workflow', ROOT / 'scripts/workflow.py')
        wf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wf)
        base = Path(self.tmp.name) / 'rb'
        base.mkdir()
        good_src = base / 'good-src'
        good_src.write_text('new-good')
        good_dst = base / 'good-dst'
        good_dst.write_text('old-good')
        bad_parent = base / 'not-a-dir'
        bad_parent.write_text('x')
        bad_src = base / 'bad-src'
        bad_src.write_text('new-bad')
        writes = [
            (good_src, good_dst, b'old-good', b'new-good', 0o644),
            (bad_src, bad_parent / 'never', None, b'new-bad', None),
        ]
        with self.assertRaises(OSError):
            wf.write_all(writes)
        self.assertEqual(good_dst.read_bytes(), b'old-good')  # 已回滚
        self.assertFalse((bad_parent / 'never').exists())

    def test_backup_pruning(self):
        self.bootstrap()
        for i in range(12):
            self.put(self.source / 'SKILL.md', f'v{i}')
            self.run_script('sync')
            self.run_script('sync', '--apply')
            self.git('add', '.')
            self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', f'v{i}')
        backups = list((self.home / '.local/state/agent-workflow/backups').glob('sync-*'))
        self.assertLessEqual(len(backups), 10)

    def test_target_cruft_does_not_block(self):
        self.bootstrap()
        self.put(self.repo / 'skills/demo/.DS_Store', 'cruft')
        self.put(self.repo / 'skills/demo/notes/scratch.md', 'cruft')
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        self.run_script('sync', '--apply')
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'changed')

    def test_missing_rules_file_does_not_block_skills(self):
        self.bootstrap()
        (self.home / '.claude/CLAUDE.md').unlink()
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        out = self.run_script('sync', '--apply')
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'changed')
        self.assertIn('CLAUDE.md', out)

    def test_existing_source_incomplete_is_conflict(self):
        self.bootstrap()
        self.put(self.repo / 'skills/demo/references/r.md', 'doc')  # 仓库侧多出文件，本地源没有
        out = self.run_script('install', ok=False)
        self.assertIn('incomplete', out)



if __name__ == '__main__':
    unittest.main()
