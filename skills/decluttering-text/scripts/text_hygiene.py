#!/usr/bin/env python3
"""Scan prose and agent-facing files for objective text-hygiene issues."""
import argparse
from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote, urlsplit

try:
    import yaml
except ImportError:
    yaml = None

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    line: int
    column: int
    message: str


MARKDOWN_SUFFIXES = {'.md', '.markdown', '.mdx'}
PROSE_SUFFIXES = {'.txt', '.rst', '.adoc', '.org', '.tex'}
CONFIG_SUFFIXES = {'.json', '.yaml', '.yml', '.toml'}
CODE_SUFFIXES = {
    '.py', '.sh', '.bash', '.zsh', '.fish', '.ts', '.tsx', '.js', '.jsx',
    '.css', '.scss', '.less', '.html', '.htm', '.xml', '.svg', '.ini',
    '.cfg', '.properties', '.env', '.java', '.kt', '.kts', '.go', '.rs',
    '.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.php', '.rb', '.swift',
    '.sql', '.proto', '.graphql', '.gql', '.ipynb', '.vue', '.svelte',
}
TEXT_SUFFIXES = MARKDOWN_SUFFIXES | PROSE_SUFFIXES | CONFIG_SUFFIXES | CODE_SUFFIXES

GENERATED_NAMES = {
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock',
    'Cargo.lock', 'go.sum', 'composer.lock', 'Gemfile.lock', 'Pipfile.lock',
    'bun.lockb', 'npm-shrinkwrap.json',
}
GENERATED_PATTERNS = (
    '*.min.js', '*.min.css', '*.map', '*.snap', '*.pb.go', '*_pb2.py',
    '*.generated.*', '*.g.dart', '*.freezed.dart',
)
SECRET_NAMES = {
    '.env', '.env.local', '.env.production', '.env.development',
    'id_rsa', 'id_ed25519', 'credentials', 'credentials.json', 'secrets.yaml',
}
SKIP_DIRS = {
    '.git', '.hg', '.svn', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.tox', '.nox', 'node_modules', 'vendor', 'third_party',
    'dist', 'build', 'target', 'coverage', '.venv', 'venv', 'site-packages',
    '.idea', '.vscode',
}
MAX_FILE_BYTES = 2_000_000
LONG_LINE_WIDTH = 120
DUPLICATE_MIN_CHARS = 80

FENCE = re.compile(r'^(?: {0,3}> ?)* {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$')
HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
REFERENCE_DEFINITION = re.compile(r'^ {0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))')
REFERENCE_LINK = re.compile(r'\[([^\]]+)\]\[([^\]]*)\]')
HTML_ID = re.compile(r'<a\s+[^>]*(?:id|name)=["\']([^"\']+)["\']', re.IGNORECASE)
GENERIC_LINK_TEXT = {
    'here', 'this', 'link', 'read more', 'click here', 'learn more',
    '详情', '这里', '点击这里', '了解更多', '点此', '链接',
}
AI_RESIDUE = re.compile(
    r'contentReference|\[cite:\s*\d+\]|grok_card|attached_file|:::writing',  # text-hygiene: ignore ai-residue
    re.IGNORECASE)
TRACKING_PARAM = re.compile(
    r'[?&](utm_source|utm_medium|utm_campaign|utm_term|utm_content)=',
    re.IGNORECASE)
INSTRUCTION_LIKE = re.compile(
    r'\b(ignore (all )?previous instructions|system prompt|you are now|'  # text-hygiene: ignore instruction-like
    r'do not tell the user|act as (a|an) )\b', re.IGNORECASE)  # text-hygiene: ignore instruction-like
SUPPRESS = re.compile(r'text-hygiene:\s*ignore([^\n]*)', re.IGNORECASE)
SUPPRESS_FILE = re.compile(
    r'^\s*(?:#|//|/\*|<!--|;|--|%)\s*text-hygiene:\s*ignore-file([^\n]*)',
    re.IGNORECASE)

