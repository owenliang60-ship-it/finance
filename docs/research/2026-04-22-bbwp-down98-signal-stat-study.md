# BBWP Downcross 98 Signal Statistics Study

**Date:** 2026-04-22  
**Status:** Completed  
**Protocol:** [2026-04-22-bbwp-down98-signal-stat-protocol.md](/Users/owen/CC%20workspace/Finance/docs/research/2026-04-22-bbwp-down98-signal-stat-protocol.md)

## TL;DR

结论先写死：

1. 这次不该把问题做成回测，而应该做成事件统计，这个改法是对的。
2. 按你指定的定义，`BBWP downcross 98%` 之后的 `3 / 7 / 10` 日收益，**没有**稳定表现出和“当前趋势方向”负相关。
3. 更直接地说，strict `BBWP downcross 98%` 在日频美股上，**没有被证明是一个可靠的“趋势结束提示”信号**。
4. 短周期上，`close > middle band` 的上涨趋势桶，后续收益往往不比下跌趋势桶差，很多时候反而更强，这和“见顶/趋势结束”叙事相反。

## Why This Is A Statistics Study, Not A Backtest

这次研究的问题不是：

- “用 `BBWP` 做交易能赚多少钱？”
- “这个信号能不能做成一个策略？”

而是一个更底层的问题：

- `BBWP downcross 98%` 出现时，如果它真对“趋势结束”有指导意义，那么它出现后的未来收益，应该和**当前趋势方向**呈现负相关。

所以这次设计成纯事件统计，而不是组合回测，原因有三个：

1. 你要验证的是**信号含义**，不是策略可交易性。
2. 一旦先上回测，结论很容易被仓位、持有期、再平衡、过滤器这些次级设计污染。
3. “趋势结束”这个命题，本质上就是看事件后的条件分布有没有反向倾向，统计比回测更直接。

## Frozen Methodology

### Signal

- `BBWP downcross 98%` = `prev > 98 and curr <= 98`

### BBWP parameters

- `bb_period = 20`
- `bb_std = 2.0`
- `lookback = 150`

### Current trend definition

趋势不是看过去收益率，也不是看相对大盘强弱。  
只看**信号日当天**价格相对 Bollinger 中轨的位置：

- `close[T] > middle[T]` → 当前上涨趋势
- `close[T] < middle[T]` → 当前下跌趋势

这里的 `middle[T]` 就是同参数 `20` 日 Bollinger 中轨，也就是 `20` 日均线。

这样定义的理由很简单：

- 你要的是绝对趋势，不是 relative trend
- 中轨上方/下方是最贴近 Bollinger 语义的结构分界
- 它不依赖未来信息，也不偷用结果变量

### Forward returns

不用 benchmark-adjusted return，也不用 `T+1 open`。  
这次只看最原始的股价事件后收益：

- `ret_3 = close[T+3] / close[T] - 1`
- `ret_7 = close[T+7] / close[T] - 1`
- `ret_10 = close[T+10] / close[T] - 1`

理由：

- 你问的是“信号后价格怎么走”
- 不是“相对 SPY 有没有 alpha”
- `3 / 7 / 10` 日更接近 `BBWP` 这种波动率极值回落信号的短中期统计窗口

### Primary inference

我用两层统计来回答“是否和当前趋势负相关”：

#### 1. 分桶均值

- `mean(ret_h | above_mid)`
- `mean(ret_h | below_mid)`

如果它真提示趋势结束，那么理想上应该看到：

- 上涨趋势桶后续更弱
- 下跌趋势桶后续更强

#### 2. Reversal Score

定义：

- `trend_sign = +1` for `above_mid`
- `trend_sign = -1` for `below_mid`
- `reversal_score = - trend_sign * ret_h`

解释：

- 上涨趋势后下跌 → score > 0
- 下跌趋势后上涨 → score > 0
- 信号后趋势继续延续 → score < 0

所以如果 `BBWP downcross 98%` 真有“趋势结束”含义，那么应该看到：

- `mean(reversal_score) > 0`

这实际上就是把“未来收益和当前趋势负相关”翻译成一个更好读的统计量。

### Statistical discipline

- 所有事件结果都做 **date clustering**
- 同一天多个事件先在日期层平均，避免横截面相关性夸大显著性
- 各结果表内部做 BH-FDR

