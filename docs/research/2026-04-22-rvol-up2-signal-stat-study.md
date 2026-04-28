# RVOL Cross-Up 2σ Signal Statistics Study

**Date:** 2026-04-22  
**Status:** Completed  
**Scope:** First-pass exploratory statistics study, no IS/OOS split

## TL;DR

结论先写死：

1. `RVOL_150 cross_up 2σ` 不是噪音事件。
2. 在 `pool` 和 `extended` 两个 universe 上，它后续 `5 / 10 / 20 / 40` 日**原始收益整体为正**，而且 horizon 越长越强。
3. 但它**不是一个干净的左侧抄底信号**。从 `SPY excess` 看，最稳的不是 `panic` 桶，而是 **positive-day / high-state / churn** 这类“放量确认”场景。
4. 你提出的“同一只股票密集重复 spike 会把统计吹虚”这个担心是对的。加入 `same-symbol horizon de-overlap` 后，样本量明显收缩，但主结论仍然成立。
5. 当前最接近数据的说法是：`RVOL` 更像“有事情正在发生”的成交量冲击事件。在强股票池里，它偏趋势确认；在更大 universe 里，它还有 edge，但更像右尾驱动，而不是厚中位数 / 高胜率型信号。

## Why This Is A Statistics Study, Not A Backtest

这次的问题不是：

- “用 `RVOL` 做一套完整交易策略能赚多少钱？”
- “这个事件该怎么配仓、持有多久、怎么控制并发？”

而是更底层的问题：

- `RVOL_150 cross_up 2σ` 出现后，未来 `5 / 10 / 20 / 40` 日收益分布到底是什么样？

所以这次故意做成**事件统计**而不是组合回测，原因有三个：

1. 先验证信号本体，再谈交易实现。
2. 如果一上来就做回测，仓位、持有期、再平衡、成本、并发上限这些二级设计会污染你对信号含义的判断。
3. 你这轮要的是“先尝尝咸淡”，那最诚实的第一步就是先看事件后分布。

## Frozen Methodology

### Signal

- `RVOL_150 cross_up 2σ` = `prev_rvol <= 2.0 and curr_rvol > 2.0`

### RVOL parameters

- `lookback = 150`
- `threshold = 2.0`

RVOL 定义与仓库中的 [rvol.py](/Users/owen/CC%20workspace/Finance/src/indicators/rvol.py:20) 一致：

- `RVOL_t = (Vol_t - mean(Vol_{t-150:t-1})) / std(Vol_{t-150:t-1})`

### Forward returns

这次看两套口径：

1. **Raw close-to-close**
   - `ret_h = close[T+h] / close[T] - 1`
2. **Excess vs SPY**
   - `ret_h_excess = ret_h_stock - ret_h_SPY`

其中 `h ∈ {5, 10, 20, 40}`。

### Universes

- `pool`
- `extended`

### No IS/OOS split in this pass

这轮刻意**不做 IS/OOS**，因为目标是先看全体事件后分布，建立第一层直觉。  
如果后面要把 `RVOL` 升格成正式因子，再做 OOS hardening。

### Statistical discipline

这次不是裸统计，做了两层独立性修正：

#### 1. Date clustering

同一天多个股票一起触发时，先对同日事件收益取横截面均值，再在日期层做 t-test。  
这样不会因为横截面相关性把显著性吹高。

#### 2. Same-symbol horizon de-overlap

这是这次新加的规则，也是你刚才认的口径：

- 算 `20d` 分布时，同一只股票在一次触发后的未来 `20` 个交易日内，不允许第二个事件再进入样本
- `40d` 也是同理

这相当于把密集连续 spike 视为**同一个 tradeable episode**，而不是反复记样本。

### Diagnostic buckets

为了回答“RVOL 是 panic、base breakout，还是 trend-end churn”这个解释问题，这次额外做了几组**诊断桶**。它们不是新因子，只是解释层：

#### Event-day move buckets

- `sign_neg`: 信号日跌幅 `< -1%`
- `sign_flat`: 信号日涨跌在 `[-1%, +1%]`
- `sign_pos`: 信号日涨幅 `> +1%`

#### State buckets via PMARP

- `pmarp_low`: `PMARP < 20`
- `pmarp_mid`: `20 <= PMARP <= 80`
- `pmarp_high`: `PMARP > 80`

#### Three proxy scenarios

- `panic_proxy` = `sign_neg + pmarp_low`
- `base_proxy` = `sign_flat + pmarp_mid`
- `churn_proxy` = `sign_pos + pmarp_high`

这些 proxy 是为了帮助解释，不是这轮研究要宣称成立的新信号。

## Reproducibility

### Commands

```bash
.venv/bin/pytest tests/test_backtest/test_rvol_signal_stats.py tests/test_backtest/test_pmarp_signal_stats.py tests/test_backtest/test_bbwp_signal_stats.py tests/test_backtest/test_daily_event_returns.py -q
.venv/bin/python -m py_compile backtest/research/rvol_signal_stats.py scripts/run_rvol_signal_stats.py
.venv/bin/python scripts/run_rvol_signal_stats.py --report-date 2026-04-22
```