BIDI_CONTROLS = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
BIDI_MARKS = {0x200E, 0x200F, 0x061C}
ZERO_WIDTH = {0x200B, 0x200C, 0xFEFF}
LINE_SEPARATORS = {0x2028, 0x2029}
EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x1F1E6, 0x1F1FF))


def split_lines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')


def display_width(line):
    width = 0
    for char in line:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
    return width


def is_emoji(char):
    codepoint = ord(char)
    return codepoint == 0xFE0F or any(start <= codepoint <= end for start, end in EMOJI_RANGES)


def emoji_adjacent(line, index):
    previous = line[index - 1] if index > 0 else ''
    following = line[index + 1] if index + 1 < len(line) else ''
    return (previous and is_emoji(previous)) or (following and is_emoji(following))


def frontmatter_findings(lines):
    if not lines or lines[0].strip() != '---':
        return set(), []
    for number in range(2, len(lines) + 1):
        if lines[number - 1].strip() in {'---', '...'}:
            findings = []
            if yaml is not None:
                try:
                    yaml.compose('\n'.join(lines[1:number - 1]))
                except yaml.YAMLError as exc:
                    mark = getattr(exc, 'problem_mark', None)
                    line = (mark.line + 2) if mark is not None else 1
                    column = (mark.column + 1) if mark is not None else 1
                    problem = getattr(exc, 'problem', None) or str(exc)
                    findings.append(Finding('frontmatter-yaml', 'error', line, column, problem))
            else:
                findings.append(Finding(
                    'frontmatter-yaml-unchecked', 'warning', 1, 1,
                    'PyYAML unavailable; frontmatter syntax not parsed'))
            return set(range(1, number + 1)), findings
    return set(range(1, len(lines) + 1)), [
        Finding('unclosed-frontmatter', 'error', 1, 1,
                'frontmatter opening --- is not closed')
    ]


def fence_state(lines, skip_lines=None):
    skip_lines = skip_lines or set()
    code_lines = set()
    findings = []
    open_fence = None
    for number, line in enumerate(lines, 1):
        if number in skip_lines:
            continue
        match = FENCE.match(line)
        if open_fence is None:
            if match:
                marker = match.group('marker')
                open_fence = (marker[0], len(marker), number)
                code_lines.add(number)
            continue
        code_lines.add(number)
        if match:
            marker = match.group('marker')
            if (marker[0] == open_fence[0] and len(marker) >= open_fence[1]
                    and not match.group('info').strip()):
                open_fence = None
    if open_fence is not None:
        findings.append(Finding(
            'unclosed-fence', 'error', open_fence[2], 1,
            f'code fence opened here is not closed ({open_fence[0] * open_fence[1]})'))
    return code_lines, findings


def line_ending_findings(text):
    crlf = text.count('\r\n')
    remainder = text.replace('\r\n', '')
    bare_cr = remainder.count('\r')
    bare_lf = remainder.count('\n')
    styles = []
    if crlf:
        styles.append('CRLF')
    if bare_lf:
        styles.append('LF')
    if bare_cr:
        styles.append('CR')
    if len(styles) < 2:
        return []
    return [Finding('mixed-line-endings', 'warning', 1, 1,
                    'mixed line endings: ' + ', '.join(styles))]


def trailing_whitespace_findings(lines, markdown, code_lines):
    findings = []
    for number, line in enumerate(lines, 1):
        stripped = line.rstrip(' \t')
        if stripped == line:
            continue
        trailing = line[len(stripped):]
        if markdown and number not in code_lines and len(trailing) >= 2 and stripped.strip():
            findings.append(Finding(
                'markdown-hard-break', 'warning', number, len(stripped) + 1,
                'two or more trailing spaces create a hard line break'))
        else:
            findings.append(Finding(
                'trailing-whitespace', 'warning', number, len(stripped) + 1,
                'trailing spaces or tabs'))
    return findings


