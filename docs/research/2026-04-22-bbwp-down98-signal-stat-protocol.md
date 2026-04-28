# BBWP Downcross 98 Signal Statistics Protocol

**Date:** 2026-04-22  
**Status:** Frozen before implementation

## Core Question

不再把这次研究定义为回测，也不再混入 `PMARP`。  
这次只回答一个问题：

`BBWP downcross 98%` 出现后，未来 `3 / 7 / 10` 个交易日的股价收益率，是否与信号出现时的**当前趋势方向**呈现负相关？

如果答案是是，那么这个信号才可以被理解为对“当前趋势可能接近结束”有一定指导意义。

## Signal Definition

- `BBWP downcross 98%` = `prev > 98.0 and curr <= 98.0`

## BBWP Parameters

- `bb_period = 20`
- `bb_std = 2.0`
- `lookback = 150`

## Current Trend Definition

趋势不是看过去收益率，不看 relative strength，也不看未来是否反穿中轨。  
只看**信号日当天**价格相对布林中轨的位置：

- `close[T] > middle_band[T]` → 当前上涨趋势
- `close[T] < middle_band[T]` → 当前下跌趋势
- `close[T] == middle_band[T]` → 中性，单独计数，不纳入主统计

其中 `middle_band[T]` 使用与本次 `BBWP` 同参数的 Bollinger 中轨，即 `20` 日均线。

## Return Definition

信号日为 `T`，未来收益率定义为：

- `ret_3 = close[T+3] / close[T] - 1`
- `ret_7 = close[T+7] / close[T] - 1`
- `ret_10 = close[T+10] / close[T] - 1`

这是纯股价收益率，不做 benchmark-adjustment。

## Universes

- `pool`
- `extended`

## Samples

- Full: `2021-07-01` ~ latest
- IS: `2021-07-01` ~ `2023-12-31`
- OOS: `2024-01-01` ~ latest

## Primary Statistics

对每个 horizon (`3 / 7 / 10`) 和每个 universe / sample：

### 1. 分桶统计

- `mean(ret_h | above_mid)`
- `mean(ret_h | below_mid)`
- median
- hit rate
- date-clustered t-test

### 2. 桶间比较

比较 `below_mid` vs `above_mid`：

- `diff = mean(ret_h | below_mid) - mean(ret_h | above_mid)`
- Welch t-test on date-clustered means

如果这个 diff 为正，说明信号更像“下跌趋势后的反弹提示”，而不是“上涨趋势后的见顶提示”。
如果 diff 为负，说明相反。

### 3. Reversal Score

定义：

- `trend_sign = +1` for `above_mid`
- `trend_sign = -1` for `below_mid`
- `reversal_score = - trend_sign * ret_h`

解释：

- 上涨趋势后下跌 → score > 0
- 下跌趋势后上涨 → score > 0
- 信号后趋势继续延续 → score < 0

因此：

- `mean(reversal_score) > 0` 表示未来收益与当前趋势方向整体负相关

## Statistical Discipline

- event-level returns 用 **date clustering** 聚合同日事件，避免横截面相关性把显著性吹高
- 所有 p-value 在同一张结果表内做 BH-FDR
- 不为了显著性回头改 `98%` 阈值、`3/7/10` horizon 或 trend 定义

## Interpretation Rules

- 这次研究的目标不是证明“趋势已经结束”
- 而是检验：`BBWP downcross 98%` 后的未来收益，是否**倾向于反着当前趋势方向走**
- 所以主结论看：
  - `above_mid` vs `below_mid` 的分桶均值
  - `reversal_score` 是否显著为正
