# Issue 043: FMP forward 时延预算漏乘每股 endpoint 数量

**Status**: 已修正文档预算；等待自然周六确认组合 runtime
**Date**: 2026-07-13
**Severity**: MEDIUM — 不影响数据正确性，但原 `<30min` 运维目标不可能满足
**Related**: Task 11 interval probe / Task 13 weekly smoke / Saturday cron

## 触发

100-call serial probe 在 1.5s 配置下耗时 167.6s、0×429；但 1,074-symbol production backfill 与 weekly 分别耗时 79.9min、79.5min，显著超过计划 `<30min`。

## 根因

计划用 symbol 数近似 API call 数，漏算每股需要 quarterly estimates、annual estimates、earnings 三个串行 endpoint。仅 pacing 理论下限就是：

`1,074 × 3 × 1.5s = 4,833s ≈ 80.6min`

实际 runtime 与理论值一致，因此不是云端性能回归，也不能靠小幅代码优化降到 30min。

## 修正

- `ARCHITECTURE.md` 改用实测：FMP weekly 约 79.5min，连同 yfinance 旧线的总预算约 95–105min。
- 保留 1.5s 安全间隔；不为追求旧目标擅自并发调用或冒 429 风险。
- 10:45 job 继续使用 `market_db_writer` 资源锁，当前没有后续 writer 冲突。
- 2026-07-18 自然周六验收同时记录 yfinance + FMP 总 duration，作为最终 cron SLO 基线。

## 教训

第三方 API 批任务的时延预算必须按 `targets × endpoints_per_target × interval` 计算，再加响应时间和固定调用；不能只按 universe 大小估算。
