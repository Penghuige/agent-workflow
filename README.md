# agent-workflow

个人 agent 工作流：自建 skill 库 + 全局规范（AGENTS.md）。设计目标：在任何一台新机器上，agent 读完本 README 即可自行完成安装配置。

## 内容

- `skills/deep-deliberation` —— 多视角对抗审议：高代价、卡住或难逆转决策的深化-冻结-多视角-批评-综合流程
- `skills/auditing-skills` —— skill 审查员：建后分级执行验证（Tier 0-3）、用后失败复盘、全库体检
- `skills/project-deep-dive` —— 项目设计深潜：方法考古、取舍还原、替代方法图谱，产出学习笔记
- `AGENTS.md` —— 全局个人规范：沟通 / 安全红线 / 验证习惯 / 任务闸门 / 文档纪律 / 工作流偏好
- `install.sh` —— 幂等安装脚本：只建符号链接，绝不删除或覆盖已有实体文件

## 安装（agent 按此执行）

```bash
git clone git@github.com:Penghuige/agent-workflow.git ~/github/agent-workflow
bash ~/github/agent-workflow/install.sh
```

install.sh 的行为：

1. 把 `skills/*` 链接进 `~/.cc-switch/skills/`（实体源约定）、`~/.agents/skills/`、`~/.claude/skills/`
2. 把 `AGENTS.md` 装为 `~/.claude/CLAUDE.md`，并让 `~/.agents/AGENTS.md` 指向它
3. 遇到已存在的实体目录/文件：跳过并提示，不做任何删除

装完后重开 agent 会话生效（技能清单是会话启动时生成的快照）。

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

两条纪律（详见装好的 AGENTS.md 工作流偏好）：

- `npx skills add` 默认落实体目录；装完按布局约定归位：实体移入 `~/.cc-switch/skills/`，`~/.agents/skills/` 与 `~/.claude/skills/` 建符号链接
- 建/装 skill 前遵守查重顺序：先查本地（含插件目录），再查网络，最后才新建；上游 skill 不在原文件里改

## 约定

- 本仓只收自建 skill 与全局规范；上游 skill 一律用安装命令引用
- 审议/学习笔记（`notes/`）不入库——它们是工作台产物，不是工作流
- 公开仓库：任何文件不得包含密钥、token、内网地址
