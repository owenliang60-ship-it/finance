# RVOL 深入研究：条件变量、强势确认与右尾路径

**日期:** 2026-04-24  
**状态:** 已完成  
**范围:** 离线 R&D 事件研究；不进入生产信号；不产生交易建议

## 一句话结论

RVOL 不适合升级为 PMARP 的过滤器，也不适合作为独立买入信号。  
但 RVOL 在 `PMARP >= 60` 的强势状态里，尤其叠加“当日上涨 + 收盘靠近高点”后，有稳定的相对 SPY 超额收益证据。它更像 **强势确认 / attention context**，不是高胜率 alpha trigger。

最终判断：

| 研究问题 | 结论 | 处理 |
|---|---|---|
| RVOL 能否提升 PMARP upcross 2%？ | 否 | 不作为 PMARP filter 推进 |
| RVOL 是否能确认强势状态？ | 是，尤其 `PMARP>=60 + sign_pos + close_near_high` | 可作为技术分析层候选 context |
| RVOL edge 是否依赖右尾？ | 是 | 不能按高胜率信号使用 |
| 事件解释层是否足够支持结论？ | 财报覆盖部分可用，社交全历史不可用 | 只做解释附录 |

## 方法论冻结

### 数据与股票池

| 股票池 | symbols | 日期范围 | close 覆盖 | volume 覆盖 | high/low 覆盖 |
|---|---:|---|---:|---:|---:|
| `pool` | 153 | 2021-02-01 至 2026-04-23 | 100% | 100% | 100% |
| `extended` | 529 | 2021-02-01 至 2026-04-23 | 100% | 100% | 100% |

`extended` 的 volume 覆盖门卫通过，本轮可以解释 extended RVOL 结果。

### 参数

- RVOL: `lookback=150`, `threshold=2.0`，单位是 **2σ z-score**，不是 2 倍成交量
- PMARP: `EMA=20`, `lookback=150`
- PMARP 主信号: `PMARP upcross 2.0`
- 持有期: `5 / 10 / 20 / 40 / 60`
- 收益口径: 原始 close-to-close + 相对 SPY 超额收益
- 统计纪律: 同标的 horizon 去重 + 日期聚类 t-test + BH-FDR

### 产物路径

- `backtest/new/rvol_deep_research_20260424/universe_summary.csv`
- `backtest/new/rvol_deep_research_20260424/cohort_counts.csv`
- `backtest/new/rvol_deep_research_20260424/event_stats.csv`
- `backtest/new/rvol_deep_research_20260424/conditional_lift.csv`
- `backtest/new/rvol_deep_research_20260424/tail_diagnostics.csv`
- `backtest/new/rvol_deep_research_20260424/event_explainers.csv`

## 1. PMARP + RVOL 条件提升：失败

这是本轮最高优先级问题：RVOL 能不能提高 `PMARP upcross 2%` 的质量？

### 事件数量

| 股票池 | PMARP up2 base | RVOL same-day accepted | RVOL recent3 accepted | RVOL recent5 accepted |
|---|---:|---:|---:|---:|
| `pool` | 2,870 | 181 | 718 | 1,107 |
| `extended` | 9,936 | 642 | 2,720 | 4,071 |

### 关键结果：相对 SPY 超额收益

| 股票池 | RVOL 条件 | Horizon | Accepted mean | Rejected mean | 差值 | Accepted 胜率 | Rejected 胜率 | p-FDR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `pool` | same-day | 60d | +8.07% | +3.04% | +5.03% | 54.4% | 47.7% | 0.689 |
| `pool` | recent3 | 60d | +2.86% | +3.81% | -0.95% | 49.1% | 49.9% | 0.728 |
| `pool` | recent5 | 60d | +3.04% | +4.46% | -1.42% | 50.9% | 49.8% | 0.689 |
| `extended` | same-day | 60d | +2.61% | +1.85% | +0.76% | 52.5% | 50.5% | 0.937 |
| `extended` | recent3 | 60d | +2.03% | +2.06% | -0.02% | 52.1% | 51.3% | 0.999 |
| `extended` | recent5 | 60d | +1.95% | +2.17% | -0.23% | 52.0% | 51.1% | 0.433 |

