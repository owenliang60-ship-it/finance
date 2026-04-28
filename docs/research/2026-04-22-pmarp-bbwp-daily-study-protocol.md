# PMARP + BBWP Daily Study Protocol

**Date:** 2026-04-22  
**Status:** Frozen before implementation

## Core Hypothesis

`PMARP upcross 2%` 是离开极弱区的 bottom-fishing 信号。  
`BBWP` 不是 top/bottom 指标，它更像“当前趋势是否接近衰竭、波动率极端是否开始回落”的上下文因子。

因此本研究分成两条线：

1. **Standalone BBWP**  
   问题不是“它直接指向上涨吗”，而是“它是否提示当前趋势可能结束”。
2. **PMARP + BBWP**  
   问题不是“两个灯同时亮时均值多高”，而是“`BBWP` 是否提升 `PMARP upcross 2%` 的质量”。

## Frozen Signal Semantics

### PMARP

- `PMARP upcross 2%`: `prev < 2.0 and curr >= 2.0`

### BBWP

- **Primary:** `BBWP downcross 98%` = `prev > 98.0 and curr <= 98.0`
- **Secondary robustness:** `BBWP high-zone turn down` = `prev > 98.0 and curr < prev`

## Universe

- `pool`
- `extended`

## Sample Windows

- **Full:** `2021-07-01` ~ latest available date
- **IS:** `2021-07-01` ~ `2023-12-31`
- **OOS:** `2024-01-01` ~ latest available date

## Execution Semantics

- Signal on day `T`
- Entry at `T+1 open`
- Exit at `T+H close`
- Benchmark: `SPY`
- Return metric: stock return minus SPY return

## Horizons

- Standalone BBWP: `10d`, `20d`, `30d`, `60d`
- PMARP combo: `30d`, `60d`

## Standalone BBWP Trend-End Test

每个 `BBWP` 事件先按 **event date 之前 20 日超额收益** 分上下文：

- `prior_excess_20d < 0` → prior downtrend context
- `prior_excess_20d > 0` → prior uptrend context

解释：

- 如果 prior context 是下跌趋势，trend-end 应表现为后续超额收益转正
- 如果 prior context 是上涨趋势，trend-end 应表现为后续超额收益转弱/转负

## PMARP + BBWP Lift Test

### Baseline

- `PMARP upcross 2%`

### Filters

- `BBWP downcross 98%` same-day
- `BBWP downcross 98%` in recent `3` trading days (`T-3 ~ T`)
- `BBWP high-zone turn down` same-day
- `BBWP high-zone turn down` in recent `3` trading days

### Primary inference

对每个 filter 做：

- accepted PMARP
- rejected PMARP
- accepted vs rejected comparison

Primary question: **accepted PMARP 是否显著优于 rejected PMARP？**

## Statistics

- Event study uses date-clustered effective N
- One-sample t-test for cohort mean return
- Welch t-test for accepted vs rejected comparisons
- BH-FDR across each table’s hypothesis family

## Anti-Cheating Rules

- 不因 strict downcross 样本少而回头改 primary spec
- `high-zone turn down` 只能作为 robustness，不替代 primary
- 不从结果倒推 horizons / threshold / context window