## Reproducibility

### Commands

```bash
.venv/bin/pytest tests/test_backtest/test_daily_event_returns.py tests/test_backtest/test_bbwp_signal_stats.py tests/test_bbwp.py -q
.venv/bin/python -m py_compile backtest/research/daily_event_returns.py backtest/research/bbwp_signal_stats.py scripts/run_bbwp_signal_stats.py
.venv/bin/python scripts/run_bbwp_signal_stats.py
```

### Validation

- Tests: `12 passed`
- Full run: completed successfully

### Artifact paths

- [universe_summary.csv](/Users/owen/CC%20workspace/Finance/backtest/new/bbwp_down98_signal_stats_20260422/universe_summary.csv)
- [bucket_counts.csv](/Users/owen/CC%20workspace/Finance/backtest/new/bbwp_down98_signal_stats_20260422/bucket_counts.csv)
- [bucket_stats.csv](/Users/owen/CC%20workspace/Finance/backtest/new/bbwp_down98_signal_stats_20260422/bucket_stats.csv)
- [comparison_stats.csv](/Users/owen/CC%20workspace/Finance/backtest/new/bbwp_down98_signal_stats_20260422/comparison_stats.csv)
- [reversal_stats.csv](/Users/owen/CC%20workspace/Finance/backtest/new/bbwp_down98_signal_stats_20260422/reversal_stats.csv)
- [README.md](/Users/owen/CC%20workspace/Finance/backtest/new/bbwp_down98_signal_stats_20260422/README.md)

## Sample Size

| Universe | All events | Above mid | Below mid |
|---|---:|---:|---:|
| `pool` | 1222 | 634 | 588 |
| `extended` | 4422 | 2227 | 2195 |

这点很重要：  
这次不是“样本太少导致看不见效果”。  
尤其 `extended` 上，事件数量已经足够大，所以结论如果仍然不明显，更应该认真对待。

## Results

## 1. Overall signal is mildly positive, not reversal-shaped

先看不分趋势桶的总体事件统计。

### `pool`

| Sample | 3d | 7d | 10d |
|---|---:|---:|---:|
| Full | +0.39% | +0.74% | +0.85% |
| IS | +0.04% | +0.18% | +0.21% |
| OOS | +0.76% | +1.32% | +1.52% |

### `extended`

| Sample | 3d | 7d | 10d |
|---|---:|---:|---:|
| Full | +0.15% | +0.45% | +0.61% |
| IS | +0.02% | +0.32% | +0.19% |
| OOS | +0.27% | +0.57% | +1.01% |

解释：

- strict `down98` 之后，价格整体并不是一个明显“见顶/见底反转”形态
- 它更像一个偏温和、偏正的事件分布
- 单看总体均值，很难支撑“趋势结束提示”这个说法

## 2. Above-mid bucket is not weaker than below-mid bucket

这是主问题。

如果 `BBWP downcross 98%` 真提示趋势结束，那么按直觉：

- `above_mid` 桶应该更弱
- `below_mid` 桶应该更强

实际结果不是这样。

### Full sample

#### `pool`

| Horizon | Above mid | Below mid | Below - Above |
|---|---:|---:|---:|
| 3d | +0.67% | +0.15% | -0.52pp |
| 7d | +0.98% | +0.58% | -0.39pp |
| 10d | +0.76% | +1.31% | +0.55pp |

#### `extended`

| Horizon | Above mid | Below mid | Below - Above |
|---|---:|---:|---:|
| 3d | +0.33% | -0.00% | -0.34pp |
| 7d | +0.68% | +0.28% | -0.40pp |
| 10d | +0.59% | +0.68% | +0.09pp |

解释：

- 在 `3d / 7d`，两大 universe 都是 `above_mid` 桶更强
- 到 `10d`，`below_mid` 开始有一些追赶，但差异仍然不稳健
- 这和“上涨趋势那边应该先弱下来、下跌趋势那边应该先弹起来”的预期并不一致

### OOS sample

#### `pool`

| Horizon | Above mid | Below mid | Below - Above |
|---|---:|---:|---:|
| 3d | +0.89% | +0.85% | -0.04pp |
| 7d | +1.29% | +1.63% | +0.34pp |
| 10d | +1.27% | +2.34% | +1.07pp |

