---
name: background-tasks
description: Operating discipline for background and long-running work — pre-launch dedup and registration, backoff monitoring with progress-based stall detection, cleanup and handoff. Use when launching, monitoring, or wrapping up background tasks, watchers, builds, or servers.
---

# 后台任务

需后台运行、跨多次等待或持续占用资源的任务的操作纪律。数小时级或无人值守任务的需求封闭与执行纪律见 long-task。

## 启动前

- 核实宿主持久运行能力，并按工作目录、关键参数、输入/输出路径和目标资源查重；已有同一任务先核对状态并复用。
- 登记任务标识、用途、状态信号、输出路径及终止条件；缺少日志、进度或心跳信号先补再跑。

## 监控

- 优先后台执行并使用可用的完成通知；无通知能力时报告任务标识与状态，不承诺无法保证的跨会话存活或通知。
- 主动检查按指数退避取快照；按最近有效进展判断卡住而非总时长或输出静默；超过静默阈值先查进程、日志和进度文件再处理；快照间隙推进其他工作。

## 收尾

- 任务完成、被替换或会话结束时清理本会话临时监控及辅助进程；用户要求跨会话继续的长任务保留，交接状态、查询和停止方式。
