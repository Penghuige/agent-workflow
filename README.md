# agent-workflow

个人全局规范与四个自建 skill 的发布副本。**本机运行实体是日常编辑源，仓库用于审阅、版本管理和分发。** 上游套件只引用，不改写或打包进本仓。

## 维护边界

- `deep-deliberation`：高代价决策的独立视角与交叉批评；视角库、笔记契约按需读取。
- `auditing-skills`：技能体检、失败复盘与分级验证；标点整理、描述修改和流程变更采用不同检查。
- `project-deep-dive`：围绕学习靶心还原项目设计，复用已有证据和笔记。
- `code-audit`：存量代码的分镜头覆盖审查；机械化先扫 + 不变量清单 + 并行镜头 + 证据分级。
- `AGENTS.md`：跨项目的沟通、授权、验证和技能选择规则；项目特有约束仍由用户维护在项目内。

本次工作流调整依据 [OpenAI 关于 skills、AGENTS.md 与任务边界的建议](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)：减少无关常驻指令，按需读取资料，并明确何时继续、何时停下。不绑定特定模型，也不移除用户的安全与写入授权边界。

## 安装与本机布局

需要 Bash、Python 3.9+ 和 Git。安装不调用网络、包管理器或外部模型。

```bash
git clone git@github.com:Penghuige/agent-workflow.git ~/github/agent-workflow
bash ~/github/agent-workflow/install.sh
```

| 内容 | 本机编辑源 | 宿主入口 |
|---|---|---|
| 本仓四个 skills | `~/.cc-switch/skills/<name>/` 实体目录 | `~/.agents/skills/`、`~/.claude/skills/` 中的链接 |
| 全局规范 | `~/.claude/CLAUDE.md` 实体文件 | `~/.agents/AGENTS.md`、`$CODEX_HOME/AGENTS.md` 的链接；未设置 CODEX_HOME 时为 `~/.codex/AGENTS.md` |

安装仅在编辑源缺失时从仓库复制；已有编辑源保留，不用仓库旧版本覆盖本机工作。宿主已有同目标链接时不变；实体文件、不同目标链接、断链均保留并报告冲突。旧版安装若让编辑源链接回仓库，会报告布局不一致，不自动迁移。冲突返回非零，不能当作完整安装成功。

Codex 技能使用已有 `.agents/skills` 入口，不再新增 `.codex/skills` 重复入口；机器上既有的入口保持不变。Codex 全局规范加载方式见 [官方说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。安装成功后在新会话确认实际发现的技能和规范；已有会话可能保留旧的描述。

## 编辑与发布

1. 确认文档或技能改动的目标、范围和关键内容后，修改上述本机实体；普通定向修复不重新查整个技能生态。
2. 按 auditing-skills 验证受影响的行为。已有结果仍适用就复用，不反复全库体检。
3. 预览本机到仓库的差异，检查新增资源路径，再应用：

```bash
bash ~/github/agent-workflow/sync.sh
bash ~/github/agent-workflow/sync.sh --apply
```

`sync.sh` 默认零写入预览；`--apply` 表示已批准预览。仅同步仓库 `skills/*/SKILL.md` 已声明的自建技能和全局规范；Git 已跟踪但工作区缺失的声明仍会被检查并报冲突。技能资源允许根 `SKILL.md`、`README.md`、`LICENSE`/`LICENSE.md`/`LICENSE.txt`，以及 `references/`、`scripts/`、`assets/`、`agents/`。新增自建技能须先明确归属、批准加入仓库，不扫描并收录全部本机技能。

同步规则：

- 先检查全部来源、资源路径和将写入目标的 Git 状态；相关 staged/unstaged/untracked/ignored 冲突阻止整个应用，无关工作区改动不阻断。
- 不跟随技能树内的符号链接，写入目标含硬链接时停止；不可读资源目录按错误处理，拒绝未审核的根路径、隐藏文件、缓存及常见凭据文件名。路径规则不检测正文中的秘密；公开内容仍需在预览时人工/agent审阅。
- 目标独有文件保留并报告 unresolved；先决定其用途，不能把保留旧文件称为已完全同步。同源复制为 no-op，但会报告旧布局问题并返回非零。
- 写前备份全部将被覆盖的文件，记录新增路径，放在 `~/.local/state/agent-workflow/backups/sync-*`（只保留最近 10 份）。预检失败零目标写入；写入用临时文件原子替换，中途 I/O 故障自动回滚本次已写文件，备份仍可人工检查。
- `--apply` 要求内容与最近一次预览逐字节一致（preview-manifest 哈希校验）；预览后源文件再变化会被拒绝，需重新预览。
- 不执行 `git add`、commit 或 push。检查 `git diff` 后只暂存本任务文件；本地提交授权不包含 push。

仓库拉取更新后，不用 install 强制覆盖已有运行实体。先比较发布副本与本机改动，批准需要的合并范围后再更新本机；当前工具不提供无人值守的反向覆盖。

## 上游依赖（本仓引用而不维护的 skill）

```bash
npx skills add mvanhorn/last30days-skill@last30days -g -y
npx skills add vercel-labs/skills@find-skills -g -y
npx skills add addyosmani/agent-skills@context-engineering -g -y
npx skills add addyosmani/agent-skills@doubt-driven-development -g -y
```

论文写作套件（HKUSTDial/Supervisor-Skills），按需安装：

```bash
npx skills add HKUSTDial/Supervisor-Skills@paper-writer -g -y
npx skills add HKUSTDial/Supervisor-Skills@paper-polish -g -y
npx skills add HKUSTDial/Supervisor-Skills@pre-submission-reviewer -g -y
npx skills add HKUSTDial/Supervisor-Skills@intro-drafter -g -y
npx skills add HKUSTDial/Supervisor-Skills@tech-paper-template -g -y
npx skills add HKUSTDial/Supervisor-Skills@benchmark-paper-template -g -y
npx skills add HKUSTDial/Supervisor-Skills@idea-evaluator -g -y
npx skills add HKUSTDial/Supervisor-Skills@figure-designer -g -y
npx skills add HKUSTDial/Supervisor-Skills@drawio-reconstruction -g -y
npx skills add HKUSTDial/Supervisor-Skills@deep-research -g -y
npx skills add HKUSTDial/Supervisor-Skills@vibe-research-workflow -g -y
```

这些是按任务选择的上游能力，不是启动每个任务前必须装齐的依赖。其他宿主插件提供的 writing-skills、systematic-debugging、brainstorming 等只在可用且适合任务时使用；缺失时说明并使用当前宿主能力，不把 Kimi 插件路径当作跨平台必备。

## 任务表达与完成边界

任务请求说明目标、材料位置、不可改变的约束和完成标准即可。例如：“修复同步脚本遗漏辅助文件的问题，保留已有冲突文件；完成隔离测试、检查差异并本地提交，不 push。” 不要求先读全库或固定经过每一种 skill。

方案或文档写入范围获批后，在该范围内持续完成实现、验证、失败修复和交付。改变方向、明显扩大成本、删除既有产物或外发时重新确认。

## 验证与公开范围

```bash
bash -n install.sh sync.sh
python3 -m unittest discover -s tests -v
git diff --check
```

脚本测试使用临时 HOME 和 Git 仓库，不访问本机运行实体或生产系统。新机安装、重复安装、冲突、预览零写入、资源同步、备份和 Git index 保持均有对应样例；技能选择与行为验证另按 auditing-skills 执行，不能用脚本测试替代。

`/notes/` 与 Python 测试缓存不入库，备份位于仓库外。公开仓库不得包含密钥、token 或内网地址；忽略规则不等于发布审查。
