# 项目接入与快照构建

默认由中央 `assets/dashboard.html` 与渲染器维护界面，项目保存配置和必要的采集器。
分组来自来源标签、用例 `suite` 等数据字段；布局由配置选择。沿用已有满足需求的原生
报告入口，保留其 trace、iframe 等查看能力。项目默认不复制或 fork HTML/CSS/JS。

## 接入前盘点

从项目现有测试配置、执行脚本、CI、报告目录及相关质量文档交叉查找来源；沿用已知入口，
不为接入展示重做全库测试。先把实际存在的材料整理成映射，示例如下：

| 测试流程/方法 | 现有执行入口 | 原始产物及版本/环境 | 证据范围与缺口 | 接入方式 |
|---|---|---|---|---|
| 单元/集成测试 | 项目 runner 或 CI | JUnit XML、来源记录 | 只有本轮已输出用例；全集可能未知 | 标准来源 |
| 浏览器流程 | 项目浏览器脚本 | JSON、原生报告、实际录制附件 | 缺录制则步骤/Trace未采集 | 标准来源＋已有证据入口 |
| 门禁/专项命令 | 项目检查入口 | 逐条日志或摘要 | 命令结果不等于全部内部用例结果 | 项目采集，明确记录粒度 |
| 人工/探索记录 | 已有验收流程 | 实际操作、观察、附件 | 只有清单/PR声明时无实际执行证据 | v1记录或保留缺口 |

分类按项目事实调整，不要求凑齐表中类别。测试代码存在、工具已配置、报告存在、
本轮实际执行分别取证。同一份执行报告只接一次；独立版本/环境/重试使用不同轮次标识，
跨轮结果不能累计成当前版本覆盖率。只有摘要时保留摘要与报告入口，不编造内部逐用例
结果；CI job、专项命令和单测用例也需在来源/轮次中说明各自记录粒度。
解析器可按 suite 拆成多个展示分组，分组数不代表独立执行次数；真实执行轮次仍看原始
命令、时间和运行标识，不把展示分组反推成测试执行历史。

盘点后选择最小适配：已有 v1 直接接入；已有标准报告走中央适配器；领域文档、人工记录、
非标准日志才由项目采集器转换。发现真实合法的标准格式不兼容时，保留原文件及来源，
向中央适配器提供最小复现与回归；不在各项目重复复制解析器或修改原始结果凑成功。

项目解析器的状态由原报告字段或明确的日志判据得出，不能硬编码通过，也不能把文件
存在当作成功。用最小合成材料核对成功、失败、缺失和无法识别的输出；缺材料时保留
对应缺口或采集错误。可复用解析函数由调用者传入来源/附件位置，避免不同方法都链接到
同一份日志。结构校验通过后仍要对账原材料，抽查状态、版本/dirty及附件内容是否对应。
负例应采用本项目 runner/checker 的实际失败输出，不只测试采集器自造的格式。多阶段
日志按目标流程判定，先前成功短语不能掩盖后续明确失败；不可读日志保留采集失败。

## 表达测试过程

保留实际命令、轮次/环境、执行时间、版本与来源。运行中的 runner 确实提供阶段或用例
进展时，才更新 `running`、已知用例状态和已记录步骤；只有最终报告时展示最终结果，
不从文件刷新或测试代码反推曾执行的步骤。长日志可作为附件，明确记录的操作阶段可转
为 `steps`，但测试名、PR总结和代码顺序不能替代实际动作与观察。

原始运行材料缺失的历史声明保留其出处和限制，不进入已验证执行统计；探针清单可列
计划，探索轨没有原始操作记录时保持未接入。旧结果的 revision、dirty、时间缺失就写未知，
不要用采集时当前 HEAD 或文件修改时间补执行事实。

## 初始化

```bash
python3 <skill-dir>/scripts/dashboard_project.py init --root <project-root> --name Example
```

在项目中生成 `.test-dashboard/config.json`。需要非标准来源采集器样板时加
`--with-adapter`，才会同时生成 `.test-dashboard/adapter.py`。
初始化先检查全部目标；任一目标已存在即报冲突并零写入，已有配置和采集器不被覆盖。
已接入项目直接维护现有配置，无需反复初始化。
初始配置中的 Playwright/JUnit 来源是路径样例，应按项目实际用途替换或删除，不代表这些方法已经配置。

## 配置来源与布局

以下示例配置位于项目 `.test-dashboard/config.json`；按项目已有产物路径调整：

```json
{
  "config_version": 1,
  "project_root": "..",
  "project": {"name": "Example"},
  "output": "reports/test-dashboard/index.html",
  "layout": "auto",
  "sources": [
    {
      "id": "browser",
      "format": "playwright",
      "path": "frontend/test-results/report.json",
      "label": "Browser",
      "environment": "local",
      "track": "planned"
    },
    {
      "id": "python",
      "format": "junit",
      "path": "reports/junit.xml",
      "label": "Python",
      "environment": "local"
    },
    {
      "id": "manual",
      "format": "normalized",
      "path": ".test-dashboard/project-results.json",
      "label": "Project records"
    }
  ]
}
```

