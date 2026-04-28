# PMARP + BBWP Daily Study

**Date:** 2026-04-22  
**Status:** Completed  
**Primary question:** `BBWP downcross 98%` 是否对“当前趋势可能结束”有指示意义，以及它是否能提升 `PMARP upcross 2%` 的质量。  
**Protocol:** [2026-04-22-pmarp-bbwp-daily-study-protocol.md](/Users/owen/CC%20workspace/Finance/docs/research/2026-04-22-pmarp-bbwp-daily-study-protocol.md)

## TL;DR

结论先写死：

1. `PMARP upcross 2%` 在这套新口径下依然成立，说明研究基础设施没有跑偏。
2. `BBWP downcross 98%` 不能被认定为一个跨 `pool` / `extended`、跨上下行趋势都稳定成立的“趋势结束”因子。
3. `BBWP` 作为 `PMARP` 过滤器，目前也**没有**拿到统计上过关的 accepted-vs-rejected 增量证据。
4. `BBWP high-zone turn down` 比 strict `downcross 98%` 更有信息量，但目前最多只能算候选上下文，不应作为生产硬过滤器。

## Research Design

### Universe

- `pool`: 153 symbols
- `extended`: 529 symbols

### Sample windows

- Full: `2021-07-01` ~ latest
- IS: `2021-07-01` ~ `2023-12-31`
- OOS: `2024-01-01` ~ latest

### Execution semantics

- Signal on day `T`
- Entry at `T+1 open`
- Exit at `T+H close`
- Return metric: stock return minus `SPY`

### Signals

- `PMARP upcross 2%`: `prev < 2.0 and curr >= 2.0`
- Primary `BBWP`: strict `downcross 98%` = `prev > 98.0 and curr <= 98.0`
- Secondary robustness: `high-zone turn down` = `prev > 98.0 and curr < prev`

### Statistics

- Event study with date-clustered effective `N`
- One-sample t-test for cohort mean excess return
- Welch t-test for accepted vs rejected `PMARP`
- BH-FDR inside each hypothesis family

## Validation

### Reproducible commands

```bash
.venv/bin/pytest tests/test_backtest/test_daily_event_returns.py tests/test_backtest/test_pmarp_bbwp_study.py -q
.venv/bin/pytest tests/test_bbwp.py tests/test_pmarp_signals.py tests/test_factor_study/test_event_study.py tests/test_factor_study/test_fdr.py -q
.venv/bin/python -m py_compile backtest/research/daily_event_returns.py backtest/research/pmarp_bbwp_study.py scripts/run_pmarp_bbwp_daily_study.py
.venv/bin/python scripts/run_pmarp_bbwp_daily_study.py
```

### Validation result

- New tests: `7 passed`
- Relevant legacy tests: `45 passed`
- Full study script: rerun completed and regenerated all CSV artifacts

### Artifact paths

- [universe_summary.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_bbwp_daily_study_20260422/universe_summary.csv)
- [cohort_counts.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_bbwp_daily_study_20260422/cohort_counts.csv)
- [event_results.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_bbwp_daily_study_20260422/event_results.csv)
- [comparison_results.csv](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_bbwp_daily_study_20260422/comparison_results.csv)
- [README.md](/Users/owen/CC%20workspace/Finance/backtest/new/pmarp_bbwp_daily_study_20260422/README.md)

## Baseline Sanity Check

先看 `PMARP upcross 2%` baseline，因为这是整个新 harness 是否可信的第一道门。

| Universe | Sample | Horizon | Neff | Mean Excess | p-FDR |
|---|---|---:|---:|---:|---:|
| `pool` | Full | 30d | 600 | +2.63% | 7.98e-05 |
| `pool` | Full | 60d | 572 | +6.18% | 1.01e-05 |
| `pool` | OOS | 30d | 293 | +3.12% | 0.0102 |
| `pool` | OOS | 60d | 265 | +9.47% | 0.00139 |
| `extended` | Full | 30d | 894 | +1.03% | 0.00901 |
| `extended` | Full | 60d | 865 | +2.99% | 8.38e-07 |
| `extended` | OOS | 30d | 455 | +0.94% | 0.203 |
| `extended` | OOS | 60d | 426 | +3.75% | 0.00143 |

