# Issue 035: KLAC 拆股窗口 historical_market_cap 跨表口径污染

**Status**: OPEN — 历史篮子估值管线已隔离，源表修复待 production backfill
**Date**: 2026-07-14
**Severity**: HIGH — 会直接把 SOXX 2026-06 earnings yield 放大并压低 PE proxy
**Related**: `historical_market_cap` · `daily_price` · `fmp_stock_splits` · SOXX historical TTM PE

## 现象

KLAC 在 2026-06-10 至 2026-06-23 的历史市值约从 $280B 降到 $28B–$35B，06-24 恢复到约 $315B；同期 FMP split endpoint 记录 2026-06-12 发生 10:1 split。价格序列已经拆股调整，但市值序列在该窗口内的 shares-outstanding 口径滞后，形成约 ÷10 的假市值。

## 风险

KLAC 在 SOXX 权重约 4%。仅检查“数据非空/距估值日不超过 7 天”或整体覆盖率无法发现 fresh-but-wrong 数据；若幂等回填只跳过已有行，污染会永久保留并使当期 earnings yield 虚高约 10 倍。

## 防线

- 对每个成分股逐日比较 market-cap return、price return、implied shares 与 split event。
- 只有观察到 implied shares 确实按公告 split ratio 变化时才调整 shares anchor；若 price 与 HMC 历史均已 back-adjusted（MCHP 2021-10-13 实证），不得重复乘 split ratio。
- anomaly interval 从异常打开持续到经济口径真正恢复，不能让拆股相邻日的表面 ×10 跳变自动关闭。
- invalid/unresolved 窗口执行一次带前后交易日 padding 的强制 range replace；空/坏响应保持旧范围不变。
- 重拉后仍异常则 quarantine，估值不允许跨过该日期 forward-fill。
- 只读 verifier 从原始 HMC/price/split 表重跑检测，并对所有已发布 member-date 逐一核验。

## 关闭条件

Production backfill 后，2026-06-10..2026-06-23 每个 KLAC 行必须满足以下二选一：

1. 重拉后 implied shares 与拆股调整后的 clean regime 连续，分类为 accepted；或
2. 保持 quarantine，相关 SOXX 日期不得使用 KLAC 市值且必须遵守 90% weight gate。

完成后附 verifier 证据并把状态改为 RESOLVED。
