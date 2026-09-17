#!/usr/bin/env python3
"""Install local editing sources and preview/apply their publication copies."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

RESOURCE_DIRS = {'references', 'scripts', 'assets', 'agents'}
ROOT_FILES = {'SKILL.md', 'README.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt'}
PRIVATE_NAMES = {'.git', '__pycache__', '.pytest_cache', 'node_modules', 'memory', 'notes', 'logs', 'cache', '.DS_Store'}
CRED_WORDS = ('secret', 'secrets', 'credential', 'credentials', 'token', 'tokens')
GIT_STRIP = {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_PREFIX'}


def exists(path):
    return os.path.lexists(path)


def safe_parents(path):
    for parent in path.parents:
        if parent.is_symlink() or (exists(parent) and not parent.is_dir()):
            raise ValueError(f'unsafe parent: {parent}')


def regular(path):
    safe_parents(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'expected regular file: {path}')
    if path.stat().st_nlink > 1:
        raise ValueError(f'hard-linked file requires manual review: {path}')


def credential_name(name):
    stem = name.lower().rsplit('.', 1)[0]
    for sep in '._-':
        stem = stem.replace(sep, ' ')
    return any(word in CRED_WORDS for word in stem.split())


def ignorable(rel):
    return any(part in PRIVATE_NAMES or part.startswith('.') for part in rel.parts)


def tree(path, publication=True):
    safe_parents(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f'expected real skill directory (layout mismatch): {path}')
    files = {}
    def walk_error(error):
        raise error
    for root, dirs, names in os.walk(path, followlinks=False, onerror=walk_error):
        for name in sorted(dirs + names):
            item = Path(root) / name
            rel = item.relative_to(path)
            if item.is_symlink():
                raise ValueError(f'symlink in skill tree: {item}')
            mode = item.stat().st_mode
            if not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
                raise ValueError(f'unsupported path type: {item}')
            private = ignorable(rel)
            private |= item.suffix.lower() in {'.pyc', '.pyo', '.pem', '.key', '.log'}
            private |= credential_name(name)
            allowed = rel.parts[0] in RESOURCE_DIRS or (len(rel.parts) == 1 and name in ROOT_FILES and item.is_file())
            if publication and (private or not allowed):
                raise ValueError(f'unreviewed publication path: {item}')
            if item.is_file():
                files[rel] = item
    if Path('SKILL.md') not in files:
        raise ValueError(f'missing SKILL.md: {path}')
    return files


def git_env():
    env = {key: value for key, value in os.environ.items() if key not in GIT_STRIP}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    return env


def declared(repo):
    safe_parents(repo / 'skills' / 'placeholder')
    names = {p.parent.name for p in (repo / 'skills').glob('*/SKILL.md')}
    tracked = subprocess.run(
        ['git', '-C', str(repo), 'ls-files', '-z', '--', 'skills'],
        capture_output=True, env=git_env())
    if tracked.returncode == 0:
        for entry in tracked.stdout.split(b'\0'):
            parts = os.fsdecode(entry).split('/')
            if len(parts) == 3 and parts[0] == 'skills' and parts[2] == 'SKILL.md':
                names.add(parts[1])
    else:
        print('warning: git ls-files 失败，退回目录扫描（已跟踪但被删除的声明可能漏检）', file=sys.stderr)
    return sorted(names)


def report(state, path, detail=''):
    print(f'{state}: {path}' + (f' ({detail})' if detail else ''))


def resolve_codex_home(home):
    raw = os.environ.get('CODEX_HOME')
    if raw and raw.strip():
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError(f'CODEX_HOME must be an absolute path: {raw!r}')
        return path
    return home / '.codex'


def atomic_write(dst, data, mode):
    tmp = dst.parent / f'.{dst.name}.tmp-{os.getpid()}'
    tmp.write_bytes(data)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, dst)


def write_all(writes):
    written = []
    try:
        for src, dst, old, new, old_mode in writes:
            dst.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(dst, new, stat.S_IMODE(src.stat().st_mode))
            written.append((dst, old, old_mode))
            report('installed', dst)
    except OSError:
        for dst, old, old_mode in reversed(written):
            if old is None:
                dst.unlink(missing_ok=True)
            else:
                atomic_write(dst, old, old_mode)
        raise


def install(repo, home):
    failed = False
    codex_home = resolve_codex_home(home)
    def source(src, dst, directory=False):
        nonlocal failed
        try:
            safe_parents(dst)
            if dst.is_symlink():
                raise ValueError('layout mismatch: editing source must be a real file/directory; migrate manually')
            if exists(dst):
                if directory:
                    if not dst.is_dir():
                        raise ValueError('expected real skill directory')
                    s_files = tree(src)
                    d_files = tree(dst, publication=False)
                    missing = [str(rel) for rel in s_files if rel not in d_files]
                    if missing:
                        raise ValueError(f'incomplete editing source: missing {missing[0]}; 修复或删除后重跑')
                else:
                    regular(dst)
                report('unchanged', dst, 'existing editing source preserved')
                return True
            if directory:
                tree(src)
            else:
                regular(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if directory:
                tmp = dst.parent / (dst.name + '.tmp-install')
                shutil.rmtree(tmp, ignore_errors=True)
                try:
                    shutil.copytree(src, tmp)
                    os.replace(tmp, dst)
                except OSError:
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise
            else:
                shutil.copy2(src, dst)
            report('installed', dst)
            return True
        except (ValueError, OSError) as exc:
            failed = True
            report('layout mismatch' if 'layout mismatch' in str(exc) else 'conflict', dst, str(exc))
            return False
    def link(dst, src):
        nonlocal failed
        try:
            safe_parents(dst)
            if dst.is_symlink() and dst.exists() and dst.resolve() == src.resolve():
                report('unchanged', dst)
            elif exists(dst):
                raise ValueError('existing entity, different link, or broken link preserved')
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src)
                report('installed', dst)
        except (ValueError, OSError) as exc:
            failed = True
            report('conflict', dst, str(exc))
    for name in declared(repo):
        src = home / '.cc-switch/skills' / name
        if source(repo / 'skills' / name, src, True):
            for host in ('.agents/skills', '.claude/skills'):
                link(home / host / name, src)
    src = home / '.claude/CLAUDE.md'
    if source(repo / 'AGENTS.md', src):
        for dst in (home / '.agents/AGENTS.md', codex_home / 'AGENTS.md'):
            link(dst, src)
    print('Install incomplete; resolve reported conflicts.' if failed else 'Install complete.')
    return int(failed)


def sync(repo, home, apply):
    writes, errors, extras = [], [], []
    def compare(src, dst):
        regular(src)
        safe_parents(dst)
        old_mode = None
        if exists(dst):
            regular(dst)
            if os.path.samefile(src, dst):
                report('layout mismatch', dst, 'same source; no-op; migrate editing source manually')
                errors.append('same-source layout')
                return
            old_mode = stat.S_IMODE(dst.stat().st_mode)
        new = src.read_bytes()
        old = dst.read_bytes() if exists(dst) else None
        if old == new and old_mode == stat.S_IMODE(src.stat().st_mode):
            return
        writes.append((src, dst, old, new, old_mode))
    try:
        names = declared(repo)
        for name in names:
            src = home / '.cc-switch/skills' / name
            dst = repo / 'skills' / name
            if exists(src) and src.resolve() == dst.resolve():
                report('layout mismatch', src, 'same-source skill; no-op; migrate manually')
                errors.append(f'same-source layout: {name}')
                continue
            try:
                sources, targets = tree(src), tree(dst, publication=False)
                extras.extend(dst / rel for rel in targets.keys() - sources.keys() if not ignorable(rel))
                for rel, file in sources.items():
                    compare(file, dst / rel)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        src = home / '.claude/CLAUDE.md'
        dst = repo / 'AGENTS.md'
        if exists(src) and src.resolve() == dst.resolve():
            report('layout mismatch', src, 'same-source rules; no-op; migrate manually')
            errors.append('same-source rules layout')
        elif not exists(src):
            report('warning', src, '规则源文件缺失，本次跳过（技能同步不受影响）')
        else:
            try:
                compare(src, dst)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        for _, dst, _, _, _ in writes:
            result = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1', '--untracked-files=all', '--ignored', '--', str(dst.relative_to(repo))], capture_output=True, text=True, env=git_env())
            if result.returncode:
                errors.append('cannot inspect git state')
            elif result.stdout.strip():
                errors.append(f'git staged/unstaged/untracked/ignored conflict: {dst}')
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
    for src, dst, old, new, _ in writes:
        rel = dst.relative_to(repo)
        report('update' if old is not None else 'NEW publication path', rel)
        if old is not None and stat.S_IMODE(src.stat().st_mode) != stat.S_IMODE(dst.stat().st_mode):
            print(f'mode: {stat.S_IMODE(dst.stat().st_mode):o} -> {stat.S_IMODE(src.stat().st_mode):o}')
        try:
            print(''.join(difflib.unified_diff((old or b'').decode('utf-8').splitlines(True), new.decode('utf-8').splitlines(True), fromfile=f'repo/{rel}', tofile=f'local/{rel}')), end='')
        except UnicodeDecodeError:
            print(f'binary resource: {len(old or b"")} -> {len(new)} bytes')
    for path in extras:
        report('unresolved', path, 'target-only resource preserved; manual decision required')
    for error in errors:
        report('conflict', 'preflight', error)
    if errors or extras:
        print('Preflight failed; zero destination writes.')
        return 1
    state_dir = home / '.local/state/agent-workflow'
    safe_parents(state_dir / 'placeholder')
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = state_dir / 'preview-manifest.json'
    current = {str(dst.relative_to(repo)): hashlib.sha256(new).hexdigest() for _, dst, _, new, _ in writes}
    if not apply:
        manifest_path.write_text(json.dumps(current, indent=1))
        print(f'Preview only: {len(writes)} change(s), zero writes. Review this preview before --apply.')
        return 0
    print('--apply asserts that you approved the preview shown by sync.sh.')
    if not writes:
        print('unchanged: publication copies already match.')
        return 0
    saved = None
    if manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text())
        except (ValueError, OSError):
            saved = None
    if saved is None:
        report('conflict', 'apply', '没有找到预览记录；先运行 sync 预览再 --apply')
        return 1
    mismatched = [rel for rel, digest in current.items() if saved.get(rel) != digest]
    if mismatched:
        for rel in mismatched:
            report('conflict', rel, 'changed since preview; 请重新预览')
        return 1
    backup_root = home / '.local/state/agent-workflow/backups'
    safe_parents(backup_root / 'placeholder')
    if backup_root.resolve().is_relative_to(repo.resolve()):
        raise ValueError('backup directory must be outside repository')
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='sync-', dir=backup_root))
    stale = sorted((p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith('sync-')), key=lambda p: p.stat().st_mtime)
    for old_backup in stale[:-10]:
        shutil.rmtree(old_backup, ignore_errors=True)
    for _, dst, old, _, _ in writes:
        if old is not None:
            out = backup / dst.relative_to(repo)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, out)
    (backup / 'new-paths.txt').write_text(''.join(str(dst.relative_to(repo)) + '\n' for _, dst, old, _, _ in writes if old is None))
    report('backup', backup)
    try:
        write_all(writes)
    except OSError as exc:
        report('conflict', 'apply', f'{exc}；已回滚本次已写文件；备份在 {backup}')
        return 1
    manifest_path.write_text(json.dumps(current, indent=1))
    print('Applied; git index unchanged. Review git diff before committing.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'sync'))
    parser.add_argument('--apply', action='store_true', help='apply the preview you already reviewed and approved')
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        parser.error('需要 Python 3.9+')
    if args.command == 'install' and args.apply:
        parser.error('--apply is only supported for sync')
    repo = Path(__file__).resolve().parent.parent
    try:
        home = Path.home()
    except RuntimeError:
        parser.error('无法确定 HOME（未设置或不可解析）')
    if not str(home) or not home.is_absolute():
        parser.error(f'HOME 异常：{home!r}（需绝对路径）')
    home = home.resolve()
    try:
        return install(repo, home) if args.command == 'install' else sync(repo, home, args.apply)
    except (ValueError, OSError) as exc:
        report('conflict', args.command, str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
