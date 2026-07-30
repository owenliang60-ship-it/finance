# Issue 047: income_quarterly 覆写会丢失财报 restatement vintage

**Status**: OPEN — 一次性描述性研究接受；不满足严格 point-in-time 回测
**Date**: 2026-07-14
**Severity**: MEDIUM — 防公告日前视成立，但无法重建供应商后来重述前的旧值
**Related**: `income_quarterly` · `select_four_continuous_asof_quarters()`

## 根因

`income_quarterly` 的主键是 `(symbol, date)`，写入采用 replace/upsert。相同 fiscal date 后续抓到的新版本会覆盖旧值。`accepted_date` 表示报表接受时间，不等同于供应商 snapshot vintage；供应商也可能沿用原 accepted date 修订历史值。

因此：

- 代码能阻止尚未公告的季度提前可见；
- 当次 API 响应内若含同 fiscal 多行，仍可按 acceptance 选择；
- 但跨抓取批次的旧版本不会保留，历史估值可能使用后来重述后的数字。

## 当前边界

2026-07-14 本地只读扫描的 1,835 条季度记录没有 malformed accepted/net-income 行；本研究明确标为 GAAP TTM proxy，而非 vintage fundamentals database。保留的 dry-run JSON、分支 SHA 与源库时间点提供一次性复核锚点，但不等于完整 PIT 数据。

## Recurring pipeline 关闭条件

新增 append-only fundamentals vintage 表，至少以 `(symbol, fiscal_date, fetched_at, accepted_date)` 保留每次供应商快照，并满足：

1. 两个抓取 vintage 均可查询，重跑不覆写旧版本；
2. 历史估值只选择当时已抓取且已 visible 的 vintage；
3. run manifest 冻结 source snapshot/hash；
4. verifier 能按 valuation/run cutoff 重算同一结果。
