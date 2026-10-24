# cc-switch Skills 命令与边界

验证基线为 `cc-switch 5.10.4`。先核对本机版本和 `--help`；版本不同则重新做隔离验证。

## 只读预检

按任务选用下列命令，不要求每次全部执行。

```bash
cc-switch --version
cc-switch skills --help
cc-switch skills list
cc-switch skills info <id>
cc-switch skills scan-unmanaged
cc-switch skills sync-method
cc-switch skills storage-location
```

`sync-method` 和 `storage-location` 不带值时才是查询。管理状态由 CLI 读取，不直接
打开数据库。将 `info` 的描述与实体 `SKILL.md` 比较，二者可能不同步。

同时检查配置根和 Skills 目录权限：

```bash
config_root="${CC_SWITCH_CONFIG_DIR:-$HOME/.cc-switch}"
stat -c '%a %U:%G %n' "$config_root" "$config_root/skills"
```

路径缺失或允许组/其他用户写入时，先停止变更并报告。不要自动放宽或收紧权限。

## 本地技能导入与定向启用

先把技能放入一个受支持应用的 Skills 目录，再运行：

```bash
cc-switch skills scan-unmanaged --app claude
cc-switch skills import-from-apps <id> --app claude
cc-switch skills info <id>
cc-switch skills enable <id> --app codex
```

- `<id>` 是技能目录名，不是绝对路径。5.10.4 中传绝对路径可能返回 `Imported 0`
  且退出码为 0；必须检查导入数量和 `info`，不能只看退出码。
- 只在 SSOT 中新建目录不会自动注册。`install` 面向可发现的仓库技能，本地导入名称
  不等于可安装名称；不要用它代替上述导入流程。
- `enable` 可创建所选应用的入口，但仍须检查管理矩阵、realpath 和实际文件。已有实体
  可能保留并成为独立副本，不能假定同步会把它转换为链接。
- 每个额外应用都单独核对 `enable --help` 和实际结果；Kimi 不在应用列表时，检查
  `~/.agents/skills/<id>/SKILL.md` 及新会话发现，不编造 `--app kimi`。

## 同步、更新与卸载

`cc-switch skills sync --app <app>` 同步该应用全部已启用技能，不是单技能操作，也没有
`--dry-run`。`update`、`uninstall`、`storage-location` 和 `set-apps` 同样先核对影响范围
并取得单独授权。自建技能以本机实体和项目 `sync.sh` 发布，不交给 `update --all` 覆盖。
`check-updates` 只代表检查，不能据其名称推断没有缓存或状态写入。

## 隔离验证

命令未在当前版本验证过时，先在临时目录运行，不碰真实配置：

```bash
sandbox="$(mktemp -d)"
install -d -m 700 "$sandbox/home" "$sandbox/config"
install -d -m 700 "$sandbox/home/.claude/skills" "$sandbox/home/.codex/skills"
HOME="$sandbox/home" CC_SWITCH_CONFIG_DIR="$sandbox/config" cc-switch skills list
```

按同样方式设置 fixture 与权限后运行目标子命令。`Imported 0`、权限错误、未注册或部分
写入都是停止信号；不要在生产环境连续尝试不同参数来碰运气。

## 三层验收

| 层 | 证据 |
|---|---|
| 管理状态 | `list` / `info` 中的目录名、description、应用矩阵 |
| 文件系统 | SSOT 与入口的实体/链接、realpath、内容差异、权限 |
| 宿主发现 | 新会话或宿主列表中的技能与 description，必要时一次无害调用 |

`~/.agents/skills` 是独立发现路径，不是应用开关的充分证据。原实体、插件缓存和同名技能
还要按来源区分；任何一层未验证都明确写出，不用“同步成功”代替端到端验收。
