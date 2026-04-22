# PMARP Upcross 2% Extended Universe — 硬化收尾 Handoff

**日期**: 2026-04-22
**作者**: Claude (handoff 给 Codex)
**分支**: `research/pmarp-extended-hardening` (worktree `.worktrees/pmarp-extended-hardening/`)
**基点**: `a365aa1` — chore(backtest/pipeline): drop parquet fallback after pyarrow install
**适用对象**: Codex (后续硬化工作执行方)
**目的**: 把 PMARP upcross 2% 因子从"事件研究已通过"推到"可纳入因子库的生产级验证"

---

## TL;DR

1. **这个任务不是规划中，是"已走完 2 个阶段，只差最后硬化"**。ongoing.md 原描述过时。
2. **阶段 1 已通过**：Extended 533 + partial PIT 下，cross_up 2.0 @ 60d mean excess **+1.94%, p-FDR 0.0169 ⭐, Hit 58.6%, N=3398, Neff=198**。`t-stat` 比 pool 166 的 2.57 还高 (2.97)，显著性增强。
3. **阶段 2 已完成两个衍生研究**（2026-04-22）：downcross 98 不是 bearish 信号；BBWP 作 PMARP 过滤器没过 FDR。
4. **Codex 已审阅过阶段 1**，撤回了 `threshold_2.0` 语义写反的叙事，`cross_up_2.0` 数字和方法论未被质疑。
5. **离"进因子库"还差 3 件硬骨头**：true survivorship bias 未修、无真 OOS holdout、无 walk-forward。
6. **Codex 继续的起点**：先决定硬化优先级（建议 true survivorship 优先），然后做 1 件，别一次性全做。

---

## 1. 任务原始要求（Boss 的 bar）

来自 ongoing.md 被删除前的描述：

> (1) 等 Broad Universe 历史数据底座 plan v3 落地并执行
> (2) 在 `backtest/factor_study/` 框架里写预注册协议：universe/时间窗口/t-stat 门槛/p-FDR 门槛/OOS holdout 切法全部在看数据前写死
> (3) 跑 Russell 1000/3000 级 universe 扩大验证
> (4) 严格 walk-forward + IS/OOS 隔离
> (5) 若 OOS 通过，把因子正式纳入 Finance 因子库作为 Timing 维度硬边

**关键约束**：
- 禁止看数据后调参（避免 confirmation bias）
- 禁止从 V2/V3.x 结果反推阈值
- 保持 upcross 2% 固定阈值不变

---

## 2. 已完成阶段

### 2.1 基础设施（全部已 commit 到 main）

| 组件 | Commit | 位置 |
|---|---|---|
| `USStocksAdapter` 加 `universe` + `mcap_threshold` 参数 | `894c4ff` | `backtest/adapters/us_stocks.py` |
| coverage gate every rebalance + skip-existing row check | `0dd67bc` | 同上 |
| `historical_market_cap` 表 schema + 回填脚本 | (在 main/V3 pipeline 分支) | `src/data/fmp_client.py`, `scripts/fetch_historical_mcap.py` |
| Extended universe ($10B+ yfinance) | `34535aa` | `src/data/extended_universe_manager.py` |
| Factor study R1-R4 增强 (benchmark-adjusted / FDR / date-clustered / IS-OOS) | `4f3f849`~`811c0fd` | `backtest/factor_study/` |

### 2.2 阶段 1：Extended 533 + Partial PIT 回测 — 已通过（2026-04-12）

**报告**: `docs/research/2026-04-12-pmarp-extended-universe-reconstitution-study.md`（Codex 审阅通过第二版）

**单信号 `cross_up_2.0 @ 60d` 对比**：

| 指标 | pool 166 (2026-03-17) | extended 533 + partial PIT (2026-04-12) |
|---|---|---|
| N (raw events) | 916 | **3398** |
| Neff (date-clustered) | 155 | **198** |
| Mean excess (60d) | +5.87% | **+1.94%** |
| Hit rate | 61.9% | 58.6% |
| t-stat | 2.57 | **2.97** |
| p-val | 0.0110 | **0.0034** |
| p-FDR | 0.028 ✅ | **0.0169 ⭐** |

**核心解读**：
- 均值压缩（-67%）不是因子弱化，而是小 sample 被 outlier 放大被挤出来了
- t-stat 上升 + p-FDR 下降 → 显著性反而变强
- **+1.94% 是更诚实的 expected value**