def unicode_findings(lines):
    findings = []
    for line_number, line in enumerate(lines, 1):
        for index, char in enumerate(line):
            codepoint = ord(char)
            column = index + 1
            if codepoint in BIDI_CONTROLS:
                findings.append(Finding('bidi-control', 'error', line_number, column,
                                        f'invisible bidi control U+{codepoint:04X}'))
            elif codepoint in BIDI_MARKS:
                findings.append(Finding(
                    'bidi-mark', 'warning', line_number, column,
                    f'invisible bidi mark U+{codepoint:04X}; verify legitimate use'))
            elif 0xE0000 <= codepoint <= 0xE007F:
                findings.append(Finding('unicode-tag', 'error', line_number, column,
                                        f'invisible Unicode tag U+{codepoint:04X}'))
            elif codepoint == 0xFFFD:
                findings.append(Finding('replacement-char', 'error', line_number, column,
                                        'U+FFFD indicates lossy character decoding'))
            elif char == '\x00':
                findings.append(Finding('nul', 'error', line_number, column,
                                        'NUL is unsafe in Markdown and text tools'))
            elif codepoint in LINE_SEPARATORS:
                findings.append(Finding(
                    'line-separator', 'warning', line_number, column,
                    f'Unicode line separator U+{codepoint:04X}; parsers may treat it as a newline'))
            elif codepoint == 0x200D:
                if not emoji_adjacent(line, index):
                    findings.append(Finding(
                        'zero-width-format', 'warning', line_number, column,
                        'zero-width joiner outside an emoji sequence'))
            elif codepoint in ZERO_WIDTH:
                findings.append(Finding('zero-width-format', 'warning', line_number, column,
                                        f'zero-width format character U+{codepoint:04X}'))
            elif codepoint == 0x00A0:
                findings.append(Finding('nbsp', 'warning', line_number, column,
                                        'non-breaking space may be invisible to readers'))
            elif unicodedata.category(char) == 'Cf':
                findings.append(Finding('format-char', 'warning', line_number, column,
                                        f'Unicode format character U+{codepoint:04X}'))
            elif unicodedata.category(char) == 'Cc' and char not in '\t':
                findings.append(Finding('control-char', 'error', line_number, column,
                                        f'Unicode control character U+{codepoint:04X}'))
    return findings


def parse_findings(text, suffix):
    findings = []
    if suffix == '.json':
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            findings.append(Finding('json-parse', 'error', exc.lineno, exc.colno, exc.msg))
    elif suffix in {'.yaml', '.yml'}:
        if yaml is None:
            findings.append(Finding('yaml-unchecked', 'warning', 1, 1,
                                    'PyYAML unavailable; YAML syntax not parsed'))
        else:
            try:
                list(yaml.compose_all(text))
            except yaml.YAMLError as exc:
                mark = getattr(exc, 'problem_mark', None)
                line = (mark.line + 1) if mark is not None else 1
                column = (mark.column + 1) if mark is not None else 1
                problem = getattr(exc, 'problem', None) or str(exc)
                findings.append(Finding('yaml-parse', 'error', line, column, problem))
    elif suffix == '.toml' and tomllib is not None:
        try:
            tomllib.loads(text)
        except Exception as exc:
            findings.append(Finding('toml-parse', 'error', 1, 1, str(exc)))
    return findings


def mask_inline_code(line):
    characters = list(line)
    index = 0
    while index < len(line):
        if line[index] != '`':
            index += 1
            continue
        end = index
        while end < len(line) and line[end] == '`':
            end += 1
        run = end - index
        cursor = end
        while cursor < len(line):
            if line[cursor] != '`':
                cursor += 1
                continue
            closing_end = cursor
            while closing_end < len(line) and line[closing_end] == '`':
                closing_end += 1
            if closing_end - cursor == run:
                for position in range(index, closing_end):
                    characters[position] = ' '
                index = closing_end
                break
            cursor = closing_end
        else:
            index = end
    return ''.join(characters)


