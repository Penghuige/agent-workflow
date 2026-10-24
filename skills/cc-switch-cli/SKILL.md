---
name: cc-switch-cli
description: Use when 使用 cc-switch CLI 管理 agent skills，包括安装归位、启停同步、共享目录或软链接冲突、宿主可见性排查，以及写技能后的部署验证；不用于供应商、凭据、MCP、prompts 或代理管理。
compatibility: 需要 cc-switch 命令；不同版本以本机 --help 为准。
---

# cc-switch-cli

## 范围与对账

只处理 Skills 的本地管理。cc-switch 管理状态、文件系统入口和宿主实际发现是三层状态，
分别取证后再对账，不用其中一层代替另外两层。

## 流程

1. **定界**：确定技能目录名、来源路径、目标应用和允许动作。供应商、凭据、MCP、
   prompts、代理及其他 cc-switch 功能另找对应流程。
2. **预检**：运行 `cc-switch --version`、`cc-switch skills --help` 及本次子命令的
   `--help`。查询管理状态用 CLI，不直接读写 SQLite 或 settings。需要安装、启停、
   同步、更新或隔离验证时读 [命令与边界](references/commands.md)，并检查配置目录权限。
3. **只读对账**：用 `list` / `info` / `scan-unmanaged` 获取管理状态；比较实体文件的
   frontmatter、入口类型、realpath 和内容；再用宿主实际列表确认发现结果。数据库描述
   与文件不一致时分别记录，不擅自刷新或修改数据库。
4. **执行变更**：只在用户批准的技能和应用范围内操作。未在当前版本验证的命令先在
   隔离 HOME 与 `CC_SWITCH_CONFIG_DIR` 中实跑。导入、启用、同步是不同动作；没有
   dry-run 的命令不得称为预览。保留原实体和备份，不用覆盖或删除来消除冲突。
5. **验收**：再次核对管理状态、文件入口和宿主发现。需要新会话或重启才能刷新时，
   明确标记未验证；`Imported 0`、部分启用、链接缺失或描述不一致均不能称为成功。

## 写技能流程中的位置

本技能只负责部署对账，不替代新建前查重、文本清理或结构、触发、行为验证；这些步骤
由对应能力按 description 选择。发布到 Git 仓库仍走项目的 `sync.sh` 预览与应用；
`cc-switch skills sync` 是同步到应用目录，两者不可互换。

## 共享目录与符号链接

`~/.agents/skills` 可能被多个宿主直接读取，条目仍可绕过 cc-switch 的每应用开关。
区分共享目录、应用专属目录、符号链接和独立副本；同步后原实体可能仍存在，需另行对账。
当前 CLI 未提供 `--app kimi` 时，Kimi 只通过共享入口和实际会话验证，不虚构应用参数，
也不为布局整齐迁移全部技能。

## 停止条件

- 命令、参数或应用不受当前版本支持：停止猜测，改用已验证流程。
- 配置或 Skills 目录允许组/其他用户写入：在变更前停止并报告，不自动 chmod。
- 需要全局 sync、update、uninstall、storage-location、set-apps 或直接改数据库：
  先取得单独授权；自建技能不执行 `update --all`。
- 出现冲突、部分写入或目标已有实体：保留证据，只回滚本次明确创建的产物。
