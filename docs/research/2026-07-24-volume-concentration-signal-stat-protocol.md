# Volume Concentration Signal Statistics Protocol

**Date:** 2026-07-24
**Status:** Frozen（跑数前冻结）
**Signal:** Top-50 dollar-volume concentration regime（市场级，单时间序列）
**Target:** SPY / QQQ 前瞻收益

## 背景与诚实性声明

本协议在一次描述性探索**之后**注册：2026-07-24 的探索 pass 已看过全样本 4-bucket 前瞻收益中位数表（scratchpad `vol_concentration_v2.py`）。因此本研究**不是盲测**，IS/OOS 切分只能作为**子样本稳定性检查**，不能当真 OOS 解读。缓解手段：

1. 主检验用与探索 pass 完全相同的参数（不挑参数）；
2. 全参数网格稳健性（144 组合）看符号一致率，不挑最优；
3. 推断用循环平移置换检验，保留信号与收益序列各自的自相关结构；
4. episode 级统计报告真实自由度（约 11 段），不让重叠日频样本夸大置信度。

与 PMARP/BBWP 系列协议的关键差异：那些是横截面事件研究（数万个个股事件，日期聚类后仍有数百有效 N）；本研究是**单一市场级序列**，日频观测高度重叠，有效样本量约等于 regime 片段数。统计功效先天受限，协议按此设计。

## Frozen Methodology

### 数据

- 本地 `data/market.db` `daily_price`，`close > 0 AND volume > 0`
- 剔除池内基准 ETF：`SPY / QQQ / SOXX`（SPY/QQQ 仅作为 target 与方向代理）
- 样本范围：2021-02-01 ~ 最新（regime 标签经 burn-in 后自 2022-06 起有效）

### 信号构造（与探索 pass 逐行一致）

```text
dv[i,t]        = close[i,t] * volume[i,t]
topN_share[t]  = sum(largest N dv at t) / sum(all dv at t)      # N = 50（主）
sm[t]          = 20 日滚动均值 of topN_share
pctile[t]      = 252 日滚动窗口内 sm[t] 严格大于占比 × 100        # 窗口含当日
dir[t]         = SPY close[t] / close[t-20] - 1 > 0
高集中[t]      = pctile[t] > 80
bucket[t]      ∈ {高集中+涨, 高集中+跌, 低集中+涨, 低集中+跌}
```

无前视：`sm`、`pctile`、`dir` 均只用截至 t 收盘的数据。

### 前瞻收益

```text
fwd_h[t]  = target_close[t+h] / target_close[t] - 1      # h ∈ {5, 20, 60}
mdd60[t]  = min over s∈(t, t+60] of target_close[s] / target_close[t] - 1
```

主 target = SPY；QQQ 为稳健性。主口径 close-to-close 自 t 起（与 PMARP 协议一致）；`skip1` 变体（自 t+1 起）进网格。

### 主检验（primary family，共 6 个）

| # | 对比 | 假设方向 |
|---|------|----------|
| C1 | 高集中 vs 低集中，fwd_h 均值差 | 高集中更弱（负） |
| C2 | 高集中+涨 vs 低集中+涨，fwd_h 均值差 | 高集中+涨更弱（负） |

各 × h ∈ {5, 20, 60}，主 endpoint 为 **fwd20**。

### 推断

1. **循环平移置换**（主）：regime 标签序列相对收益序列整体循环平移随机偏移（偏移量 ∈ [63, T-63]），5,000 次，双侧 p = |置换差| ≥ |观测差| 的占比。保留两序列各自的自相关与聚类结构。种子 `20260724`。
2. **Newey-West t**（辅）：`fwd_h ~ dummy`，HAC lags = h。
3. **BH-FDR** 于 6 个主检验内校正。
4. **Episode 级**：每个 regime 片段（≥5 天，间隔 ≤5 天合并）取进入日，报告片段级前瞻收益符号计数——真实自由度视角。

### 稳健性网格（不做推断，只看符号一致率）

`N ∈ {20,50} × threshold ∈ {70,80,90} × pctile_window ∈ {252,504} × dir_lookback ∈ {10,20,60} × target ∈ {SPY,QQQ} × skip ∈ {0,1}` = 144 组合，每组报告 C1/C2 fwd20 均值差符号与 NW t。

### 子样本稳定性（非真 OOS）

- IS: burn-in 结束 ~ 2024-12-31
- 稳定性段: 2025-01-01 ~ 最新

### 探索性附加（不进主结论）

- 换手率交互：高集中日内，churn 1 年分位 <20 vs ≥20 的 fwd20 差（当前市场形态即此象限）
- 路径风险：各 bucket 的 mdd60 分布

## 预注册判定标准

| 结论 | 条件 |
|------|------|
| **成立** | C1 或 C2 的 fwd20 置换 p 过 BH-FDR (q=0.05)，且网格符号一致率 ≥70%，且 IS 与稳定性段同号 |
| **方向性但证据不足** | 符号一致率 ≥70% 且两子样本同号，但 p 不过 |
| **不成立** | 网格符号一致率 <70%，或两子样本反号 |

“方向性但证据不足”时的预注册用途上限：晨报 context 序列（描述性展示），不得作为择时信号进入任何仓位公式。

## Artifacts

- 代码 + 输出：`backtest/new/vol_concentration_signal_stats_20260724/`
- 报告：`docs/research/2026-07-24-volume-concentration-signal-stat-study.md`
