# PMARP Cross-Up 2% Extended Universe Hardening Preregistration

**Date:** 2026-04-22  
**Status:** Frozen before OOS execution  
**Owner:** Codex under Boss direction

## Research Question

在更严格的 extended universe + PIT market-cap 过滤口径下，`PMARP cross_up 2%` 是否仍然是一个可纳入 Finance 因子库的美股均值回归事件信号？

本次不是做组合回测，也不是继续扩展新过滤器。  
只回答一个更硬的问题：

> 在显式时间切分的 OOS holdout 上，`PMARP cross_up 2%` 的 60 日超额收益是否仍然显著为正？

## Prior Evidence (Already Seen, Not Re-tested Here)

- 2026-03-17 `pool 166`:
  `cross_up_2.0 @ 60d mean excess +5.87%, p-FDR 0.028`
- 2026-04-12 `extended 533 + partial PIT`:
  `cross_up_2.0 @ 60d mean excess +1.94%, p-FDR 0.0169`

这些历史结果只构成本次硬化的先验证据，不用于反推阈值、horizon 或 split。

## Frozen Signal Semantics

- Factor: `PMARP`
- Signal: `cross_up_2.0`
- Definition: `prev < 2.0 and curr >= 2.0`
- EMA period: `20`
- Lookback: `150`

## Universe And Data Policy

- Market: `us_stocks`
- Universe: `extended`
- PIT filter: `mcap_threshold = 10e9`
- Missing historical market cap policy: **drop**
- Coverage gate: historical market-cap coverage must stay `>= 90%` on each rebalance date

### Honest limitation that remains

True survivorship is **not** fixed in this preregistration.  
`historical_market_cap` only covers symbols that exist in today's seed universe, so delisted names are still absent. This run hardens partial PIT and time holdout, not delisting backfill.

## Time Window

- Study start: `2021-07-01`
- Study end: latest available date in current `market.db`
- Computation frequency: `D`

### OOS split

- Explicit OOS start: `2025-01-01`
- IS window: `2021-07-01` to the last daily computation date before `2025-01-01`
- OOS window: the first daily computation date on or after `2025-01-01` to latest

Reason for this split:
- keeps a clean chronological holdout of roughly one calendar year plus current YTD
- retains far more than the existing `min_oos_dates = 50` gate on daily sampling
- is chosen on procedural grounds, not after viewing OOS outcomes

## Return Definition

- Benchmark: `SPY`
- Return metric: forward excess return vs `SPY`
- Horizons: `7d`, `30d`, `60d`

## Primary Endpoint

The factor passes this hardening step only if **all** of the following hold on OOS for `cross_up_2.0 @ 60d`:

1. mean excess return `> 0`
2. hit rate `> 55%`
3. BH-FDR adjusted `p < 0.05`

## Secondary Checks

- OOS `30d` direction should remain positive
- Full / IS / OOS should not flip sign against each other at `60d`
- Effective sample size (`Neff`) must be reported using date clustering

These secondary checks help interpret quality, but they do not override the primary gate.

## Statistics

- Event study only
- Same-day events are date-clustered before t-test
- BH-FDR is applied within the OOS event family for this single run
- No parameter sweep beyond the frozen horizons above

## Execution Command

```bash
/Users/owen/CC\ workspace/Finance/.venv/bin/python scripts/run_factor_study.py \
  --market us_stocks \
  --factor PMARP \
  --thresholds 2 \
  --universe extended \
  --mcap-threshold 10e9 \
  --start 2021-07-01 \
  --freq D \
  --oos-start 2025-01-01 \
  --horizons 7,30,60 \
  --benchmark SPY \
  --csv \
  --html
```

## Anti-Cheating Rules

- Do not change the `2.0` threshold after seeing results
- Do not change the OOS boundary after seeing results
- Do not switch away from daily for this preregistered run
- Do not add BBWP, downtrend, or other filters into this run
- Do not re-run with new splits and pick the best-looking one

## Stop Rule

Run exactly once on the current local data snapshot after the preregistration is written.  
If the primary endpoint fails, record failure honestly instead of weakening the standard.
