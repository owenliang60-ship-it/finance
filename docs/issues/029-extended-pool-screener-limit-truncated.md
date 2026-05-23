# Issue 029: Extended Pool Screener Limit Truncated

**Status**: Resolved (Phase A1)
**Date**: 2026-05-23
**Severity**: P0 数据完整性
**Related**: docs/plans/2026-05-23-extended-pool-data-integrity-phase-a1.md

## 触发
2026-05-21 Boss 问"概念词表里 TTMI 的 L1-L3"，发现 TTMI（市值 $17.59B）不在 concept registry / extended pool / 任何 universe。

## 根因
`src/data/fmp_client.py:get_large_cap_stocks()` 调用 FMP screener 时未传 `limit` 参数。FMP screener 默认 `limit=1000` 且按 marketCap 降序返回。

实际效果：扩展池等价于 **隐式 marketCap >= $24.41B 阈值**（top-1000 边界），而非配置的 `EXTENDED_UNIVERSE_MIN_MCAP_B = 10`。

## 影响面
- `extended_universe.json`: 533 only（应 ~949），漏 ~416 只 $10B-$24B 中盘
- `pool_manager` 科技扩池 `TECH_MARKET_CAP_THRESHOLD=$10B` 同样受影响
- `rs_universe_scan` default `--min-mcap=10` universe 阉割
- 下游因子研究 / RS backtest / concept registry 全部受污染

## 修复
Phase A1 (commit hash 落定后填):
- `src/data/fmp_client.py`: 加 `limit=SCREENER_DEFAULT_LIMIT=5000` 默认参数 + truncation sentinel warning
- `src/data/extended_universe_manager.py`: `MIN_COUNT_FLOOR` 400→800
- `terminal/tools/fmp_tools.py`: tool registry execute() 透传 limit

## 教训
1. **任何调用第三方 screener / list API 拿"全集"语义时，必须显式传 page limit + 加 sentinel**
2. `MIN_COUNT_FLOOR` 设计目的是防 corruption，但救不了"被默默截断"的失败模式——floor 之上还需要 sentinel
3. 历史 commit `34535aa`（5/9 weekly refresh fix）只修了 cron 漏调 flag，没碰 limit truncation——**修复 governance 不等于修复正确性**

## 关联
- 5/9 plan `docs/plans/2026-05-09-extended-pool-weekly-refresh.md`（修治理漏洞）
- A2 follow-up: 416 只新增票 concept 补分类
- A3 follow-up: 周频 concept-build cron 接入
