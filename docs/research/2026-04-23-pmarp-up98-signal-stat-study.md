# PMARP Upcross 98 Signal Statistics Study

**Date:** 2026-04-23  
**Status:** Completed

## TL;DR

结论先写死：

1. `PMARP upcross 98%` 在日频事件统计里，后续 `7 / 14 / 21` 个交易日的原始股价收益在 `pool` 和 `extended` 上都整体为正，而且多数窗口显著。
2. 所以它**不是**一个“过热后立刻转弱”的日频反转信号；更像“进入极强区后仍有正漂移”的强势延续事件。
3. 但把收益换成相对 `SPY` 的超额收益后，结论明显变弱：`pool` 里还有持续正超额，`extended` 尤其 `OOS` 基本不显著。
4. 当前最合理的定位不是“广谱 breakout alpha”，而是“在更干净股票池里可观察到的强势确认/延续信号”；能否上升为生产级 alpha，还需要更严格的组合层验证。

## Why This Is A Statistics Study, Not A Backtest

这次先不做完整回测，只回答两个更底层的问题：

1. `PMARP upcross 98%` 出现后，价格本身接下来是继续涨，还是容易转弱？
2. 这种后续上涨里，有多少只是市场 beta，有多少还能留下相对 `SPY` 的超额？

如果一上来就做组合回测，仓位、并发、持有上限、成本、再平衡都会把问题搅在一起。  
这次先做事件统计，先把信号语义本身说清楚。

## Frozen Methodology

### Signal

- `PMARP upcross 98%` = `prev < 98.0 and curr >= 98.0`

### PMARP parameters

- `ema_period = 20`
- `lookback = 150`

### Universes

- `pool`
- `extended`

### Samples

- Full: `2021-07-01` ~ latest
- IS: `2021-07-01` ~ `2023-12-31`
- OOS: `2024-01-01` ~ latest

### Horizons

- `7 / 14 / 21` trading days

### Primary return definition

信号日为 `T`，主统计看原始 close-to-close forward return：

- `ret_7 = close[T+7] / close[T] - 1`
- `ret_14 = close[T+14] / close[T] - 1`
- `ret_21 = close[T+21] / close[T] - 1`

### Supplemental return definition

附加看同 horizon 下相对 `SPY` 的超额收益：

- `excess_h = stock_ret_h - spy_ret_h`

### Statistics

对每个 universe / sample / horizon 输出：

- raw events
- date-clustered effective `N`
- mean return
- median return
- positive-rate
- one-sample t-test vs 0
- BH-FDR within each universe/sample table

### Why date clustering

同一天可能有很多股票一起触发 `up98`。  
如果直接把每只股票当独立样本，显著性会被横截面相关性夸大。

所以这次先对同日事件取横截面均值，再在日期层做 t-test。

## Artifacts

- [universe_summary.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_up98_signal_stats_20260423/universe_summary.csv)
- [signal_counts.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_up98_signal_stats_20260423/signal_counts.csv)
- [event_stats.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_up98_signal_stats_20260423/event_stats.csv)
- [event_excess_stats_vs_spy.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_up98_signal_stats_20260423/event_excess_stats_vs_spy.csv)
- [README.md](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_up98_signal_stats_20260423/README.md)

## Sample Size

| Universe | Raw events | Symbols |
|---|---:|---:|
| `pool` | 2623 | 151 |
| `extended` | 9639 | 523 |

样本量已经足够大，尤其 `extended`。  
所以如果结论仍偏单边，就不能简单用“小样本偶然性”解释。

## Results 1: Raw Price Drift

### `pool`

| Sample | Horizon | Neff | Mean | Median | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|---:|---:|
| Full | 7d | 769 | +1.34% | +0.52% | 54.7% | 6.24e-06 |
| Full | 14d | 764 | +2.30% | +1.03% | 56.2% | 2.85e-08 |
| Full | 21d | 761 | +2.91% | +1.84% | 59.3% | 5.11e-09 |
| IS | 7d | 350 | +0.76% | +0.60% | 54.6% | 0.0271 |
| IS | 14d | 350 | +1.39% | +0.68% | 54.0% | 0.0169 |
| IS | 21d | 350 | +1.47% | +0.39% | 52.0% | 0.0169 |
| OOS | 7d | 419 | +1.82% | +0.49% | 54.9% | 7.89e-05 |
| OOS | 14d | 414 | +3.07% | +1.36% | 58.0% | 1.04e-06 |
| OOS | 21d | 411 | +4.13% | +2.36% | 65.5% | 8.11e-08 |

### `extended`

| Sample | Horizon | Neff | Mean | Median | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|---:|---:|
| Full | 7d | 1030 | +0.55% | +0.65% | 56.5% | 7.32e-05 |
| Full | 14d | 1023 | +1.02% | +0.89% | 57.5% | 1.18e-07 |
| Full | 21d | 1017 | +1.25% | +1.30% | 59.9% | 8.87e-08 |
| IS | 7d | 489 | +0.51% | +0.68% | 56.6% | 0.0215 |
| IS | 14d | 489 | +0.77% | +0.89% | 56.6% | 0.0183 |
| IS | 21d | 489 | +0.87% | +0.69% | 55.2% | 0.0192 |
| OOS | 7d | 541 | +0.59% | +0.64% | 56.4% | 6.60e-04 |
| OOS | 14d | 534 | +1.24% | +0.89% | 58.2% | 1.81e-06 |
| OOS | 21d | 528 | +1.60% | +1.60% | 64.2% | 9.49e-08 |

