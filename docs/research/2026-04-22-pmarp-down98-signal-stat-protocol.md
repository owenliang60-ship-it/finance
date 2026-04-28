# PMARP Downcross 98 Signal Statistics Protocol

**Date:** 2026-04-22  
**Status:** Frozen before implementation

## Core Question

不把这次研究做成组合回测。  
只回答一个问题：

`PMARP downcross 98%` 出现后，未来 `7 / 14 / 21` 个交易日的股价收益率统计分布是什么样？

如果这个信号真代表“离开极强、强势衰减”，那么后续收益应当偏弱，至少不应该继续显著强势。

## Signal Definition

- `PMARP downcross 98%` = `prev > 98.0 and curr <= 98.0`

## PMARP Parameters

- `ema_period = 20`
- `lookback = 150`

## Return Definition

信号日为 `T`，未来收益率定义为：

- `ret_7 = close[T+7] / close[T] - 1`
- `ret_14 = close[T+14] / close[T] - 1`
- `ret_21 = close[T+21] / close[T] - 1`

这是纯股价收益率，不做 benchmark-adjustment。

## Universes

- `pool`
- `extended`

## Samples

- Full: `2021-07-01` ~ latest
- IS: `2021-07-01` ~ `2023-12-31`
- OOS: `2024-01-01` ~ latest

## Primary Statistics

对每个 horizon (`7 / 14 / 21`) 和每个 universe / sample 输出：

- `N`
- date-clustered effective `N`
- mean return
- median return
- positive-rate
- one-sample t-test vs 0
- BH-FDR within each universe/sample table

## Statistical Discipline

- event-level returns 使用 **date clustering**，先对同日事件取横截面均值，再做 t-test
- 不为追求显著性回头改 `98%` 阈值或 horizon
- 不引入 PMARP 之外的过滤器

## Interpretation Rules

- 这次研究是**信号统计**，不是策略回测
- 主结论看：
  - mean return 的方向和大小
  - positive-rate 是否明显低于 50%
  - OOS 是否和 Full / IS 同向
- 如果结果在多数窗口仍为正，或者方向不稳，就不能把 `PMARP downcross 98%` 解释为可靠的“强势衰减”交易信号