读法：

- `same-day RVOL` 在 `pool` 里看起来有很大的均值提升，但样本只有 181 个 raw events，FDR 后完全不过线。
- `recent3 / recent5` 没有 lift，甚至在多个口径下 accepted 比 rejected 更弱。
- `extended` 没有任何支持 RVOL 提升 PMARP 的证据。

**Gate A 结论:** 不通过。RVOL 不应作为 PMARP upcross 2% 的过滤器推进。

## 2. 强势状态确认：通过，但不是高胜率信号

第二个问题：RVOL 是否在强势状态里有“确认/加速”意义？

核心 cohort：

- `rvol_up2_pmarp_gte60`
- `rvol_up2_pmarp_gte60_sign_pos`
- `rvol_up2_pmarp_gte60_close_near_high`
- `rvol_up2_pmarp_gte60_sign_pos_close_near_high`

### `pool`：相对 SPY 超额收益

| Cohort | 20d mean | 40d mean | 60d mean | 20d p-FDR | 60d p-FDR |
|---|---:|---:|---:|---:|---:|
| `PMARP>=60` | +2.05% | +2.92% | +3.73% | 0.000470 | 0.000561 |
| `PMARP>=60 + sign_pos` | +2.30% | +3.18% | +4.15% | 0.000234 | 0.000470 |
| `PMARP>=60 + close_near_high` | +2.13% | +3.32% | +4.12% | 0.000520 | 0.00134 |
| `PMARP>=60 + sign_pos + close_near_high` | +2.40% | +3.61% | +4.61% | 0.000360 | 0.000642 |

### `extended`：相对 SPY 超额收益

| Cohort | 20d mean | 40d mean | 60d mean | 20d p-FDR | 60d p-FDR |
|---|---:|---:|---:|---:|---:|
| `PMARP>=60` | +0.62% | +0.80% | +1.12% | 0.00685 | 0.00897 |
| `PMARP>=60 + sign_pos` | +0.94% | +1.02% | +1.63% | 0.00298 | 0.00758 |
| `PMARP>=60 + close_near_high` | +0.84% | +1.17% | +1.52% | 0.00897 | 0.0168 |
| `PMARP>=60 + sign_pos + close_near_high` | +0.93% | +1.26% | +1.74% | 0.00685 | 0.00758 |

读法：

- 强势 RVOL 在 `pool` 和 `extended` 都成立，而且是 SPY excess，不只是市场 beta。
- `sign_pos + close_near_high` 对强势桶有增量，说明事件日价格结构确实有解释力。
- 但胜率仍然不是 PMARP 那种厚胜率。`extended` 里 20d 胜率约 52%，40/60d 又回到 49% 附近。

**Gate B 结论:** 有条件通过。RVOL 可以作为强势状态确认变量推进，但不能被描述为高胜率独立信号。

## 3. 低 PMARP 放量：raw 反弹强，超额证据弱

低 PMARP 的 RVOL 事件在 raw return 上很好看：

| 股票池 | Cohort | 20d raw | 40d raw | 60d raw |
|---|---|---:|---:|---:|
| `pool` | `PMARP<30 + sign_neg` | +2.81% | +4.80% | +6.51% |
| `extended` | `PMARP<30 + sign_neg` | +2.09% | +3.36% | +4.95% |

但换成 SPY excess 后：

| 股票池 | Cohort | 20d excess | 40d excess | 60d excess | 60d p-FDR |
|---|---|---:|---:|---:|---:|
| `pool` | `PMARP<30 + sign_neg` | +1.27% | +2.62% | +3.33% | 0.0171 |
| `extended` | `PMARP<30 + sign_neg` | +0.35% | +0.86% | +1.46% | 0.0269 |

读法：

- 低 PMARP 放量确实能抓反弹，但里面有明显市场反弹成分。
- `extended` 的 60d 过线，但 20/40d 不稳，且胜率仍然不到 50%。
- 这条线不能直接上升为“panic bottom”交易规则。

## 4. 右尾 / MFE / MAE 路径诊断

RVOL 的问题不是完全没 edge，而是 edge 形态不适合被当成高胜率信号。

