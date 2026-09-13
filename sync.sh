#!/usr/bin/env bash
# 从线上环境同步最新工作流到本仓库（单向：线上 → 仓库）。
# 用法：bash sync.sh，然后检查 diff，自行 commit + push。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for s in deep-deliberation auditing-skills project-deep-dive; do
  cp "$HOME/.cc-switch/skills/$s/SKILL.md" "$REPO/skills/$s/SKILL.md"
done
cp "$HOME/.claude/CLAUDE.md" "$REPO/AGENTS.md"

cd "$REPO"
git add -A
git status --short
echo "已同步。检查 diff 后 commit 并 push（push 需确认）。"
