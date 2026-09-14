#!/usr/bin/env python3
"""Install local editing sources and preview/apply their publication copies."""
import argparse
import difflib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

RESOURCE_DIRS = {'references', 'scripts', 'assets', 'agents'}
ROOT_FILES = {'SKILL.md', 'README.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt'}
PRIVATE_NAMES = {'.git', '__pycache__', '.pytest_cache', 'node_modules', 'memory', 'notes', 'logs', 'cache', '.DS_Store'}


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
            private = any(p in PRIVATE_NAMES or p.startswith('.') for p in rel.parts)
            private |= item.suffix.lower() in {'.pyc', '.pyo', '.pem', '.key', '.log'}
            private |= any(word in name.lower() for word in ('secret', 'credential', 'token'))
            allowed = rel.parts[0] in RESOURCE_DIRS or (len(rel.parts) == 1 and name in ROOT_FILES and item.is_file())
            if publication and (private or not allowed):
                raise ValueError(f'unreviewed publication path: {item}')
            if item.is_file():
                files[rel] = item
    if Path('SKILL.md') not in files:
        raise ValueError(f'missing SKILL.md: {path}')
    return files


def declared(repo):
    safe_parents(repo / 'skills' / 'placeholder')
    names = {p.parent.name for p in (repo / 'skills').glob('*/SKILL.md')}
    tracked = subprocess.run(
        ['git', '-C', str(repo), 'ls-files', '-z', '--', 'skills'],
        capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    if tracked.returncode == 0:
        for entry in tracked.stdout.split(b'\0'):
            parts = os.fsdecode(entry).split('/')
            if len(parts) == 3 and parts[0] == 'skills' and parts[2] == 'SKILL.md':
                names.add(parts[1])
    return sorted(names)


def report(state, path, detail=''):
    print(f'{state}: {path}' + (f' ({detail})' if detail else ''))


def install(repo, home):
    failed = False
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
                    regular(dst / 'SKILL.md')
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
                shutil.copytree(src, dst)
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
        for dst in (home / '.agents/AGENTS.md', Path(os.environ.get('CODEX_HOME', str(home / '.codex'))) / 'AGENTS.md'):
            link(dst, src)
    print('Install incomplete; resolve reported conflicts.' if failed else 'Install complete.')
    return int(failed)


def sync(repo, home, apply):
    writes, errors, extras = [], [], []
    def compare(src, dst):
        regular(src)
        safe_parents(dst)
        if exists(dst):
            regular(dst)
            if os.path.samefile(src, dst):
                report('layout mismatch', dst, 'same source; no-op; migrate editing source manually')
                errors.append('same-source layout')
                return
        new = src.read_bytes()
        old = dst.read_bytes() if exists(dst) else None
        if old == new and stat.S_IMODE(src.stat().st_mode) == stat.S_IMODE(dst.stat().st_mode):
            return
        writes.append((src, dst, old, new))
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
                extras.extend(dst / rel for rel in targets.keys() - sources.keys())
                for rel, file in sources.items():
                    compare(file, dst / rel)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        src = home / '.claude/CLAUDE.md'
        dst = repo / 'AGENTS.md'
        if exists(src) and src.resolve() == dst.resolve():
            report('layout mismatch', src, 'same-source rules; no-op; migrate manually')
            errors.append('same-source rules layout')
        else:
            try:
                compare(src, dst)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        for _, dst, _, _ in writes:
            result = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1', '--untracked-files=all', '--ignored', '--', str(dst.relative_to(repo))], capture_output=True, text=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
            if result.returncode:
                errors.append('cannot inspect git state')
            elif result.stdout.strip():
                errors.append(f'git staged/unstaged/untracked/ignored conflict: {dst}')
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
    for src, dst, old, new in writes:
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
    if not apply:
        print(f'Preview only: {len(writes)} change(s), zero writes. Review this preview before --apply.')
        return 0
    print('--apply asserts that you approved the preview shown by sync.sh.')
    if not writes:
        print('unchanged: publication copies already match.')
        return 0
    backup_root = home / '.local/state/agent-workflow/backups'
    safe_parents(backup_root / 'placeholder')
    if backup_root.resolve().is_relative_to(repo.resolve()):
        raise ValueError('backup directory must be outside repository')
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='sync-', dir=backup_root))
    for _, dst, old, _ in writes:
        if old is not None:
            out = backup / dst.relative_to(repo)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, out)
    (backup / 'new-paths.txt').write_text(''.join(str(dst.relative_to(repo)) + '\n' for _, dst, old, _ in writes if old is None))
    report('backup', backup)
    for src, dst, _, _ in writes:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        report('installed', dst)
    print('Applied; git index unchanged. Review git diff before committing.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'sync'))
    parser.add_argument('--apply', action='store_true', help='apply the preview you already reviewed and approved')
    args = parser.parse_args()
    if args.command == 'install' and args.apply:
        parser.error('--apply is only supported for sync')
    repo = Path(__file__).resolve().parent.parent
    home = Path.home()
    try:
        return install(repo, home) if args.command == 'install' else sync(repo, home, args.apply)
    except (ValueError, OSError) as exc:
        report('conflict', args.command, str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
