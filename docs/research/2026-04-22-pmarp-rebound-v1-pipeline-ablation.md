# PMARP Rebound V1 Pipeline Ablation — Negative Finding

**Date:** 2026-04-22
**Status:** Archived (not production-ready)
**Branch:** `research/pmarp-rebound-v1`
**Parent:** `codex/backtest-pipeline-v3` @ `123e897`

## Why this exists

这份研究的上位研究是 **PMARP upcross 2% 跨市场独立验证** (2026-04-11)，那次结论是事件级均值上美股 166 只 60d +5.87% (p-FDR=0.028)、A 股 4938 只 V2 66.2% 胜率。

本次要回答的下一步问题：**那个 event-level alpha 能否在 V3 cross-sectional daily-rebalance pipeline 里 survive？**

答案：**不能。** 33 份参数扫描里没有一份能同时在 IS 和 OOS 都跑赢 SPY。作为 negative finding 存档。

## Scope of archive

### Infrastructure changes（9 个 M 文件）

为了让 event-style factor 能塞进 daily-rebalance pipeline：

- `backtest/pipeline/factors/_base.py` — `PipelineFactor` 加可选 `compute_panel()`，返回 panel 而不是 per-date dict
- `backtest/pipeline/factors/registry.py` — 注册 `PMARPReboundV1PipelineFactor`
- `backtest/pipeline/primitives/signal_engine.py` — 支持 panel-based score 注入（+100 行）
- `backtest/pipeline/primitives/universe_builder.py` — 支持 10B 市值阈值 + sector 过滤扩展（+84 行）
- `backtest/pipeline/primitives/evaluation.py` / `runner.py` / `spec.py` — 小幅适配
- `tests/pipeline/test_spec.py` / `test_universe_builder.py` — 相应测试

### Factor & specs

- `backtest/pipeline/factors/pmarp_rebound_v1.py` — event-style factor：PMARP 上穿 2 + RVOL z-score ≥ 阈值 + SPY EMA120/144 regime filter，触发后持有 `holding_window_days` 交易日
- `backtest/specs/pipeline_pmarp_rebound_v1.yaml` — base: hold30, t=2.0, rvol≥2
- `backtest/specs/pipeline_pmarp_rebound_v1_hold20.yaml` — hold20
- `backtest/specs/pipeline_pmarp_rebound_v1_peak7.yaml` — recent 7 日 RVOL peak 确认
- `backtest/specs/pipeline_pmarp_rebound_v1_peak7_longsplit.yaml` — peak7 + long/short split
- `backtest/specs/pipeline_pmarp_rebound_v1_soft05.yaml` — soft threshold (floor=0.5)
- `backtest/specs/pipeline_pmarp_rebound_v1_soft05_longsplit.yaml` — soft05 + long/short split
- `backtest/specs/pipeline_pmarp_breadth_campaign_base.yaml` — breadth campaign base

### Runner & tests

- `scripts/run_pmarp_breadth_campaign.py` — breadth campaign runner，扫 top_n × vol cap × threshold × breadth 组合
- `tests/pipeline/test_pmarp_rebound_v1.py`

### Reports (33 份 backtest artifacts)

`reports/backtest/` 下：

- 6 份 `pipeline_pmarp_rebound_v1*` — 6 个 base spec 变体直接 run
- 16 份 `pmarp_breadth_campaign_*` — breadth campaign 扫参
- 10 份早期命名 `pmarp_soft05_volcap_*` / `soft05_10b_*` / `tech_only_*` — 早期扫参产物
- 1 份 `pipeline_rs_rating_b_b6d7552328926a0b` — **bonus artifact**，不属于 PMARP 系，是本 worktree 里同期跑的 RS Rating B 独立扫参。一起存档避免 worktree 清理时丢失

每份 report 目录：`metrics.json` + `report.html` + `report.md` + `spec.yaml` + `split.json` + `nav_{is,oos}.parquet` + `signals_{is,oos}.parquet`。

## Results summary

### Core table (selected)

