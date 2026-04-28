# PMARP Downcross 98 Signal Statistics Study

**Date:** 2026-04-22  
**Status:** Completed  
**Protocol:** [2026-04-22-pmarp-down98-signal-stat-protocol.md](/Users/owen/CC%20workspace/Finance/docs/research/2026-04-22-pmarp-down98-signal-stat-protocol.md)

## TL;DR

结论先写死：

1. 这次把问题做成事件统计而不是回测，是对的。
2. `PMARP downcross 98%` 在 `pool` 和 `extended` 上，信号后 `7 / 14 / 21` 日原始股价收益**整体都是正的**。
3. 所以按这次定义，它**不是**一个可靠的日频 bearish / 强势衰减交易信号。
4. 更准确地说，它更像“离开极强区，但价格后续仍常常维持正漂移”，而不是“下穿 98 后就容易转弱下跌”。

## Why This Is A Statistics Study, Not A Backtest

这次研究的问题不是：

- “用 `PMARP down98` 做交易能赚多少钱？”
- “这个信号能不能做成完整策略？”

而是一个更底层的问题：

- `PMARP downcross 98%` 这个事件本身，之后 `7 / 14 / 21` 个交易日的收益分布是什么样？

所以这次设计成**纯事件统计**而不是回测，原因有三个：

1. 你要先验证的是**信号含义**，不是交易实现。
2. 如果直接上回测，仓位、持有期、再平衡、成本、并发上限这些二级设计，会污染你对信号本体的判断。
3. `PMARP down98` 的直觉语义是“离开极强，强势衰减”，这本质上就是一个事件后分布问题，统计比回测更直接。

## Frozen Methodology

### Signal

- `PMARP downcross 98%` = `prev > 98.0 and curr <= 98.0`

这和仓库里 [pmarp.py](/Users/owen/CC%20workspace/Finance/src/indicators/pmarp.py) 的定义一致。

### PMARP parameters

- `ema_period = 20`
- `lookback = 150`

### Forward returns

信号日为 `T`，这次只看最原始的股价事件后收益：

- `ret_7 = close[T+7] / close[T] - 1`
- `ret_14 = close[T+14] / close[T] - 1`
- `ret_21 = close[T+21] / close[T] - 1`

为什么不用 `T+1 open` 或 benchmark-adjusted return：

1. 你这次问的是“信号出现后价格怎么样”
2. 不是“这个信号有没有相对 SPY 的 alpha”
3. `7 / 14 / 21` 更适合看 `PMARP down98` 这种“离开极强区”事件的中短期后效应

### Universes

- `pool`
- `extended`

### Samples

- Full: `2021-07-01` ~ latest
- IS: `2021-07-01` ~ `2023-12-31`
- OOS: `2024-01-01` ~ latest

### Statistics

对每个 horizon 和每个 universe / sample 输出：

- raw events
- date-clustered effective `N`
- mean return
- median return
- positive-rate
- one-sample t-test vs 0
- BH-FDR within each universe/sample table

### Why date clustering

同一天可能有很多股票一起触发 `PMARP down98`。  
如果直接把每只股票当独立样本，显著性会被横截面相关性夸大。

所以这次先对**同日事件取横截面均值**，再在日期层做 t-test。  
这样更诚实，也和前几轮 `BBWP` / `PMARP` 研究保持一致。

## Reproducibility

### Commands

```bash
.venv/bin/pytest tests/test_backtest/test_pmarp_signal_stats.py tests/test_backtest/test_daily_event_returns.py tests/test_pmarp_signals.py -q
.venv/bin/python -m py_compile backtest/research/pmarp_signal_stats.py scripts/run_pmarp_down98_signal_stats.py
.venv/bin/python scripts/run_pmarp_down98_signal_stats.py
```

### Validation

- Tests: `27 passed`
- Full run: completed successfully

### Artifact paths

- [universe_summary.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_down98_signal_stats_20260422/universe_summary.csv)
- [signal_counts.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_down98_signal_stats_20260422/signal_counts.csv)
- [event_stats.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_down98_signal_stats_20260422/event_stats.csv)
- [README.md](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_down98_signal_stats_20260422/README.md)

## Sample Size

| Universe | Raw events | Symbols |
|---|---:|---:|
| `pool` | 2231 | 151 |
| `extended` | 8289 | 523 |

这次同样不是“小样本看不出来”的问题。  
尤其 `extended` 的事件数已经足够大，所以结论如果仍然偏正，就不能简单说是噪音。

## Results

## 1. Full sample: both universes stay positive after `PMARP down98`

### `pool`

| Horizon | Neff | Mean | Median | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|---:|
| 7d | 692 | +1.16% | +0.74% | 56.6% | 7.18e-05 |
| 14d | 688 | +2.29% | +0.71% | 55.2% | 0.00161 |
| 21d | 686 | +2.17% | +1.37% | 55.4% | 6.31e-05 |

### `extended`