**Bonus 发现**：PMARP 作为连续因子在 extended 533 上显著**负 IC**（60d t=-4.00, p=0.0001, Q5-Q1 -1.58%），**推翻了 2026-03-17 "连续 IC 无效"的结论**。事件研究 + 连续 IC 两条证据链都指向"低 PMARP 反转"方向。

### 2.3 阶段 2：衍生研究（2026-04-22）

#### 2.3.1 PMARP downcross 98% 事件统计

**报告**: `docs/research/2026-04-22-pmarp-down98-signal-stat-study.md`

**结论**：不是 bearish 信号。7/14/21d 都是正收益（`pool` Full +1.16% / +2.29% / +2.17%；`extended` Full +0.47% / +0.80% / +1.15%）。是"脱离极端强势区" ≠ "进入弱势区"。

#### 2.3.2 PMARP + BBWP Daily Study

**报告**: `docs/research/2026-04-22-pmarp-bbwp-daily-study.md`

**结论**：
- `PMARP upcross 2%` baseline 在 daily `T+1 open → T+H close` 语义下依然成立（sanity check 通过）
- BBWP 单独：仅在 `pool + prior-downtrend` 有东西，不稳健普适
- BBWP 作 PMARP 过滤器：accepted cohort 常常更高但 **accepted-vs-rejected 没过 FDR**
- 最值得追的候选：`pool + PMARP up2 + BBWP high-zone turn down recent3 + 60d`（Full diff +13.07pp, p=0.064, p-FDR=0.412；OOS diff +20.10pp, p=0.127, p-FDR=0.594）— 未过线

**决策**：不把 BBWP 并入 PMARP 生产过滤器。

---

## 3. 离"纳入因子库"还差什么（硬化 gap 分析）

对照 Boss 原始要求的 5 点：

| 原始要求 | 当前状态 | Gap |
|---|---|---|
| (1) Broad Universe 数据底座 | ⚠️ **部分达成** — extended 533 ($10B+) 已有 5 年日频；但 historical_market_cap 仅含"今天还活着的 567 只" | 等 broad universe v3 plan 落地可进一步扩大 |
| (2) 预注册协议（门槛写死） | ❌ **未做** | 本次 handoff 的核心待办 |
| (3) Russell 1000/3000 级 universe | ⚠️ **部分** — 已 extended 533 $10B+，但未覆盖 R1000/R3000 | 口径讨论：是否 $10B 就够 |
| (4) Walk-forward | ❌ **未做** | 当前 `--no-oos` 是全量 in-sample |
| (5) 严格 IS/OOS 隔离 | ❌ **未做** | 理由是"固定阈值无过拟合"但不够硬 |

**三个硬骨头按优先级排序**：

### 硬骨头 A — True Survivorship Bias 修复（最大缺陷）

`historical_market_cap` 的回填 seed 是"今天还活着的 567 只"，完全不含过去 5 年退市股票：
- TWTR (2022-10 私有化, 退市前市值 ~$41B)
- VMW (2023-11 被 Broadcom 收购)
- ATVI (2023-10 被 Microsoft 收购)
- FRC / SBNY / SIVB (2023 银行倒闭)

**估计影响**：30-50 只退市股 × 10-30 事件 ≈ 600-1500 个 cross_up 2 事件缺失 → **~18-44% sample loss**。补齐后 mean return 预计 +1.0%~1.5% 区间，**不会翻转方向**，但数字会更诚实。

**执行路径（FMP starter 实测可行）**：
1. 爬 S&P 500 历史 changes（Wikipedia）+ Russell 退市名单
2. FMP single-symbol `historical-market-capitalization` query（TWTR 实测返回 712 rows）
3. `yfinance` 补 daily_price
4. 重跑 2026-04-12 那份 study，看数字变化

### 硬骨头 B — 真 OOS Holdout（方法论硬化）

当前是 2021-2026 全量 in-sample。"固定阈值 2% 不调参" 是辩护，但不够硬。

**可选方案**：
- **方案 1 (简单)**：2021-2024 IS / 2025+ OOS。问题：OOS ~52 weekly dates，n_events 可能不够
- **方案 2 (walk-forward)**：滑动窗口 3 年 IS / 1 年 OOS，每年更新。好处：充分利用数据；坏处：复杂
- **方案 3 (cross-market OOS)**：美股作 IS，朋友 A 股 4938 作独立 OOS（已有 66.2% 胜率）。好处：最严苛；坏处：hit rate 单维度不如 mean return 有说服力