def parse_inline_links(line):
    links = []
    cursor = 0
    while True:
        close = line.find('](', cursor)
        if close == -1:
            break
        open_bracket = line.rfind('[', 0, close)
        if open_bracket == -1:
            cursor = close + 2
            continue
        text = line[open_bracket + 1:close]
        is_image = open_bracket > 0 and line[open_bracket - 1] == '!'
        index = close + 2
        while index < len(line) and line[index] in ' \t':
            index += 1
        if index >= len(line):
            break
        if line[index] == '<':
            end = line.find('>', index + 1)
            if end == -1:
                cursor = close + 2
                continue
            target = line[index + 1:end]
            end_index = end + 1
        else:
            start = index
            depth = 0
            while index < len(line):
                char = line[index]
                if char == '\\':
                    index += 2
                    continue
                if char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0:
                        break
                    depth -= 1
                index += 1
            if index >= len(line):
                cursor = close + 2
                continue
            target = line[start:index]
            end_index = index + 1
        title = re.match(r"\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\))", line[end_index:])
        if title:
            end_index += title.end()
        links.append((target.strip(), open_bracket + 1, is_image, text))
        cursor = end_index
    return links


def normalize_heading_text(text):
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[*_~]', '', text)
    text = re.sub(r'\s*\{#[^}]+\}\s*$', '', text)
    return text.strip()


def slugify(text, slugs):
    normalized = normalize_heading_text(text)
    base = re.sub(r'[^\w\- ]', '', normalized.lower(), flags=re.UNICODE).strip()
    base = re.sub(r'-+', '-', base.replace(' ', '-')).strip('-') or 'section'
    count = slugs.get(base, 0)
    slugs[base] = count + 1
    return base if count == 0 else f'{base}-{count}'


def heading_findings(lines, skip_lines):
    findings = []
    levels = []
    seen = {}
    slugs = {}
    fragments = set()
    for number, line in enumerate(lines, 1):
        if number in skip_lines:
            continue
        for match in HTML_ID.finditer(line):
            fragments.add(match.group(1))
        match = HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        text = match.group(2)
        if levels and level > levels[-1] + 1:
            findings.append(Finding(
                'heading-level-skip', 'warning', number, 1,
                f'heading level jumps from H{levels[-1]} to H{level}'))
        levels.append(level)
        normalized = normalize_heading_text(text).lower()
        key = (level, normalized)
        if key in seen:
            findings.append(Finding(
                'duplicate-heading', 'warning', number, 1,
                f'duplicate H{level} heading; first at line {seen[key]}'))
        else:
            seen[key] = number
        explicit = re.search(r'\{#([^}]+)\}\s*$', text)
        if explicit:
            fragments.add(explicit.group(1))
        fragments.add(slugify(text, slugs))
    return findings, fragments


def anchor_fragments(path, cache):
    if path in cache:
        return cache[path]
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        cache[path] = set()
        return cache[path]
    lines = split_lines(text)
    frontmatter_lines, _ = frontmatter_findings(lines)
    code_lines, _ = fence_state(lines, frontmatter_lines)
    _, fragments = heading_findings(lines, code_lines | frontmatter_lines)
    for line in lines:
        for match in HTML_ID.finditer(line):
            fragments.add(match.group(1))
    cache[path] = fragments
    return fragments


def check_target(target, line, column, base_dir, current_path, current_fragments, cache):
    findings = []
    target = target.strip()
    if not target:
        return findings
    if TRACKING_PARAM.search(target):
        findings.append(Finding('tracking-parameter', 'warning', line, column,
                                'tracking parameter in link target'))
    if target.startswith('#'):
        fragment = unquote(target[1:])
        if current_fragments is not None and fragment and fragment not in current_fragments:
            findings.append(Finding(
                'broken-anchor', 'warning', line, column,
                f'anchor does not exist in this file: #{fragment}'))
        return findings
    parsed = urlsplit(target)
    if parsed.scheme or target.startswith('//'):
        return findings
    file_part = unquote(parsed.path)
    if not file_part or re.match(r'^[A-Za-z]:[\\/]', file_part):
        return findings
    if base_dir is None:
        return findings
    candidate = Path(file_part) if Path(file_part).is_absolute() else base_dir / file_part
    if not candidate.exists():
        findings.append(Finding('broken-relative-link', 'warning', line, column,
                                f'link target does not exist: {target}'))
        return findings
    if parsed.fragment and candidate.suffix.lower() in MARKDOWN_SUFFIXES:
        if current_path is not None and candidate.resolve() == current_path.resolve():
            fragments = current_fragments or set()
        else:
            fragments = anchor_fragments(candidate, cache)
        fragment = unquote(parsed.fragment)
        if fragment not in fragments:
            findings.append(Finding(
                'broken-anchor', 'warning', line, column,
                f'anchor does not exist in {candidate.name}: #{fragment}'))
    return findings