### Raw interpretation

这张表已经足够说明一件事：

- `PMARP upcross 98%` 后，价格不是偏弱，而是继续偏强
- `14 / 21d` 比 `7d` 更稳定
- `OOS` 没有把这个现象打掉，反而在 `pool` 上更强

所以它不支持“上穿 98 之后容易过热见顶”的说法。  
如果只看原始价格漂移，这个信号更接近强势延续而不是短期反转。

## Results 2: Excess Return vs SPY

### `pool`

| Sample | Horizon | Neff | Mean Excess | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|---:|
| Full | 7d | 769 | +0.97% | 54.1% | 5.64e-04 |
| Full | 14d | 764 | +1.64% | 54.2% | 1.89e-05 |
| Full | 21d | 761 | +2.01% | 55.5% | 1.89e-05 |
| IS | 7d | 350 | +0.57% | 55.4% | 0.0542 |
| IS | 14d | 350 | +1.05% | 56.0% | 0.0389 |
| IS | 21d | 350 | +1.09% | 54.6% | 0.0389 |
| OOS | 7d | 419 | +1.30% | 53.0% | 0.00401 |
| OOS | 14d | 414 | +2.14% | 52.7% | 4.56e-04 |
| OOS | 21d | 411 | +2.80% | 56.2% | 2.67e-04 |

### `extended`

| Sample | Horizon | Neff | Mean Excess | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|---:|
| Full | 7d | 1030 | +0.24% | 52.6% | 0.0608 |
| Full | 14d | 1023 | +0.45% | 53.8% | 0.0247 |
| Full | 21d | 1017 | +0.42% | 54.6% | 0.0592 |
| IS | 7d | 489 | +0.43% | 54.4% | 0.0409 |
| IS | 14d | 489 | +0.59% | 55.4% | 0.0277 |
| IS | 21d | 489 | +0.53% | 56.0% | 0.0607 |
| OOS | 7d | 541 | +0.07% | 51.0% | 0.667 |
| OOS | 14d | 534 | +0.32% | 52.2% | 0.418 |
| OOS | 21d | 528 | +0.32% | 53.2% | 0.418 |

### Excess interpretation

这张表把故事修正得更准确：

- `pool` 上，`up98` 不只是 raw price drift，连相对 `SPY` 的超额收益也还是正的，`14 / 21d` 最稳
- 但 `extended` 上，这个超额已经明显变薄
- 到 `extended OOS`，`7 / 14 / 21d` 全部不显著

所以更合理的说法不是“`up98` 是广谱 alpha”，而是：

- 在更干净的 `pool` 里，它像一个有一定超额的强势确认信号
- 在更广义的 `extended` 里，它更多体现为强票本身的 raw continuation，超额并不稳健

## Reconciling The Two Layers

表面上看，Raw 和 Excess 好像有冲突：

- Raw: 很强，持续为正
- Excess: `extended OOS` 明显变弱

其实这两者可以同时成立。

更符合数据的解释是：

1. `PMARP upcross 98%` 确实抓到了“进入极强区”的股票
2. 这些股票之后经常还会继续涨，所以 raw return 很稳
3. 但放到更大的股票池后，这种上涨里有相当一部分是市场 beta、赛道 beta，或者强票共同暴露
4. 因此 raw drift 很清楚，alpha 却没有同样清楚

## Final Conclusion

### Q1. `PMARP upcross 98%` 后，股价本身会怎样？

回答：**继续偏强，不是转弱。**

- `pool` Full: `+1.34% / +2.30% / +2.91%`
- `extended` Full: `+0.55% / +1.02% / +1.25%`

而且 `OOS` 也保持同方向。

### Q2. 它能不能被理解成“过热后容易衰减”的日频信号？

回答：**不能。**

这次统计恰恰说明：

- 它不是 bearish fade signal
- 更像 strength confirmation / continuation signal

### Q3. 它是不是一个稳健的广谱 alpha？

回答：**暂时不能这么说。**

原因是：

- `pool` 上有一定超额
- 但 `extended OOS` 的相对 `SPY` 超额不显著

所以目前更保守、也更准确的定位是：

- **raw price drift 成立**
- **broad-universe alpha 尚未被证明**

### Q4. 现在实际该怎么用这个信号？

当前最合理的 operational interpretation：

1. 不要把 `up98` 当成“该卖了”的反转信号
2. 如果要用，更像是“强势确认”而不是“过热警报”
3. 研究上优先继续看：
   - `pool` 而不是 `extended`
   - `14 / 21d` 而不是只盯 `7d`
   - 组合层 `T+1 open` + 成本 + 并发约束的正式 backtest

## Caveat

这次仍然不是完整策略回测：

- 没有 `T+1 open` 入场
- 没有仓位容量限制
- 没有并发信号竞争
- 没有手续费和滑点

所以它回答的是“信号后分布是什么”，不是“完整策略最终能赚多少”。