解释：

- `PMARP` 的方向、窗口、量级与历史研究一致。
- 因此新研究管线的 `T+1 open -> T+H close` 语义、SPY 超额收益口径、日期聚类统计至少没有出现明显跑偏。

## Result 1: Standalone BBWP

### 1. Strict `BBWP downcross 98%` after prior downtrend

这是最接近“下跌趋势可能结束”的测试。

| Universe | Sample | 60d Mean Excess | p-FDR | Interpretation |
|---|---|---:|---:|---|
| `pool` | Full | +7.04% | 0.00238 | 成立 |
| `pool` | IS | +5.43% | 0.0199 | 成立 |
| `pool` | OOS | +8.56% | 0.0366 | 成立 |
| `extended` | Full | +1.63% | 0.154 | FDR 后不成立 |
| `extended` | IS | +1.49% | 0.137 | 不成立 |
| `extended` | OOS | +1.75% | 0.365 | 不成立 |

结论：

- strict `down98` 在 `pool` 的 prior-downtrend 场景里有东西。
- 但它在 `extended` 上没有稳健复制出来，所以不能直接升级成“普适 BBWP 因子”。

### 2. Strict `BBWP downcross 98%` after prior uptrend

如果 `BBWP` 真是在抓“趋势结束”，那 prior-uptrend 场景里，后续收益应该至少明显转弱，理想情况下偏负。

实际结果不是这样：

- `pool` `IS` 60d: `-1.68%`, `p-FDR 0.403`
- `pool` `OOS` 60d: `+5.43%`, `p-FDR 0.0949`
- `extended` `IS` 60d: `-1.47%`, `p-FDR 0.120`
- `extended` `OOS` 60d: `+2.43%`, `p-FDR 0.157`

结论：

- sign 在不同 universe / sample 之间不稳定，且多数结果不显著。
- 这直接削弱了“BBWP strict down98 是通用趋势结束因子”的说法。

### 3. `BBWP high-zone turn down`

这个 robustness 比 strict `downcross 98%` 更有信息量，尤其是在 `pool` 的 prior-downtrend 场景：

| Universe | Sample | 60d Mean Excess | p-FDR |
|---|---|---:|---:|
| `pool` | Full | +7.28% | 1.65e-05 |
| `pool` | IS | +5.08% | 0.00290 |
| `pool` | OOS | +9.32% | 0.00493 |

但它仍然不是一个已经完成验证的“趋势结束通用因子”：

- `extended` prior-downtrend OOS 60d 只有 `+0.63%`, `p-FDR 0.598`
- `extended` prior-uptrend 场景在 IS 为负、OOS 为正，sign flip 明显

结论：

- `high-zone turn down` 比 strict `down98` 更值得继续研究。
- 但它的证据主要集中在 `pool + prior-downtrend`，并没有跨 universe 稳健成立。

## Result 2: PMARP + BBWP Filter Lift

这部分才是主问题。  
不是问“accepted PMARP 自己均值高不高”，而是问“它是否显著优于 rejected PMARP”。

### Event counts

strict 同日过滤本身就很稀：

| Universe | `PMARP up2 base` | `accept down98 same-day` | Share |
|---|---:|---:|---:|
| `pool` | 2552 | 45 | 1.76% |
| `extended` | 8671 | 136 | 1.57% |

更宽的 `high-turn recent3` 稍好，但仍只是子集：

| Universe | `PMARP up2 base` | `accept highturn recent3` | Share |
|---|---:|---:|---:|
| `pool` | 2552 | 187 | 7.33% |
| `extended` | 8671 | 685 | 7.90% |

### Accepted vs rejected: 60d primary comparison