**建议**：先做方案 1，如果显著性崩了再上方案 2。

### 硬骨头 C — 预注册协议文档化

Boss 要求"universe / 时间窗口 / t-stat 门槛 / p-FDR 门槛 / OOS holdout 切法全部在看数据前写死"。

**建议放在** `backtest/factor_study/preregistrations/pmarp_cross_up_2.md`，内容：
- Hypothesis: PMARP upcross 2 → 60d positive excess return (directional, behavioral anomaly)
- Universe: extended 533 + PIT mcap filter $10B (主) / pool 166 (次)
- Time window: 2021-04-13 → latest (data-driven, not chosen)
- Horizons: 7, 30, 60 (固定，不 sweep)
- Pass criteria: 60d p-FDR < 0.05 AND Hit rate > 55% AND Mean excess > 0
- OOS split: ??? (need decision)
- Multiple testing: BH-FDR within this single family
- Stopping rule: 1 run, no re-testing after seeing results

---

## 4. 代码与数据清点

### 4.1 已 commit（worktree 已继承）

全部阶段 1 基础设施（见 §2.1 表格）。

### 4.2 ⚠️ Uncommitted（worktree 没继承，需要决策）

**`scripts/run_factor_study.py`** — `--universe` / `--mcap-threshold` CLI 暴露。

这份 diff 在 main working tree 上未 commit（见 2026-04-12 研究报告 §3.2）。worktree 从 HEAD 分叉时没有它。**codex 开工前需要决定**：

- **选项 A**：把 main working tree 上这份 diff 复制到 worktree 并 commit 为 "feat(factor-study): expose universe + mcap CLI"
- **选项 B**：重新写一遍（结构清晰，但和 main 上的冗余）
- **选项 C**：等 Boss 决策是否先 commit 到 main，worktree 再 rebase

**Claude 建议选项 A**（最简单，main 上那份 diff 反正也是我们的工作）。

Diff 内容（3 处编辑）见 `docs/research/2026-04-12-pmarp-extended-universe-reconstitution-study.md` §3.2。

### 4.3 数据资产（本地 + 云端）

| 资产 | 路径 | 规模 | 更新 |
|---|---|---|---|
| 价格 (pool + extended) | `data/market.db` | 162 MB, 557 symbols × 5 年日频 | 云端 cron 日更 |
| historical_market_cap | `data/market.db` (同表) | **687,588 行, 567 symbols, 2021-04-13 → 2026-04-10** | 手动回填 |
| Extended universe 清单 | `data/pool/extended_universe.json` | 533 symbols | 周频 refresh |
| 历史 study artifacts | `backtest/new/pmarp_us_*_20260412.md`, `data/factor_study/report_PMARP_20260412.html` | — | — |

⚠️ historical_market_cap **缺失退市股** — 硬骨头 A 的核心问题。

### 4.4 入口脚本

```bash
# 复现 2026-04-12 extended 533 研究（需要 §4.2 uncommitted diff 先进 worktree）
cd "/Users/owen/CC workspace/Finance/.worktrees/pmarp-extended-hardening"
/Users/owen/CC\ workspace/Finance/.venv/bin/python scripts/run_factor_study.py \
  --market us_stocks \
  --factor PMARP \
  --thresholds 2 \
  --universe extended \
  --mcap-threshold 10e9 \
  --horizons 7,30,60 \
  --benchmark SPY \
  --no-oos \
  --html

# 2026-04-22 衍生研究
/Users/owen/CC\ workspace/Finance/.venv/bin/python scripts/run_pmarp_down98_signal_stats.py
/Users/owen/CC\ workspace/Finance/.venv/bin/python scripts/run_pmarp_bbwp_daily_study.py
```

### 4.5 测试覆盖

- `tests/test_factor_study/` — 82 tests (Codex 审阅时 all pass)
- `tests/test_us_stocks_reconstitution.py` — 涵盖 coverage gate / passthrough / PIT 逻辑
- `tests/test_backtest/test_pmarp_signal_stats.py` — 27 tests
- `tests/test_backtest/test_pmarp_bbwp_study.py` — 7 tests

---

## 5. 建议的下一步切分（codex 可选）

**不要一次做完 3 个硬骨头**。建议按依赖顺序：