def normalize_label(text):
    return ' '.join(text.strip().lower().split())


def link_findings(lines, skip_lines, base_dir, current_path, current_fragments):
    findings = []
    cache = {}
    definitions = {}
    for number, line in enumerate(lines, 1):
        if number in skip_lines:
            continue
        masked = mask_inline_code(line)
        for match in REFERENCE_DEFINITION.finditer(masked):
            label = normalize_label(match.group(1))
            target = match.group(2) or match.group(3) or ''
            if label.startswith('^') or not any(marker in target for marker in ('.', '/', '#', ':')):
                continue
            definitions[label] = (target, number, match.start(1) + 1)
        for target, column, is_image, text in parse_inline_links(masked):
            if is_image and not text.strip():
                findings.append(Finding('missing-image-alt', 'warning', number, column,
                                        'image has empty alt text'))
            elif not is_image and normalize_label(text) in GENERIC_LINK_TEXT:
                findings.append(Finding(
                    'generic-link-text', 'warning', number, column,
                    f'link text is not descriptive: {text.strip()!r}'))
            findings.extend(check_target(
                target, number, column, base_dir, current_path, current_fragments, cache))
        for match in REFERENCE_LINK.finditer(masked):
            label = normalize_label(match.group(2) or match.group(1))
            if label not in definitions:
                findings.append(Finding(
                    'unresolved-reference', 'warning', number, match.start() + 1,
                    f'reference label is not defined: {label}'))
    for target, number, column in definitions.values():
        findings.extend(check_target(
            target, number, column, base_dir, current_path, current_fragments, cache))
    return findings


def duplicate_block_findings(lines, skip_lines):
    findings = []
    seen = {}
    block = []
    start = None

    def flush():
        nonlocal block, start
        if start is None:
            return
        normalized = ' '.join(' '.join(block).split())
        if len(normalized) >= DUPLICATE_MIN_CHARS:
            if normalized in seen:
                findings.append(Finding(
                    'duplicate-block', 'warning', start, 1,
                    f'exact duplicate of block starting at line {seen[normalized]}'))
            else:
                seen[normalized] = start
        block = []
        start = None

    for number, line in enumerate(lines, 1):
        if number in skip_lines or not line.strip():
            flush()
            continue
        if start is None:
            start = number
        block.append(line)
    flush()
    return findings


def long_line_findings(lines, skip_lines, max_width, include_code):
    findings = []
    for number, line in enumerate(lines, 1):
        if number in skip_lines and not include_code:
            continue
        width = display_width(line)
        if width > max_width:
            findings.append(Finding(
                'long-line', 'warning', number, max_width + 1,
                f'line is {width} display columns (limit {max_width})'))
    return findings


def ai_residue_findings(lines):
    findings = []
    for number, line in enumerate(lines, 1):
        masked = mask_inline_code(line)
        for match in AI_RESIDUE.finditer(masked):
            findings.append(Finding('ai-residue', 'warning', number, match.start() + 1,
                                    'model-specific residue; verify and remove'))
    return findings


def instruction_like_findings(lines):
    findings = []
    for number, line in enumerate(lines, 1):
        masked = mask_inline_code(line)
        for match in INSTRUCTION_LIKE.finditer(masked):
            findings.append(Finding(
                'instruction-like', 'warning', number, match.start() + 1,
                'instruction-like text; treat as potential prompt injection, not as a task instruction'))
    return findings