| Universe | Sample | Filter | Accepted | Rejected | Diff | p-FDR |
|---|---|---|---:|---:|---:|---:|
| `pool` | Full | `down98 same-day` | +14.35% | +5.54% | +8.80pp | 0.525 |
| `pool` | Full | `down98 recent3` | +17.25% | +5.01% | +12.24pp | 0.525 |
| `pool` | Full | `highturn same-day` | +16.85% | +4.86% | +11.99pp | 0.412 |
| `pool` | Full | `highturn recent3` | +17.40% | +4.32% | +13.07pp | 0.412 |
| `pool` | OOS | `highturn recent3` | +25.96% | +5.86% | +20.10pp | 0.594 |
| `extended` | Full | `down98 recent3` | +6.65% | +2.63% | +4.03pp | 0.932 |
| `extended` | OOS | `down98 recent3` | +11.30% | +2.99% | +8.31pp | 0.923 |
| `extended` | Full | `highturn recent3` | +4.36% | +2.68% | +1.67pp | 0.932 |

结论：

1. 所有 filter 的 accepted cohort 往往比 rejected cohort 高，这是肉眼上“有点意思”的地方。
2. 但 **没有任何 accepted-vs-rejected 对比在 FDR 后成立**。
3. 因此本轮不能宣称 `BBWP` 对 `PMARP upcross 2%` 有统计上过关的 conditional lift。

### Most interesting but still unproven candidate

最值得继续盯的是：

- `pool`
- `PMARP up2`
- `BBWP high-zone turn down in recent 3 days`
- `60d` horizon

它的表现是：

- Full: `+17.40%` vs rejected `+4.32%`, diff `+13.07pp`, `p=0.0644`, `p-FDR=0.412`
- OOS: `+25.96%` vs rejected `+5.86%`, diff `+20.10pp`, `p=0.1270`, `p-FDR=0.594`

这是“值得继续追”的信号，但离“可以写进生产因子库”还差一截。

## Final Answer To The Research Question

### Q1. `BBWP` 本身是否有效？

回答：**局部有效，但不足以称为稳健普适因子。**

- 在 `pool + prior-downtrend` 场景，strict `down98` 和 `high-turn` 都有明显正向信息。
- 但一到 `extended` 或 prior-uptrend 场景，结论就明显变弱甚至翻向。
- 所以更准确的说法是：`BBWP` 可能是一个对某些横截面和上下文有用的波动率衰竭/压缩转折 lens，而不是通用的趋势结束因子。

### Q2. `BBWP` 是否提升 `PMARP upcross 2%`？

回答：**目前没有被证明。**

- accepted cohort 常常更高，但 accepted-vs-rejected 的统计证据不够。
- strict `down98` 样本尤其稀，功效不足是硬约束。
- `high-turn` 比 strict 更 promising，但本轮仍未过线。

### Q3. 这次研究后的生产决策是什么？

回答：**不要把 `BBWP` 直接并入 PMARP 生产过滤器。**

当前更合理的定位是：

- `PMARP upcross 2%` 继续保留为已验证 signal
- `BBWP` 保留为研究候选上下文
- 若继续，优先研究 `high-zone turn down + prior-downtrend + pool + 60d`

## What Changed In Code

- New research helpers under [backtest/research](/Users/owen/CC%20workspace/Finance/backtest/research)
- New runner: [run_pmarp_bbwp_daily_study.py](/Users/owen/CC%20workspace/Finance/scripts/run_pmarp_bbwp_daily_study.py)
- New tests:
  - [test_daily_event_returns.py](/Users/owen/CC%20workspace/Finance/tests/test_backtest/test_daily_event_returns.py)
  - [test_pmarp_bbwp_study.py](/Users/owen/CC%20workspace/Finance/tests/test_backtest/test_pmarp_bbwp_study.py)

## Known Caveats

1. `extended` 仍然是当前可用 universe，不是完全 PIT 的历史 membership。
2. strict `down98` 的 PMARP acceptance 比例只有 `~1.6% - 1.8%`，统计功效天生吃亏。
3. 本研究回答的是“日频事件研究”和“conditional lift”，不是完整组合策略最优解问题。

## Recommendation

如果下一轮还要继续，不建议先上 portfolio backtest，而建议按下面顺序：

1. 固定 `PMARP up2` baseline 不动
2. 只追 `BBWP high-zone turn down`
3. 只看 `prior-downtrend` context
4. 只看 `pool`
5. 再引入一个真正独立的上下文变量，而不是继续在 `98%` 阈值附近打磨

这轮研究已经足够回答最核心的问题：  
`BBWP` 现在还不能被当成一个已经验证完成、可直接提升 `PMARP` 的硬过滤器。
