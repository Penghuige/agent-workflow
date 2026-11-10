# 测试结果与证据契约 v1

本契约连接测试执行端、项目适配器和面板。执行端产出真实结果，适配器只转换已知字段；
中央模板与渲染器负责组织、筛选和证据下钻。标准 Playwright/JUnit 报告可直接通过
[项目接入](project-setup.md) 构建；非标准输入由项目采集器输出本格式，以 `normalized`
来源合并。已有满足需求的原生报告继续保留，统一面板不替代其专有查看能力。

## 文档结构

```json
{
  "schema_version": 1,
  "project": {"name": "Example", "revision": "abc123", "dirty": false},
  "runs": [{
    "id": "web-local-attempt-0",
    "label": "浏览器流程",
    "track": "planned",
    "revision": "abc123",
    "environment": "local",
    "started_at": "2026-10-27T12:00:00Z",
    "state": "completed",
    "inventory_known": true,
    "source": "reports/playwright.json",
    "notes": [],
    "cases": [{
      "id": "profile.rename",
      "name": "修改个人信息后保存",
      "suite": "端到端测试",
      "status": "failed",
      "duration_ms": 1350,
      "expected": "显示保存成功，新昵称保持为新值",
      "actual": "接口返回500，页面提示保存失败",
      "error": "等待保存成功提示超时",
      "command": "复制项目已确认的复现命令；这里仅显示文本",
      "steps": [
        {"name": "打开个人信息", "status": "passed"},
        {"name": "输入新昵称", "status": "passed"},
        {"name": "保存并验证成功", "status": "failed",
         "expected": "显示保存成功", "actual": "显示保存失败",
         "error": "HTTP 500"}
      ],
      "attachments": [
        {"kind": "screenshot", "label": "失败截图", "path": "artifacts/rename.png"},
        {"kind": "trace", "label": "操作轨迹", "path": "artifacts/rename-trace.zip"},
        {"kind": "report", "label": "原生报告", "path": "playwright-report/index.html"}
      ]
    }]
  }]
}
```

必需项：`schema_version=1`、`project.name`、`runs`；每轮必需 `id`、`label`、
`state`、`inventory_known`、`cases`；每条用例必需 `id`、`name`、`suite`、`status`。
其余字段可缺省。缺省与空字符串均不能被补成成功、已采集或已覆盖。

## 计划测试与探索测试

- `track` 为 `planned`（默认）或 `exploratory`。两轨分别展示；共享的名称不代表相同
  范围，不能合并成一个完成率。
- 计划测试可来自 TDD/验收清单与实际 runner inventory。没有可信清单时，已收集结果
  仍可展示，但计划总量和开发完成度未知；测试代码存在也不等于本轮执行通过。
- 探索轮可附 `charter`（目标、范围及停止条件的简述）和 `seed`（实际使用的种子）。
  `inventory_known` 必须为 `false`；探索路径是本轮实际尝试的记录，不是完整测试全集。
  探索中的 `passed` 只表示本条流程按执行器判据完成，不能汇总为“项目全部通过”。
- 探索用例可附 `findings`。每项必需 `title`、`status`（`candidate` 或 `confirmed`）、
  `basis`（规范、断言、不变量或启发式判断依据）；可附 `expected`、`actual`、`severity`
  和 `attachments`。候选发现不会自动改变用例状态或变成已确认缺陷。
- 不理想但缺少明确判据的观察记 `candidate`，保留原始步骤、现象、证据和待确认点。
  `confirmed` 应有可核实的判据与复现证据；仅凭“感觉不好”不能确认测试失败。

探索用例示例（放入 `track=exploratory` 的 run）：

```json
{
  "id": "profile.long-name",
  "name": "个人信息：输入长昵称后保存",
  "suite": "探索：个人信息",
  "status": "passed",
  "steps": [
    {"name": "打开个人信息", "status": "passed"},
    {"name": "输入长昵称并保存", "status": "passed"}
  ],
  "findings": [{
    "title": "保存后昵称遮挡操作按钮",
    "status": "candidate",
    "basis": "启发式可用性观察；尚无明确长度和布局验收条款",
    "actual": "按钮被遮挡，需要截图和复现确认",
    "attachments": []
  }]
}
```

## 测试方法总览

文档顶层可选 `methods` 数组，用于展示 agent 根据项目事实考虑过的测试方法。每项
必需 `id`（数组内唯一）、`name`、`reason` 字符串；`state` 为 `present`、`suggested`、
`not_applicable`、`unknown`（默认）；可附 `category`、`evidence` 字符串及 `run_ids`
字符串数组，数组内不可重复。关联轮次必须在本文档 `runs` 中存在，尚无运行记录时可以不关联。

`present` 表示项目已配置该方法，需要当前配置或测试实现依据，不代表本轮已经执行或通过。
只有历史报告或探索记录时，配置状态保留 `unknown`，仍可用 `run_ids` 关联实际结果。
`suggested` 为建议补充；
`not_applicable` 为有依据的当前不适用；`unknown` 为尚未评估。状态与依据由项目适配
和评估提供，渲染器不替用户做决定。未提供 `methods` 或提供 `[]` 均不证明已考虑
全部测试方法；面板区分“已考虑”“已配置”和“存在实际运行证据”。