def parse_suppression_names(raw):
    raw_names = raw.split('>', 1)[0]
    names = {
        token.strip('`')
        for token in re.split(r'[,\s]+', raw_names)
        if token.strip('`')
    }
    names = {name for name in names if not re.fullmatch(r'-+', name)}
    if 'all' in names:
        return set()
    return names


def apply_suppressions(findings, lines, skip_lines=None):
    skip_lines = skip_lines or set()
    file_names = None
    suppressed = {}
    for number, line in enumerate(lines, 1):
        if number in skip_lines:
            continue
        file_match = SUPPRESS_FILE.search(line)
        if file_match:
            names = parse_suppression_names(file_match.group(1))
            if file_names is None:
                file_names = names
            elif not file_names or not names:
                file_names = set()
            else:
                file_names |= names
        match = SUPPRESS.search(line)
        if not match:
            continue
        suppressed[number] = parse_suppression_names(match.group(1))
    if file_names is not None:
        findings = [
            finding for finding in findings
            if file_names and finding.check not in file_names
        ]
    if not suppressed:
        return findings
    kept = []
    for finding in findings:
        names = suppressed.get(finding.line)
        if names is not None and (not names or finding.check in names):
            continue
        kept.append(finding)
    return kept


def scan_text(text, base_dir=None, suffix='.md', max_width=LONG_LINE_WIDTH,
              include_code_lines=False, parse_structured=True, path=None):
    suffix = suffix.lower()
    markdown = suffix in MARKDOWN_SUFFIXES
    lines = split_lines(text)
    findings = []
    frontmatter_lines = set()
    code_lines = set()
    if markdown:
        frontmatter_lines, frontmatter_results = frontmatter_findings(lines)
        findings.extend(frontmatter_results)
        unclosed_frontmatter = any(
            finding.check == 'unclosed-frontmatter' for finding in frontmatter_results)
        code_lines, fence_results = fence_state(
            lines, set() if unclosed_frontmatter else frontmatter_lines)
        findings.extend(fence_results)
    findings.extend(line_ending_findings(text))
    findings.extend(trailing_whitespace_findings(lines, markdown, code_lines))
    findings.extend(unicode_findings(lines))
    if markdown:
        heading_results, fragments = heading_findings(lines, code_lines | frontmatter_lines)
        findings.extend(heading_results)
        findings.extend(link_findings(
            lines, code_lines | frontmatter_lines, base_dir,
            Path(path) if path is not None else None, fragments))
        findings.extend(duplicate_block_findings(lines, code_lines | frontmatter_lines))
        findings.extend(long_line_findings(
            lines, code_lines | frontmatter_lines, max_width, include_code_lines))
    elif suffix in PROSE_SUFFIXES:
        findings.extend(duplicate_block_findings(lines, set()))
        findings.extend(long_line_findings(lines, set(), max_width, include_code_lines))
    findings.extend(ai_residue_findings(lines))
    findings.extend(instruction_like_findings(lines))
    if parse_structured and suffix in CONFIG_SUFFIXES:
        findings.extend(parse_findings(text, suffix))
    return apply_suppressions(findings, lines, code_lines | frontmatter_lines)


def scan_file(path, max_width=LONG_LINE_WIDTH, include_code_lines=False,
              parse_structured=True, explicit=False):
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [Finding('read-error', 'error', 0, 0, str(exc))]
    if len(data) > MAX_FILE_BYTES:
        return [Finding('file-too-large', 'warning', 0, 0,
                        f'file is {len(data)} bytes; skipped (limit {MAX_FILE_BYTES})')]
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        if b'\x00' in data[:1024]:
            return [Finding('binary-skip', 'warning', 0, 0, 'binary content; skipped')]
        if not explicit:
            return []
        suffix = '.txt'
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        return [Finding('decode-error', 'error', 0, 0, f'not valid UTF-8: {exc}')]
    return scan_text(
        text, base_dir=path.parent, suffix=suffix, max_width=max_width,
        include_code_lines=include_code_lines,
        parse_structured=parse_structured, path=path)


