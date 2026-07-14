# Issue 048: Basket verifier 共享计算内核且无法独立证明历史 repair

**Status**: OPEN — 已校准定位并补强篡改检测；recurring pipeline 需要 immutable manifest
**Date**: 2026-07-14
**Severity**: MEDIUM — 可发现 source/output 漂移，但不能作为独立方法学实现
**Related**: `scripts/verify_basket_ttm_pe.py` · `mcap_sanity_json`

## 现象

verifier 使用 SQLite `mode=ro + query_only` 从 source tables 重算每日结果，并核对成员证据、coverage、methodology version 与 raw HMC sanity；但它复用了 producer 的季度选择、市值选择、权重合并和 sanity 内核。因此正确定位是：

> 只读 source recomputation + persisted-evidence/tamper detector

而不是独立的方法学 oracle。producer 与 verifier 若共享同一个纯函数 bug，可能同错同过。

另外，forced range refresh 会替换旧 HMC 行。post-refresh source tables 无法重建 pre-refresh 异常或证明当时确实发起过 repair。`pre_refresh_status`、`refresh_windows` 与 attempt/success 字段属于 producer-side provenance。

## 本轮补强

- base evidence 只比较 post-refresh raw tables 可复现的 candidate/invalid 事件，避免 clean padding 假红；
- repair-only evidence 单独检查窗口包含关系、attempt/success 布尔关系和 status-derived quarantine；
- verifier 现核对 `member_count` 与 allowlisted `methodology_version=1.0`；
- query 不再信任 producer 的 `quarantined` 布尔值，只从最终 status 推导 gap。

## Recurring pipeline 关闭条件

- 写入 append-only run manifest，保留 pre/post refresh row hash、窗口、响应状态与 run id；
- verifier 在单一数据库快照或资源锁下运行，避免活跃 writer 导致跨阶段读到不同时间点；
- 对关键公式增加异构/独立实现的抽样 reconciliation，而不是复制整套 producer；
- 明确 expected start/end manifest，避免用户给较晚 `--min-date` 只验证 suffix。