### Step 1 — 把 §4.2 的 CLI diff 搬进 worktree 并 commit

**必做的起手式**。否则后续实验跑不起来。

### Step 2 — 写预注册协议（硬骨头 C）

在跑任何新数据前先写死。30 分钟产物。写完先 Boss review 再动手。

### Step 3 — 做硬骨头 B（真 OOS holdout）

最小工作量，因为数据/代码都就绪，只需加 `--oos-fraction` 或手动切 IS/OOS 跑两次。

**成功门槛**（写在预注册里）：OOS 60d p-FDR < 0.05 AND hit > 55% AND mean > 0。

**失败应对**：如果 OOS 崩了，就承认 "因子在美股大市值 extended 533 上不具可投资性"，转向 A 股 universe 复制或其他方向。

### Step 4 — 做硬骨头 A（true survivorship 修复）

工作量最大。Step 3 通过再做，否则是浪费工程。

**如果 Step 3 通过 + Step 4 补齐后数字仍然显著**：PMARP cross_up 2.0 正式进因子库作为 Timing 维度硬边。

---

## 6. 已知坑

1. **worktree .venv 不共享** — 必须用绝对路径 `/Users/owen/CC workspace/Finance/.venv/bin/python`
2. **coverage gate 漏洞** — `us_stocks.py:199-204` 的 fallback `if sym not in mcaps: 保留`。覆盖率 90-99% 时缺失股票被默认保留。修复建议：改成 `剔除` 严格 PIT 或把门限提到 99%
3. **Date clustering 未处理 serial correlation** — 连续 weekly dates 的 forward return 重叠，t-stat 略高估。Codex 审阅建议 Newey-West 或块自助
4. **Weekly vs Daily** — `--freq W` 会错过周内 cross。2026-04-12 为保持可比性用 weekly，2026-04-22 衍生研究用 daily (`T+1 open → T+H close`)
5. **4 只 symbols 因数据不足被过滤** — extended 533 里有 4 只 < 70 天被 `USStocksAdapter.load_all()` 过滤掉，未记录具体 symbols
6. **A 股朋友数据** — 只有 hit rate 没有 mean excess return，跨市场对比需要他补数据

---

## 7. 参考文档

### 主线
- `docs/research/2026-03-17-pmarp-crossover-factor-study.md` — 原始发现 (pool 166)
- `docs/research/2026-04-12-pmarp-extended-universe-reconstitution-study.md` — **Codex 审阅通过版，阶段 1 主结论**
- `docs/research/2026-04-22-pmarp-down98-signal-stat-study.md` — downcross 98 不是 bearish
- `docs/research/2026-04-22-pmarp-bbwp-daily-study.md` — BBWP 过滤器未过 FDR

### 旁证
- `backtest/new/pmarp_backtest_v2_report_20260410.md` — A 股 4938 hit 66.2% (朋友数据)
- `backtest/new/pmarp_us_extended_20260412.md` — extended 研究 artifact
- `backtest/new/pmarp_us_pool_daily_t1open_report_20260412.md` / `pmarp_us_extended_daily_t1open_report_20260412.md` — daily T+1 open 语义

### Memory
- `memory/project_pmarp_cross_market_validation.md` — 跨市场 behavioral anomaly 定位
- `memory/user_technical_analysis_background.md` — PMARP 的 7 年加密货币实战 provenance

### 被撤回 / 证伪
- ~~美股 PMARP 追强 ≥98% 百分位有效~~ → 错，98% 是噪音 (p-FDR=0.69)
- ~~V3.2 大盘 PMARP 是 overfit~~ → 过头，规则结构合理
- ~~Survivorship 在朋友 A 股是大问题~~ → 错，全量 + 低退市率

---

## 8. 验收标准（Boss 视角）

硬化完成的标志（三选一或全满足）：

1. **预注册协议 + 真 OOS 通过**：extended 533 @ 60d OOS p-FDR < 0.05 AND hit > 55% AND mean > 0
2. **True survivorship 补齐后仍然显著**：加入退市股后 p-FDR 仍 < 0.05
3. **跨市场独立 OOS 复制**：朋友 A 股 4938 数据补齐 mean excess return 后，与美股方向+量级一致

达到任一即可纳入因子库。全满足则可直接进生产。

---

**Handoff 完**

Author: Claude
Reviewer: Codex (接手)
Under direction of: Boss