def is_generated(path):
    return (path.name in GENERATED_NAMES or path.name.endswith('.lock')
            or any(fnmatch.fnmatch(path.name, pattern) for pattern in GENERATED_PATTERNS))


def is_excluded(path, root, patterns):
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.as_posix(), pattern)
               for pattern in patterns)


def scan_targets(path, excludes=None, include_generated=False, errors=None):
    excludes = excludes or []
    if not path.is_dir():
        yield path
        return

    def onerror(exc):
        if errors is None:
            return
        target = Path(exc.filename) if getattr(exc, 'filename', None) else path
        errors.append((target, Finding('read-error', 'error', 0, 0, str(exc))))

    for root, dirs, files in os.walk(path, followlinks=False, onerror=onerror):
        root_path = Path(root)
        dirs[:] = sorted(
            directory for directory in dirs
            if directory not in SKIP_DIRS
            and not is_excluded(root_path / directory, path, excludes))
        for name in sorted(files):
            item = root_path / name
            if item.is_symlink():
                continue
            if name.startswith('.') or name in SECRET_NAMES or name.startswith('.env'):
                continue
            if is_excluded(item, path, excludes):
                continue
            if not include_generated and is_generated(item):
                continue
            if item.suffix.lower() not in TEXT_SUFFIXES:
                continue
            yield item


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict', action='store_true',
                        help='fail on warnings as well as errors')
    parser.add_argument('--fail-on', choices=['error', 'warning', 'never'], default=None)
    parser.add_argument('--exclude', action='append', default=[],
                        help='glob to skip, relative to each scan root')
    parser.add_argument('--max-line-width', type=int, default=LONG_LINE_WIDTH)
    parser.add_argument('--include-code-lines', action='store_true')
    parser.add_argument('--include-generated', action='store_true')
    parser.add_argument('--no-parse', action='store_true')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args(argv)
    fail_on = args.fail_on or ('warning' if args.strict else 'error')
    findings = []
    walk_errors = []
    for path in args.paths:
        if path.is_file():
            targets = [path]
            explicit = True
        else:
            targets = scan_targets(
                path, args.exclude, args.include_generated, walk_errors)
            explicit = False
        for target in targets:
            for finding in scan_file(
                    target, max_width=args.max_line_width,
                    include_code_lines=args.include_code_lines,
                    parse_structured=not args.no_parse, explicit=explicit):
                findings.append((target, finding))
    findings.extend(walk_errors)
    findings.sort(key=lambda item: (
        str(item[0]), item[1].line, item[1].column, item[1].severity, item[1].check))
    if args.json:
        if not args.quiet:
            print(json.dumps([
                {'path': str(path), 'line': finding.line, 'column': finding.column,
                 'severity': finding.severity, 'check': finding.check,
                 'message': finding.message}
                for path, finding in findings
            ], ensure_ascii=False, indent=2))
    elif not args.quiet:
        for path, finding in findings:
            print(f'{path}:{finding.line}:{finding.column}: {finding.severity}: '
                  f'{finding.check}: {finding.message}')
    if args.summary:
        errors = sum(1 for _, finding in findings if finding.severity == 'error')
        warnings = sum(1 for _, finding in findings if finding.severity == 'warning')
        print(f'summary: {errors} error(s), {warnings} warning(s)', file=sys.stderr)
        counts = {}
        for _, finding in findings:
            counts[finding.check] = counts.get(finding.check, 0) + 1
        for check in sorted(counts):
            print(f'{check}: {counts[check]}', file=sys.stderr)
    if fail_on == 'never':
        return 0
    if fail_on == 'warning':
        return 1 if findings else 0
    return 1 if any(finding.severity == 'error' for _, finding in findings) else 0


if __name__ == '__main__':
    raise SystemExit(main())