#### `extended`

| Horizon | Above mid | Below mid | Below - Above |
|---|---:|---:|---:|
| 3d | +0.40% | +0.12% | -0.28pp |
| 7d | +0.62% | +0.35% | -0.28pp |
| 10d | +0.74% | +0.91% | +0.17pp |

解释：

- `pool OOS` 的 `10d` 确实出现了你想找的方向：`below_mid` 明显强于 `above_mid`
- 但这个现象没有在 `extended` 上同步复制
- 而且桶间 diff 的检验全部 FDR 后不显著

所以这不能升格为稳健结论。

## 3. Reversal score does not support “trend-ending guidance”

这是这次研究最重要的一张表。

如果 `BBWP downcross 98%` 真对趋势结束有指导意义，那么：

- `mean(reversal_score)` 应该稳定为正

实际结果：

### Full sample

| Universe | 3d | 7d | 10d |
|---|---:|---:|---:|
| `pool` | -0.41% | -0.33% | +0.08% |
| `extended` | -0.23% | -0.15% | +0.07% |

### OOS sample

| Universe | 3d | 7d | 10d |
|---|---:|---:|---:|
| `pool` | -0.34% | -0.20% | +0.01% |
| `extended` | -0.16% | -0.00% | +0.16% |

关键解释：

- `3d / 7d` reversal score 大多是负的
- `10d` 虽然接近 0 或小幅转正，但完全不显著
- 也就是说，事件后的价格**没有稳定地反着当前趋势方向走**

这基本上直接回答了你的原问题：

按这套定义，strict `BBWP downcross 98%` 并没有展现出你希望看到的那种“趋势结束指导性”。

## What The Data Actually Suggests

更贴近数据的说法可能是：

1. `BBWP downcross 98%` 更像“极端波动开始降温”
2. 但“波动降温”不等于“原趋势结束”
3. 在很多情况下，尤其短周期里，价格仍然沿着原来的结构继续走，或者至少没有明显反向

换句话说：

- `BBWP` 可能在描述**波动率状态**
- 但它未必在描述**方向拐点**

这两个概念不能混为一谈。

## Final Conclusion

### Q1. 按“中轨上下”定义当前趋势，`BBWP downcross 98%` 之后的收益是否和当前趋势负相关？

回答：**没有稳定负相关。**

- `3d / 7d` 上，多数结果反而更接近 continuation，而不是 reversal
- `10d` 上有一些弱反转迹象，但不稳健、不显著、不能跨 universe 复制

### Q2. 这个信号能不能被理解为“趋势结束提示”？

回答：**目前不能。**

更准确的说法是：

- 它可能提示“极端波动阶段开始退潮”
- 但没有被证明能可靠指向“当前趋势即将反向”

### Q3. 这轮研究后应该怎么用它？

回答：**不要把 strict `BBWP downcross 98%` 当成一个单独的日频趋势反转信号。**

如果后面还要继续挖，我建议优先走这几条：

1. 保留这次的统计框架不变
2. 改研究对象，不再死盯 strict `down98`
3. 试 `high-zone turn down`，因为它比 strict cross 更贴近“波动率高位拐头”语义
4. 增加一个更直接的结构变量，例如 `price vs EMA20/EMA60` 或近端 swing 结构，而不是只用中轨
5. 把 horizon 拉到 `15 / 20` 看看反转是否只是更慢发生，而不是不存在

## Files Added

- Research module: [bbwp_signal_stats.py](/Users/owen/CC%20workspace/Finance/backtest/research/bbwp_signal_stats.py)
- Runner: [run_bbwp_signal_stats.py](/Users/owen/CC%20workspace/Finance/scripts/run_bbwp_signal_stats.py)
- Tests:
  - [test_bbwp_signal_stats.py](/Users/owen/CC%20workspace/Finance/tests/test_backtest/test_bbwp_signal_stats.py)
  - [test_daily_event_returns.py](/Users/owen/CC%20workspace/Finance/tests/test_backtest/test_daily_event_returns.py)

## Superseded Work

这份报告已经替代了前一版把问题做成 `PMARP + BBWP` 组合研究的方向。  
当前关于 `BBWP` 的有效性判断，应以这份 standalone signal-statistics study 为准。