| Horizon | Neff | Mean | Median | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|---:|
| 7d | 1000 | +0.47% | +0.47% | 56.2% | 3.01e-04 |
| 14d | 995 | +0.80% | +0.61% | 55.1% | 1.35e-05 |
| 21d | 989 | +1.15% | +1.05% | 57.2% | 8.23e-07 |

解释：

- 两个 universe 在 `7 / 14 / 21` 全部是正均值
- `positive-rate` 也都高于 `50%`
- 而且都不是边缘结果，很多窗口 FDR 后仍然很强

这和“下穿 98 之后应该偏弱/偏跌”的直觉是相反的。

## 2. OOS is not weaker; in some windows it is stronger

如果这个信号只是历史上的幻觉，OOS 往往会先掉下去。  
但这里不是这样。

### `pool` OOS

| Horizon | Mean | Median | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|
| 7d | +1.31% | +0.74% | 57.0% | 0.00214 |
| 14d | +3.09% | +1.40% | 58.7% | 0.0129 |
| 21d | +2.88% | +2.78% | 60.1% | 4.75e-04 |

### `extended` OOS

| Horizon | Mean | Median | Positive-rate | p-FDR |
|---|---:|---:|---:|---:|
| 7d | +0.42% | +0.43% | 55.9% | 0.00681 |
| 14d | +1.08% | +1.04% | 59.2% | 1.43e-06 |
| 21d | +1.81% | +1.64% | 62.8% | 2.82e-10 |

解释：

- OOS 没有把这个信号打回原形
- 相反，`14 / 21d` 在两个 universe 上都相当强
- 这说明 `PMARP down98` 至少在**原始价格收益**层面，不像一个衰减/见顶信号，反而更像“离开极强区后仍保留正漂移”

## 3. IS is weaker, but still not bearish

### `pool` IS

| Horizon | Mean | Positive-rate | p-FDR |
|---|---:|---:|---:|
| 7d | +0.98% | 56.2% | 0.0315 |
| 14d | +1.33% | 51.1% | 0.0315 |
| 21d | +1.32% | 49.8% | 0.0423 |

### `extended` IS

| Horizon | Mean | Positive-rate | p-FDR |
|---|---:|---:|---:|
| 7d | +0.52% | 56.5% | 0.0422 |
| 14d | +0.49% | 50.6% | 0.132 |
| 21d | +0.45% | 51.3% | 0.204 |

解释：

- `IS` 确实比 `OOS` 弱一些，尤其 `extended`
- 但即便最弱的 `IS`，均值也仍然是正的，不是负的
- 所以这次研究不能支持“PMARP down98 是强势转弱/下跌开始”的说法

## What The Data Actually Says

更贴近数据的说法是：

1. `PMARP downcross 98%` 表示“离开极强区”
2. 但“离开极强区”不等于“后面会弱”
3. 很多时候它只是从**极端强势**回到**仍然偏强**，而不是直接进入 bearish 状态

这点其实很重要：

- `PMARP` 是价格相对 `EMA20` 的历史分位
- 从 `>98` 下穿到 `<=98`，只说明“不再位于历史最极端的前 2%”
- 它完全可能仍然处于高位、仍然强于均线、仍然延续上涨

所以如果把这个信号口头理解成“强势衰减”，容易过度解读。

## Final Conclusion

### Q1. `PMARP downcross 98%` 之后，`7 / 14 / 21` 日收益统计是什么样？

回答：**在 `pool` 和 `extended` 上都整体为正。**

- `pool` Full: `+1.16% / +2.29% / +2.17%`
- `extended` Full: `+0.47% / +0.80% / +1.15%`

### Q2. 这个信号能不能被当成日频 bearish / 强势衰减交易信号？

回答：**不能。**

按这次 raw price return 的定义：

- 均值不是负的
- positive-rate 不是低于 50%，而是普遍高于 50%
- OOS 也没有体现出弱化成负收益

所以它不符合“见顶后转弱”的事件统计特征。

### Q3. 现在更合理的理解是什么？

回答：**它更像“脱离极端强势区”，不是“进入弱势区”。**

这两者差别很大：

- “脱离极端强势” 仍然可以继续涨
- “进入弱势” 才应该对应明显负收益

当前数据支持前者，不支持后者。

## Caveat

这次看的只是**原始股价收益**。  
所以如果你真正关心的是：

- 它有没有相对 `SPY` 的负 alpha
- 它是不是适合作为卖出/做空条件

那下一步应该补一版：

- benchmark-adjusted event study
- 或 `PMARP down98` vs 控制组 / unconditional mean 的比较

但至少在你这次指定的问题下，答案已经很清楚了：

`PMARP downcross 98%` 不是一个 raw-return 意义上的 bearish 日频信号。

## Files Added

- Research module: [pmarp_signal_stats.py](/Users/owen/CC%20workspace/Finance/backtest/research/pmarp_signal_stats.py)
- Runner: [run_pmarp_down98_signal_stats.py](/Users/owen/CC%20workspace/Finance/scripts/run_pmarp_down98_signal_stats.py)
- Tests: [test_pmarp_signal_stats.py](/Users/owen/CC%20workspace/Finance/tests/test_backtest/test_pmarp_signal_stats.py)
