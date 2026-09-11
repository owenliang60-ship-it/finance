# Issue 047: Writer 迁移后 derived mirror 失去刷新者会触发延迟健康检查故障

**Status**: 已修复（Extended Primary Universe Stop A review fix wave）
**Date**: 2026-08-20
**Severity**: HIGH — 30 天后 health gate FAIL，可中断每日串行数据管道
**Related**: `scripts/update_data.py` · `scripts/reconcile_fundamentals.py` · `src/data/data_health.py`

## 触发与根因

基本面 writer 从 `update_all_fundamentals()` 迁到共享 kernel 后，SSOT 已改写 `company_profile` 表，但 legacy `profiles.json` 的 `_meta.updated_at` 不再刷新。健康检查仍读取 mirror 时间戳，先 WARN、30 天后 FAIL；覆盖收敛为零时 reconcile 也因 `frozen=[]` 跳过 mirror rebuild。

## 修复

- `run_fundamental_update()` 每批结束只刷新一次 mirror；零 event target 同样刷新。
- reconcile 在任何 `--repair` run 都刷新一次，不再依赖 repair target 非空。
- mirror 写失败只 warning，不改变已提交的 market.db 结论。
- 测试覆盖正常、零 target 与 mirror I/O 失败三条路径。

## 教训

迁移 SSOT writer 时必须列出所有 derived artifacts 与它们的 freshness consumer；“主表已更新”不等于旧 gate 已自动迁移。