### Validation

- Tests: `15 passed`
- `py_compile`: passed
- Full run: completed successfully

### Artifact paths

- [universe_summary.csv](/Users/owen/CC%20workspace/Finance/backtest/new/rvol_up2_signal_stats_20260422/universe_summary.csv)
- [bucket_counts.csv](/Users/owen/CC%20workspace/Finance/backtest/new/rvol_up2_signal_stats_20260422/bucket_counts.csv)
- [event_stats.csv](/Users/owen/CC%20workspace/Finance/backtest/new/rvol_up2_signal_stats_20260422/event_stats.csv)
- [README.md](/Users/owen/CC%20workspace/Finance/backtest/new/rvol_up2_signal_stats_20260422/README.md)

## Sample Size And De-Overlap

先看最外层 raw trigger 数：

| Universe | All raw triggers | Symbols |
|---|---:|---:|
| `pool` | 5,853 | 152 |
| `extended` | 21,947 | 523 |

但这不是最终有效样本。  
加上 `same-symbol horizon de-overlap` 后，样本会按 horizon 明显收缩：

### `pool / all`

| Horizon | Raw triggers | Dedup events | Date-clustered `Neff` |
|---|---:|---:|---:|
| 5d | 5,853 | 4,854 | 1,001 |
| 10d | 5,853 | 4,248 | 960 |
| 20d | 5,853 | 3,417 | 888 |
| 40d | 5,853 | 2,380 | 800 |

### `extended / all`

| Horizon | Raw triggers | Dedup events | Date-clustered `Neff` |
|---|---:|---:|---:|
| 5d | 21,947 | 18,076 | 1,146 |
| 10d | 21,947 | 15,631 | 1,133 |
| 20d | 21,947 | 12,324 | 1,111 |
| 40d | 21,947 | 8,559 | 1,057 |

这个变化本身就说明：  
如果不做同 symbol 去重，`RVOL` 这类事件的确很容易把统计吹虚。

## Results

## 1. Overall signal is positive in both universes

先不分桶，看最基本的 `rvol_up2_all`。

### Raw returns

#### `pool`

| Horizon | Dedup N | `Neff` | Mean event | Median event | Hit rate | p-FDR |
|---|---:|---:|---:|---:|---:|---:|
| 5d | 4,854 | 1,001 | +0.91% | +0.40% | 53.8% | 2.12e-04 |
| 10d | 4,248 | 960 | +1.69% | +0.86% | 56.0% | 6.10e-07 |
| 20d | 3,417 | 888 | +2.80% | +1.55% | 56.7% | 2.28e-08 |
| 40d | 2,380 | 800 | +5.06% | +2.22% | 55.6% | 7.41e-09 |

#### `extended`

| Horizon | Dedup N | `Neff` | Mean event | Median event | Hit rate | p-FDR |
|---|---:|---:|---:|---:|---:|---:|
| 5d | 18,076 | 1,146 | +0.51% | +0.43% | 54.6% | 0.00169 |
| 10d | 15,631 | 1,133 | +0.81% | +0.72% | 55.4% | 1.36e-05 |
| 20d | 12,324 | 1,111 | +1.69% | +1.36% | 57.1% | 7.29e-10 |
| 40d | 8,559 | 1,057 | +3.08% | +2.04% | 57.4% | 5.88e-14 |

解释：

- `RVOL_150 cross_up 2σ` 在两个 universe 上都不是零效应。
- `raw return` 下，信号后收益整体为正，而且 horizon 越长越强。
- 这说明放量上穿 `2σ` 至少抓到了“市场开始认真交易这件事”的时刻。

## 2. Excess vs SPY is also positive, but weaker and less median-driven

如果只看 raw return，很可能只是 beta / 市场同步推动。  
所以第二张表更关键：`SPY excess`。

### Excess vs SPY

#### `pool`

| Horizon | `Neff` | Mean event | Median event | Hit rate | p-FDR |
|---|---:|---:|---:|---:|---:|
| 5d | 1,001 | +0.58% | +0.09% | 51.0% | 0.00330 |
| 10d | 960 | +1.11% | +0.28% | 52.1% | 9.40e-05 |
| 20d | 888 | +1.64% | +0.22% | 51.1% | 4.90e-05 |
| 40d | 800 | +3.09% | +0.37% | 51.1% | 1.18e-05 |

#### `extended`

| Horizon | `Neff` | Mean event | Median event | Hit rate | p-FDR |
|---|---:|---:|---:|---:|---:|
| 5d | 1,146 | +0.12% | -0.00% | 50.0% | 0.454 |
| 10d | 1,133 | +0.28% | +0.03% | 50.2% | 0.333 |
| 20d | 1,111 | +0.50% | -0.06% | 49.6% | 0.0245 |
| 40d | 1,057 | +0.99% | -0.10% | 49.5% | 0.0223 |

解释：

- `pool` 里的结果更像真 alpha：均值为正，`p-FDR` 明显过线，虽然中位数不高，但至少没有塌。
- `extended` 里仍然是正的，`20d / 40d` 也过线，但中位数转负、hit rate 掉到 `50%` 附近。
- 这意味着在更大 universe 上，`RVOL` 的 edge 更像**右尾驱动**，不是“多数事件都稳稳赚钱”的那种厚信号。

