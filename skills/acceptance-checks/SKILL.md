---
name: acceptance-checks
description: Detailed acceptance practices for verification work — visual changes verified against existing designs or prior versions via rendered output, UI acceptance with realistic data and edge states, tests isolated from the real environment with synthetic data, no real credentials, and no external side effects. Use when verifying UI or visual changes, or when preparing test materials and acceptance criteria.
---

# 验收细则

验证类工作的具体验收做法。通用标准见执行环境规范（如全局规范「验证与材料」），本技能承载任务条件触发的细则。

## 视觉验收

- 视觉改动必须截图检查渲染产物，与设计稿或改前版本对比；有设计稿时以设计稿为基准。
- DOM 断言和静态阅读不能替代视觉验收。

## UI 数据

- UI 验收用真实或真实量级的数据，覆盖长文本、多条目和空态。
- 示例假数据只定结构，不定稿。

## 测试材料

- 离线测试隔离真实配置及外部副作用；红态使用合成数据，不能以读取真实凭据制造失败。
- 涉及敏感配置的断言只显示必要的键或脱敏信息。
