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
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(HOME=str(self.home), CODEX_HOME=str(self.home / '.codex'),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
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

    def load_workflow(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('workflow', ROOT / 'scripts/workflow.py')
        wf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wf)
        return wf

    def test_fixture_isolates_inherited_git_environment(self):
        from unittest.mock import patch
        external_index = self.home / 'external-index'
        shutil.copy2(self.repo / '.git/index', external_index)
        before = external_index.read_bytes()
        nested = WorkflowTest('test_empty_and_repeat_install')
        self.addCleanup(nested.doCleanups)
        with patch.dict(os.environ, {'GIT_INDEX_FILE': str(external_index),
                                     'GIT_CONFIG_GLOBAL': str(self.home / 'outside-config')}):
            nested.setUp()
        self.assertEqual(external_index.read_bytes(), before,
                         'fixture modified an index outside its own repository')
        self.assertNotIn('GIT_INDEX_FILE', nested.env)
        self.assertEqual(nested.env['GIT_CONFIG_GLOBAL'], os.devnull)
        self.assertEqual(nested.env['GIT_CONFIG_NOSYSTEM'], '1')
        self.assertEqual(nested.git('rev-parse', '--show-toplevel').decode().strip(), str(nested.repo))

    def test_install_preserves_existing_temporary_directory(self):
        marker = self.source.parent / 'demo.tmp-install/user-data'
        self.put(marker, 'keep')
        self.bootstrap()
        self.assertTrue(marker.exists(), 'installer deleted an unowned directory')
        self.assertEqual(marker.read_text(), 'keep')

    def test_preview_rejects_source_mode_change(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        (self.source / 'SKILL.md').chmod(0o755)
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'skill\n')

    def test_preview_rejects_committed_target_changes(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        self.put(self.repo / 'skills/demo/SKILL.md', 'new committed work')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'target change')
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'new committed work')

    def test_preview_rejects_target_mode_change(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        self.git('config', 'core.filemode', 'false')
        (self.repo / 'skills/demo/SKILL.md').chmod(0o600)
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').stat().st_mode & 0o777, 0o600)

    def test_preview_rejects_removed_planned_path(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.put(self.source / 'assets/new.txt', 'new')
        self.run_script('sync')
        (self.source / 'assets/new.txt').unlink()
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'skill\n')

    def test_preview_rejects_partial_plan_becoming_noop(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.put(self.home / '.claude/CLAUDE.md', 'changed rules')
        self.run_script('sync')
        self.put(self.source / 'SKILL.md', 'skill\n')
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual((self.repo / 'AGENTS.md').read_text(), 'rules\n')

    def test_successful_apply_can_be_repeated(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        self.run_script('sync', '--apply')
        self.assertIn('unchanged:', self.run_script('sync', '--apply'))

    def test_preview_cannot_be_reused_in_another_repository(self):
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        other = self.repo.parent / 'other-repo'
        shutil.copytree(self.repo, other)
        self.repo = other
        self.run_script('sync', '--apply', ok=False)
        self.assertEqual((other / 'skills/demo/SKILL.md').read_text(), 'skill\n')

    def test_manifest_links_rejected_without_overwriting_unrelated_file(self):
        self.bootstrap()
        manifest = self.home / '.local/state/agent-workflow/preview-manifest.json'
        manifest.parent.mkdir(parents=True)
        unrelated = self.home / 'unrelated'
        unrelated.write_text('keep')
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    manifest.symlink_to(unrelated)
                else:
                    os.link(unrelated, manifest)
                try:
                    self.run_script('sync', ok=False)
                    self.assertEqual(unrelated.read_text(), 'keep')
                finally:
                    manifest.unlink()
                    unrelated.write_text('keep')

    def test_atomic_write_preserves_unowned_temp_and_cleans_failed_temp(self):
        from unittest.mock import patch
        wf = self.load_workflow()
        dst = self.home / 'target'
        dst.write_text('old')
        unrelated = self.home / 'unrelated'
        unrelated.write_text('keep')
        predictable = self.home / f'.target.tmp-{os.getpid()}'
        predictable.symlink_to(unrelated)
        before = set(self.home.iterdir())
        with patch.object(wf.os, 'replace', side_effect=OSError('injected replace failure')):
            with self.assertRaises(OSError):
                wf.atomic_write(dst, b'new', 0o644)
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(dst.read_text(), 'old')
        self.assertEqual(set(self.home.iterdir()), before)
        self.assertTrue(predictable.is_symlink())

    def test_rollback_failure_still_restores_other_paths_and_reports_backup(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        import io
        wf = self.load_workflow()
        self.bootstrap()
        self.put(self.source / 'SKILL.md', 'changed')
        self.put(self.source / 'assets/new.txt', 'new')
        self.put(self.home / '.claude/CLAUDE.md', 'changed rules')
        self.run_script('sync')
        original = wf.atomic_write
        def fail_rules(dst, data, mode):
            if dst == self.repo / 'AGENTS.md':
                raise OSError('injected write failure')
            return original(dst, data, mode)
        original_unlink = Path.unlink
        def fail_new_unlink(path, *args, **kwargs):
            if path == self.repo / 'skills/demo/assets/new.txt':
                raise OSError('injected restore failure')
            return original_unlink(path, *args, **kwargs)
        output = io.StringIO()
        with patch.object(wf, 'atomic_write', side_effect=fail_rules), patch.object(Path, 'unlink', fail_new_unlink), redirect_stdout(output):
            self.assertEqual(wf.sync(self.repo, self.home, True), 1)
        self.assertEqual((self.repo / 'skills/demo/SKILL.md').read_text(), 'skill\n')
        self.assertTrue((self.repo / 'skills/demo/assets/new.txt').exists())
        self.assertIn('rollback failed:', output.getvalue())
        self.assertIn('restored:', output.getvalue())
        self.assertIn('备份在', output.getvalue())
        self.assertNotIn('已回滚本次已写文件', output.getvalue())

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
            (good_src, good_dst, b'old-good', b'new-good', 0o644, 0o644),
            (bad_src, bad_parent / 'never', None, b'new-bad', None, 0o644),
        ]
        with self.assertRaises(OSError):
            wf.write_all(writes)
        self.assertEqual(good_dst.read_bytes(), b'old-good')  # 已回滚
        self.assertFalse((bad_parent / 'never').exists())

    def test_backup_pruning_preserves_unowned_directories(self):
        self.bootstrap()
        backup_root = self.home / '.local/state/agent-workflow/backups'
        markers = []
        for i in range(10):
            marker = backup_root / f'sync-user-{i}' / 'user-data'
            self.put(marker, 'keep')
            markers.append(marker)
        self.put(self.source / 'SKILL.md', 'changed')
        self.run_script('sync')
        self.run_script('sync', '--apply')
        for marker in markers:
            self.assertTrue(marker.exists(), f'unowned backup directory removed: {marker}')
            self.assertEqual(marker.read_text(), 'keep')

    def test_failed_rules_install_leaves_no_partial_source_and_can_retry(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        import io
        self.bootstrap()
        rules = self.home / '.claude/CLAUDE.md'
        rules.unlink()
        (self.repo / 'AGENTS.md').chmod(0o640)
        wf = self.load_workflow()
        with patch.dict(os.environ, self.env), patch.object(wf.os, 'chmod', side_effect=OSError('injected mode failure')), patch.object(wf.os, 'fchmod', side_effect=OSError('injected mode failure')), redirect_stdout(io.StringIO()):
            self.assertEqual(wf.install(self.repo, self.home), 1)
        self.assertFalse(rules.exists(), 'failed install published a partial editing source')
        self.assertEqual(list(rules.parent.glob('.CLAUDE.md.tmp-*')), [])
        self.run_script('install')
        self.assertEqual(rules.read_text(), 'rules\n')
        self.assertEqual(rules.stat().st_mode & 0o777, 0o640)

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
