#!/usr/bin/env bash
# agent-workflow 安装脚本：幂等，只建符号链接，绝不删除或覆盖已有实体文件。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$HOME/.cc-switch/skills" "$HOME/.agents/skills" "$HOME/.claude/skills"

for s in "$REPO"/skills/*/; do
  name=$(basename "$s")
  for host in "$HOME/.cc-switch/skills" "$HOME/.agents/skills" "$HOME/.claude/skills"; do
    target="$host/$name"
    if [ -e "$target" ] && [ ! -L "$target" ]; then
      echo "跳过 $target：已存在实体目录，请人工处理后重跑" >&2
      continue
    fi
    ln -sfn "$REPO/skills/$name" "$target"
    echo "链接 $target"
  done
done

if [ -e "$HOME/.claude/CLAUDE.md" ] && [ ! -L "$HOME/.claude/CLAUDE.md" ]; then
  echo "注意：~/.claude/CLAUDE.md 是已有实体文件，未覆盖；请人工合并后重跑" >&2
else
  ln -sfn "$REPO/AGENTS.md" "$HOME/.claude/CLAUDE.md"
  echo "链接 ~/.claude/CLAUDE.md"
fi
mkdir -p "$HOME/.agents"
ln -sfn "$HOME/.claude/CLAUDE.md" "$HOME/.agents/AGENTS.md"
echo "链接 ~/.agents/AGENTS.md"

echo "完成。重开 agent 会话后生效（技能清单是会话启动时的快照）。"
