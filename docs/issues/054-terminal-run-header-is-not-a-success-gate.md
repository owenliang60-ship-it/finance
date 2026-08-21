# Issue 054: terminal run header 不能单独充当成功闸门

**Status**: 已修复（`codex/extended-stop-d-hardening`，待合并/部署）
**Date**: 2026-08-21
**Severity**: HIGH — 100% fetch_failed 的 backfill 可被 Stop D header-only gate 放行进入 metrics
**Related**: `scripts/backfill_extended_fundamentals.py` · Stop D runbook

## 触发与根因

故障注入连续运行三次全失败 provider：15/15 jobs 在 attempts=3 后成为 terminal
`fetch_failed`，CLI 仍返回 1，但 runner 按“所有 job 已终态”把 run header 写成
`complete`。原 Stop D 闸门只查 header status，因而会把处理完毕误当成数据成功。

同一审查还发现 resume 虽只 claim 失败 dataset，却重新请求并写入全部五个 endpoint；manifest
跳过未 claimed 项，造成额外 API 消耗和 ledger 外刷新。

## 修复

- 新增共享只读 `check_run_gate()` + CLI `--verify-only`：同时检查 header、job 全终态、无 pending/in_progress、manifest 非空，以及 `fetch_failed <5%`（严格小于）。
- Stop D runbook 改为调用该 CLI，不再复制 header-only SQL。
- collection kernel 增加 `dataset_keys` 子集；runner resume 只传 claimed jobs。
- 故障注入锁定 100% failure 拒绝、4% 通过/5% 拒绝，以及只重拉失败 endpoint。

## 教训

“完成处理”与“成功达到发布门槛”必须是两个状态；业务闸门要读取底层失败分布，不能只信 header。manifest 控制的 resume 也必须同时约束 provider 调用和数据写入，而不只是约束状态更新。