| Spec | IS Sharpe | OOS Sharpe | IS Excess% | OOS Excess% | IS Alpha% | OOS Alpha% |
|---|---:|---:|---:|---:|---:|---:|
| base | -0.64 | +0.90 | -31.0 | -8.6 | -5.7 | +9.7 |
| hold20 | -0.51 | +1.82 | -28.5 | -4.3 | -4.0 | +13.6 |
| peak7 | -1.13 | +1.05 | -37.4 | -4.7 | -12.9 | +14.6 |
| peak7_longsplit | -0.33 | -0.02 | -7.1 | -17.7 | -3.8 | -3.7 |
| soft05 | -1.38 | +1.60 | -42.0 | **+7.3** | -17.1 | +28.3 |
| soft05_longsplit | -0.57 | -0.01 | -11.5 | -17.5 | -8.3 | -3.5 |
| campaign_breadth_t1_recent7_h20_top10_vol20 | +0.66 | +1.04 | **+0.5** | -12.4 | +4.5 | +5.5 |
| campaign_breadth_t1_soft05_h60_top10_vol20 | +0.23 | +1.34 | -2.2 | -5.4 | +1.5 | +11.8 |

### Four observations

1. **22 PMARP-rebound / breadth 份扫参里没有一份同时 IS/OOS excess > 0**。IS excess 只有 1 份微正 (+0.5%)，OOS excess 只有 2 份正，两个集合**零重合**。
2. **IS / OOS Sharpe 频繁反号**：`hold20` / `peak7` / `soft05` 三个 IS Sharpe 均负、OOS Sharpe 均超 +1.0，典型过拟合 / 样本外运气形态。`soft05` IS excess -42% → OOS excess +7.3% 是最夸张的反号。
3. **OOS Sharpe 看似不错是降波动拿到的，不是 alpha**：这些 OOS Sharpe > 1 的组合 OOS 年化波动都被压到 9–10%（benchmark ~15%），但 OOS IR 仍是负的，OOS excess CAGR 仍为负——说明策略只是把自己的波动压低到 benchmark 以下，没能跑出 alpha。
4. **Long/short split 两份 OOS 都近 0 Sharpe**：`peak7_longsplit` 和 `soft05_longsplit` 都是 OOS -0.01 Sharpe，说明 long leg 的温和收益被 short leg 完全抵消——signal 在 cross-section 里的排序力不足以支撑 long/short 结构。

## The core lesson

**Event-level alpha ≠ cross-sectional pipeline alpha。**

上位 event study 证明的是「PMARP 上穿 2% 后 60 日平均持有能拿超额」——这是**事件级**均值，所有触发事件等权持有、不排序、不筛选。

搬到 V3 pipeline 的 cross-sectional daily-rebalance 结构后，机制完全变了：
- 每天从当日触发列表里按 RVOL 打分选 top_n（多数触发事件被排除）
- 每天 rebalance，实际持有时长被 rebalance 节奏 + universe 变化稀释
- holding window 用 pipeline 的"持有到达 N 天"近似，和 event study 里的"触发日买入、第 60 日卖出"时序不同

结论：**把 event-style 策略用 cross-sectional pipeline 近似会稀释掉核心 edge。**

这对后续工作的约束是明确的——想保住 event study 的 alpha，要么改用真正的 event backtest engine（按触发日进场、按固定持有期出场，不走 cross-section 排序），要么在 pipeline 上接受 signal 被稀释后的残差再判断值不值得。

## Reproducibility

上述 `.parquet` / `metrics.json` 是 2026-04-12 用 `codex/backtest-pipeline-v3 @ 123e897` 上的代码 + M 文件改动跑出来的。要重跑：

```bash
git checkout research/pmarp-rebound-v1
# Run base:
.venv/bin/python -m backtest.pipeline.runner backtest/specs/pipeline_pmarp_rebound_v1.yaml
# Run breadth campaign sweep:
.venv/bin/python scripts/run_pmarp_breadth_campaign.py
```

## Why this is archived not deleted

1. 22 份 PMARP + 10 份早期扫参的**扫参矩阵重建成本不低**（需要云端数据 + pipeline run 时间）。
2. 这个 negative finding 对未来「event study → pipeline」类工作有**直接约束价值**——下次想把 event study 落地成 pipeline 因子前，先看这里。
3. panel-based factor 基础设施（`compute_panel` + signal_engine.py 的 panel 注入）本身**技术上可用**，未来如果要做 event-style 因子的 pipeline 版本，可以从这个分支分岔。