- `project.name` 必填；`project_root` 相对配置文件目录。`output` 和 `sources[].path`
  相对项目根，路径必须留在项目范围内。
- 来源 `id` 用于区分身份，`label` 用于显示；`format` 支持 `playwright`、`junit`、
  `normalized`。配置项目实际使用的来源，缺少文件的来源会明确显示未接入。
- `layout` 可选 `auto`、`compact`、`grouped`、`workflow`，选择依据见
  [界面适配](adaptive-design.md)。不以自建页面解决普通来源或分组差异。
- 来源可附 `environment`、`track`、`revision`、`started_at`、`notes`；这些值应来自
  已知执行事实。缺失的结果 revision 不继承构建时当前 HEAD。
  `normalized` 来源已有字段优先，配置只补缺省字段并追加限制说明；附件路径仍相对最终 HTML，构建不自动搬运证据文件。

标准 Playwright JSON 与 JUnit XML 直接作为来源；适配器只转换报告已知字段，保留环境、
重试等运行身份，不从测试名称补造步骤。报告不是可信计划全集，标准适配结果使用
`inventory_known=false`；未见记录不推算成已知未运行或通过。
JUnit 支持嵌套 suite，以及 Node 原生 reporter 在根容器混合直属 testcase 与 suite 的结构。

非标准矩阵、人工 QA、探索记录等由项目采集器输出 [v1 JSON](result-contract.md)，
再用 `normalized` 来源接入。采集器由项目单独运行并产出文件；`build` 不动态导入
采集器，也不执行配置或结果中的命令。已有 v1 结果无需再次转换。

## 构建与刷新

项目采集器先生成候选数据。使用中央 `render_dashboard.validate_document(document,
final_output_dir)` 校验完整文档及相对证据路径，或用同一引擎作等价候选检查；
`final_output_dir` 是配置中 HTML 输出的目录。校验成功后，用目标同目录临时文件与
`os.replace` 原子发布到配置引用的来源；失败保留上次有效来源并明确报告本次采集失败。
避免直接重定向覆盖服务正在读取的文件。标准报告沿用 runner 支持的写入方式，半写文件
出现时保留真实采集错误并在完整报告出现后恢复，不把旧成功标成最新。

先按项目已有且已批准的流程产出测试报告或采集结果，再执行：

```bash
python3 <skill-dir>/scripts/dashboard_project.py build --config <project-root>/.test-dashboard/config.json
```

构建只读已有报告，验证配置及规范化数据后生成自包含 HTML 快照，不运行测试、安装依赖
或启动服务。配置或数据校验失败时保留原有 HTML。CLI 返回 `0` 只表示页面生成成功；
页面仍可能含失败测试、未接入来源或采集失败，交付时分别报告。

缺失文件或未支持格式显示 `not_integrated`；解析失败显示 `collection_error`。
来源状态同时出现在 CLI 诊断与快照中，不能用空成功轮替代。附件不会自动搬运，
路径与原生报告入口按结果契约检查；只有实际存在或已验证可访问的证据才宣称可用。

快照顶层 `snapshot` 记录生成时间、模板 SHA256、渲染器/适配器 SHA256 和来源采集状态。
项目上下文记录当前本地 HEAD/dirty，不可用时保持未知；构建不操作 Git。
模板更新后显式重跑 `build` 才得到新界面，旧 HTML 和归档快照不会自动更新。

需要多项目共用页面及报告变化后自动刷新时，使用
[共用查看服务](shared-viewer.md)。服务在内存中复用相同采集与渲染接口，不覆盖上述快照。

单份 v1 数据仍兼容原来的直接渲染命令：

```bash
python3 <skill-dir>/scripts/render_dashboard.py --input results.json --output reports/index.html
```

此命令同样只生成页面。需调整其布局时使用已有 `--layout` 选项；多来源配置与来源采集
状态由 `dashboard_project.py build` 管理。

## 接入完成标准

检查配置路径、来源状态和版本范围，再打开生成页验证两轨、分组/筛选、失败详情、证据
入口与缺失空态。已有原生查看能力也要验证，普通详情可看不等于 trace 查看可用。
刷新命令及采集器说明保存在项目已批准的位置；测试执行与探索仍沿项目授权流程处理。

项目保留一个接入文档入口，优先更新已有说明；可使用 `.test-dashboard/README.md` 或
项目既有等价位置，并从相关测试说明链接。包含上述来源映射、采集/校验/刷新命令、
原生报告与附件位置、版本与缺口处理、增加来源的方法。共用服务参数和通用契约引用其
维护源，避免在项目内抄一份。文档中的刷新方法需实际执行验证。

交付时列出来源状态、各轮记录及统计口径、步骤/附件可用情况与仍未接入项；在共用页面
核对本项目分组、筛选、失败详情和证据，并用实际数据量检查可读性。