## 3. This does not look like a pure panic / left-side signal

如果 `RVOL` 的本质是“恐慌出清”，那么应该看到：

- `sign_neg`
- `pmarp_low`
- `panic_proxy`

这些桶在 `SPY excess` 上最强。

实际不是这样。

### `extended / excess_spy` 最值得看的几行

| Bucket | Horizon | Mean event | `Neff` | p-FDR |
|---|---:|---:|---:|---:|
| `sign_pos` | 20d | +0.81% | 1,025 | 0.0223 |
| `churn_proxy` | 20d | +0.92% | 898 | 0.0434 |
| `pmarp_high` | 20d | +0.67% | 948 | 0.0565 |
| `sign_neg` | 20d | +0.43% | 1,012 | 0.319 |
| `panic_proxy` | 20d | +0.30% | 895 | 0.766 |

### `pool / excess_spy` 最强的几行

| Bucket | Horizon | Mean event | `Neff` | p-FDR |
|---|---:|---:|---:|---:|
| `sign_pos` | 10d | +1.65% | 745 | 0.00010 |
| `sign_pos` | 20d | +2.39% | 702 | 1.85e-05 |
| `churn_proxy` | 20d | +2.40% | 567 | 0.000656 |
| `pmarp_high` | 20d | +2.19% | 602 | 0.000656 |
| `panic_proxy` | 20d | +1.29% | 540 | 0.00822 |

解释：

- `panic` 场景不是没有收益，但它不是这轮里最稳的解释。
- 真正跑出来更强的，反而是 `positive-day / high-state / churn` 这类场景。
- 这说明 `RVOL` 至少在美股日频上，不能被简单口头化成“放量=恐慌抄底”。

## 4. Positive-day RVOL looks more like confirmation than exhaustion

这个是这轮最重要的解释结论。

如果 `RVOL` 更像“趋势末端换手 / 即将结束”，那么理论上：

- `sign_pos`
- `churn_proxy`
- `pmarp_high`

这些桶应该在后续表现较差，至少不该是最强桶。

实际结果正相反：

- `pool / excess_spy`：`sign_pos` 在 `10 / 20 / 40d` 都很强
- `extended / excess_spy`：唯一稳定过线的诊断桶也是 `sign_pos 20d`
- `raw` 口径下，`sign_pos` 在两个 universe 的 `20 / 40d` 都很强

所以按这轮定义，`RVOL_150 cross_up 2σ` **更像参与度上升 / 趋势确认**，不像“趋势结束提示器”。

## 5. But the signal is not clean enough yet to call it a finished factor

虽然这轮结果是正的，但还不能直接说“RVOL 已经是第二个正式因子”，原因有三个：

1. 这还是 first-pass，全样本 exploratory，没有 OOS hardening。
2. `extended / excess` 的中位数和 hit rate 说明它不是厚信号，更像右尾驱动。
3. 目前还没有回答它和 `PMARP upcross 2%` 的独立性问题。

所以更准确的说法是：

- **`RVOL` 通过了“值得继续追”的门槛**
- 但还**没通过“已经可以正式入库”**的门槛

## Final Conclusion

### Q1. `RVOL_150 cross_up 2σ` 之后，未来收益分布是什么？

回答：**整体为正。**

- `pool / raw`: `5d +0.91%`, `10d +1.69%`, `20d +2.80%`, `40d +5.06%`
- `pool / excess`: `5d +0.58%`, `10d +1.11%`, `20d +1.64%`, `40d +3.09%`
- `extended / raw`: `5d +0.51%`, `10d +0.81%`, `20d +1.69%`, `40d +3.08%`
- `extended / excess`: `20d +0.50%`, `40d +0.99%`

### Q2. 它更像左侧 panic，还是右侧 confirmation？

回答：**这轮数据更支持右侧 confirmation。**

- `panic_proxy` 不是最强桶
- `sign_pos / churn_proxy / pmarp_high` 更常出现在显著结果里

### Q3. 去重规则重要吗？

回答：**非常重要。**

`RVOL` 事件天然容易密集重复。  
如果不做 `same-symbol horizon de-overlap`，这个因子很容易被“同一波事件反复记样本”吹虚。

### Q4. 这轮之后最值钱的下一步是什么？

回答：两个方向，优先级都很高：

1. **OOS hardening**
   - 只对这轮最像真的桶做显式时间切分
   - 候选优先看：`sign_pos`、`churn_proxy`
2. **与 `PMARP upcross 2%` 的独立性审计**
   - 拆成 `RVOL-only / PMARP-only / overlap`
   - 如果收益主要来自 overlap，那 `RVOL` 不是第二因子，只是确认器

## Caveat

这次的诊断桶是**解释层 proxy**，不是生产定义。

尤其：

- `panic_proxy = sign_neg + pmarp_low`
- `churn_proxy = sign_pos + pmarp_high`

它们是为了帮你理解 `RVOL` 更像哪种市场机制，不是这轮就要直接写进策略或因子库的最终形态。