```json
{
  "methods": [{
    "id": "property",
    "name": "性质与边界输入测试",
    "category": "输入域",
    "state": "suggested",
    "reason": "项目包含双向格式转换，已有结果仅列少量示例",
    "evidence": "parser模块与当前已接入测试",
    "run_ids": []
  }]
}
```

## 测试缺口与建议

文档顶层可选 `gaps` 数组，与两条执行轨分开：轨道表示测试工作方式，`suite` 可以是
项目任何已有测试种类，不以固定测试分类枚举限制项目。

每项必需字符串 `title`、`reason`、`suggestion`；可附 `basis` 来源、`state`
（`suggested` 默认、`accepted`、`not_applicable`）和 `confidence`（`hypothesis`
默认、`evidence`）。内容来自实际项目风险/已有测试分析，已纳入或不适用状态来自
当前项目决策；渲染器不替用户接受建议，也不将建议变成失败测试。

```json
{
  "gaps": [{
    "title": "重复保存的并发行为未见证据",
    "reason": "写入共享记录，现有结果仅覆盖单次顺序保存",
    "suggestion": "评估两个请求同时保存时的约束与幂等性测试",
    "basis": "当前共享写路径与已接入结果；尚未覆盖全库测试",
    "state": "suggested",
    "confidence": "hypothesis"
  }]
}
```

未提供 `gaps` 表示没有提供缺口评估；`[]` 只表示本次评估没有列出建议。
两者都不能证明覆盖充分。测试缺口不是已确认产品缺陷，不能合成风险通过率。

## 身份、范围与状态

- `run.id`、`case.id` 和方法 `id` 均不可为空或仅含空白；`run.id` 在文档内唯一，
  `case.id` 在本轮唯一。不同环境、批次、重试使用不同 run，
  保留各轮原始结果，不按用例显示名去重。面板跨轮统计必须标明统计的是结果记录。
- `state`：`completed`、`running`、`blocked`、`collection_error`、`not_integrated`。
  后三种表示执行或证据状态，不能冒充失败测试或合法跳过。
- 用例及步骤的 `status`：`passed`、`failed`、`skipped`、`not_run`。中途失败后未执行的
  步骤保持 `not_run`。没有步骤记录时显示“未采集步骤”，不从名称生成历史操作。
- `inventory_known=true` 表示适配器有可信的本轮用例清单，`cases` 必须包含本轮适用
  的全部用例；已知但没有匹配结果的项标 `not_run`。报告只包含实际执行结果时设为
  `false`；此时预期总数、未运行数及整体完成率未知。通过率不能当覆盖率或发布结论。
- `revision`、`environment`、时间和本轮范围共同约束结果适用性。revision 不同应显示
  版本不匹配；同 revision 仍可能有环境或时效限制，适配器用 `notes` 提供证据。
- `duration_ms` 为有限非负数；文本字段为字符串，`dirty` 为布尔值。无法解析的来源
  保持 `not_integrated` 或 `collection_error` 并写说明，不能用空成功轮替代。

## 步骤与附件

`expected` 来自规范或断言，`actual/error` 来自本次执行。保留失败步骤的顺序及证据；
不能把计划步骤当实际执行轨迹，也不能把录制轨迹的查看当成重新运行测试。

附件 `kind`：`screenshot`、`video`、`trace`、`log`、`report`。每项需要 `kind`、
`label`、`path`。步骤也可有 `attachments`，归属应保留。

- 本地附件路径相对生成 HTML 所在目录，限制在该目录及子目录；复制或链接产物前核对
  项目授权和来源。链接缺失只显示证据缺口，不能标作已验证可用。
- 如使用 `http/https` 原生报告链接，保留其来源与访问限制；其他 URL scheme、绝对路径、
  路径上跳均不用于可点击附件。日志和名称按纯文本展示，内嵌 JSON 必须安全转义。
- 普通详情可以离线展示。trace ZIP 通常交给相应框架查看器；面板提供附件和原生报告入口，
  不宣称单个 HTML 就能解析任意 trace。录像仅在实际采集后可展示。
- 日志、截图和请求证据应先按项目规则脱敏；面板不写入凭据、真实登录态或未授权上传。

## 接入与校验

项目采集器输出此 JSON 后，默认通过项目配置中的 `normalized` 来源构建。
已有单份 v1 输入也可继续直接运行中央渲染器：

```bash
python3 <skill-dir>/scripts/render_dashboard.py --input results.json --output reports/index.html
```

多来源构建会给不同来源的 run/case/method 身份加命名空间，并保持 `methods.run_ids`
引用正确；来源 revision 原样保留，不能用当前 HEAD 补齐未知的结果版本。配置中的
项目名称与当前本地 HEAD/dirty 用于面板项目上下文，不能证明历史结果属于当前版本。
`methods/gaps` 合并时仍保留缺省与已评估的区别。

构建产物顶层可附 `snapshot`，记录生成时间、中央模板及渲染器/适配器 SHA256、来源
采集状态；这是页面生成信息，不是测试运行证据。模板更新需显式重建，归档 HTML 保持
当时快照。标准报告不自动提供可信计划全集，适配后 `inventory_known=false`。
缺失来源显示 `not_integrated`，解析失败显示 `collection_error`，不伪造通过或未运行数量。

未知字段可以保留为项目扩展，但不应自动执行。中央渲染器只生成查看页面，不执行 `command`。
新增启动、重试或停止按钮时，通过项目已批准的执行入口接入，并验证真实任务身份、进度和
权限；页面按钮点击本身不等于测试已经运行。