### 强势 RVOL 路径

| 股票池 | Cohort | Horizon | Mean | Median | P10 | P90 | MFE 均值 | MAE 均值 | MFE/MAE |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `pool` | `PMARP>=60 + sign_pos + close_near_high` | 60d | +7.55% | +1.61% | -22.73% | +35.79% | +26.30% | -14.20% | 1.85 |
| `extended` | `PMARP>=60 + sign_pos + close_near_high` | 60d | +4.12% | +2.06% | -16.27% | +22.16% | +16.23% | -10.87% | 1.49 |

### PMARP 主信号路径对比

| 股票池 | Cohort | Horizon | Mean | Median | P10 | P90 | MFE/MAE |
|---|---|---:|---:|---:|---:|---:|---:|
| `pool` | `PMARP up2 base` | 60d | +8.81% | +4.42% | -17.25% | +36.88% | 1.75 |
| `pool` | `PMARP up2 accepted RVOL recent3` | 60d | +7.78% | +3.60% | -17.13% | +32.90% | 1.67 |
| `extended` | `PMARP up2 base` | 60d | +6.26% | +4.62% | -12.46% | +25.11% | 1.63 |
| `extended` | `PMARP up2 accepted RVOL recent3` | 60d | +5.98% | +4.21% | -12.60% | +23.67% | 1.59 |

读法：

- RVOL 没有改善 PMARP 的路径质量，accepted RVOL 反而略弱。
- 强势 RVOL 的 MFE/MAE 还可以，但 P10 很深，说明路径风险不小。
- RVOL 更适合作为“候选事件进入观察/解释”的触发器，而不是直接仓位规则。

**Gate C 结论:** 部分通过。右尾确实存在，MFE/MAE 可研究，但还不足以直接策略化。

## 5. 事件解释层覆盖率

### 财报覆盖

| 股票池 | 财报覆盖 | 状态 |
|---|---:|---|
| `pool` | 100% | 可用 |
| `extended` | 约 26-29% | 探索性 |

`pool` 中 RVOL 事件接近财报的比例大概 9-11%，高于 PMARP up2 base 的约 4-5%，说明财报确实解释了一部分 RVOL spike，但不是主解释。

### 社交覆盖

| 股票池 | 全历史 social 覆盖 | 2025-12 后覆盖 | 状态 |
|---|---:|---:|---|
| `pool` | 约 4.6-8.4% | 约 58.5-81.5% | 只对子样本可用 |
| `extended` | 约 1.1-2.3% | 约 11.3-22.1% | 探索性 |

这符合 plan 预期：社交数据全历史不够，不能用来支持 2021-2026 的 RVOL 结论。

## 升级 / 降级判断

| Gate | 结果 | 判断 |
|---|---|---|
| Gate A: PMARP conditional lift | 不通过 | RVOL 不做 PMARP filter |
| Gate B: Strong-state confirmation | 通过 | RVOL 可作为强势确认 context |
| Gate C: Tail-event usage | 部分通过 | 需要后续做路径/退出研究 |

## 最终定位

RVOL 的定位应该是：

> **技术分析层的 context / confirmation factor，用于识别“市场正在认真交易某个强势状态”，而不是独立 alpha trigger。**

具体落地建议：

1. 不要继续做 RVOL 单因子阈值 sweep。
2. 不要把 RVOL 加到 PMARP upcross 2% 的过滤条件里。
3. 可以保留 `PMARP>=60 + RVOL + sign_pos + close_near_high` 作为强势确认候选。
4. 如果未来策略层使用 RVOL，必须配合右尾型仓位设计、止损/退出研究，而不是按胜率型信号处理。

## 可复现命令

```bash
/Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest \
  tests/test_backtest/test_rvol_signal_stats.py \
  tests/test_backtest/test_rvol_deep_research.py \
  tests/test_backtest/test_event_path_diagnostics.py \
  tests/test_backtest/test_rvol_event_explainers.py \
  -q

/Users/owen/CC\ workspace/Finance/.venv/bin/python scripts/run_rvol_deep_research.py --report-date 2026-04-24
```

验证结果：

- Tests: `15 passed`
- `py_compile`: passed
- Full run: completed successfully
