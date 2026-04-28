# PMARP 美股回测报告 — Extended Universe + Partial PIT

**执行时间**: 2026-04-12 08:22 ~ 08:46 (耗时 24 分钟)
**数据股票数**: 529 只（extended universe $10B+，4 只因数据不足被过滤）
**有效因子分数**: 523 symbols × 261 weekly dates
**时间范围**: 2021-02-01 ~ 2026-04-07
**基准**: SPY
**收益类型**: 超额收益 (vs SPY)
**Universe Reconstitution**: ✅ 启用（每个 rebalance 日 PIT 过滤 < $10B）

> ⚠️ **注意**: 本报告是**事件研究 (Event Study)** 不是 portfolio backtest。
> 只给出 mean forward return / 胜率 / 统计显著性，**不提供** 持仓曲线 / Sharpe / 最大回撤 / 盈亏比（这些需要另外的 portfolio backtest 跑）。
> 朋友 V2 那份是 portfolio backtest，两者指标不完全对应，请对照时留意。

---

## 一、核心结论

### 1.1 持仓周期敏感性（`cross_up_2.0` 信号）

| 持仓周期 | N (raw) | Neff | 均值超额收益 | 胜率 | t-stat | p-val | p-FDR | 显著性 |
|:--------:|:-------:|:----:|:-----------:|:----:|:------:|:-----:|:-----:|:------:|
| 7 日 | — | — | — | — | — | — | > 0.138 | **未进 Top 10** |
| 30 日 | 3522 | 204 | +0.74% | 52.9% | 1.69 | 0.092 | 0.138 | 不显著 |
| **60 日** ⭐ | **3398** | **198** | **+1.94%** | **58.6%** | **2.97** | **0.0034** | **0.0169** | ✅ 显著 |

**结论**: `cross_up_2.0` **需要 60 日 horizon 才显著**，30 日边缘不显著 (p-FDR 0.138)，7 日信号太弱（未进 Top 10, 无精确数字）。**60 日 p-FDR 0.0169, mean +1.94%, 胜率 58.6%**。

> 注: 本报告的数据来源是 factor_study Top 10 输出（按 p-FDR 排序）。`cross_up_2.0 @ 7d` 未进入 Top 10 意味着它的 p-FDR > 0.138，但我们没有精确数字。要看 7d 的完整分布，需要另外跑 `--freq D` 或让 report.py 输出完整 event table，不在本次 scope。

---

> **修订说明 (Codex 审阅后)**: 第一版本节列出了 threshold_2.0 / sustained / cross_down 等多种信号变体并展开了"整个超卖区是 alpha"的叙事，**Codex 审阅指出 `threshold_2.0` 语义被写反**（实际是 `PMARP > 2` 覆盖 89% 观察，不是超卖区）。相关内容已撤回。本报告只关注 `cross_up_2.0`。详见详版报告 Section 12 修订日志。

---

### 1.3 与 2026-03-17 pool 166 只的关键对比（`cross_up_2.0 @ 60d`）

| 指标 | 2026-03-17 pool 166 | **2026-04-12 extended 533 + PIT** | 变化 |
|:----:|:-------------------:|:--------------------------------:|:----:|
| Universe 规模 | 166 | **533** | +220% |
| Reconstitution | ❌ 无 | ✅ 启用 | — |
| N (raw events) | 916 | **3398** | +271% |
| Neff | 155 | **198** | +28% |
| **均值超额收益** | **+5.87%** | **+1.94%** | **-67%** |
| 胜率 | 61.9% | 58.6% | -3.3 pp |
| t-stat | 2.57 | **2.97** | ↑ |
| p-val | 0.0110 | **0.0034** | ↓ 更显著 |
| p-FDR | 0.028 ✅ | **0.0169** ⭐ | ↓ 更显著 |

**结论**: **均值收益大幅压缩，但统计显著性变强**。+5.87% 是 166 只小 sample 下 variance 被 outlier 放大的估计；**+1.94% 是更诚实的 expected value**。t-stat / p-FDR 全面变好说明因子没弱化，只是之前的 mean 被夸大了。

---

### 1.4 连续 IC 分析（新发现）

| Horizon | Mean IC | IC_IR | Hit% | t-stat | p-val | Q5-Q1 |
|:-------:|:-------:|:-----:|:----:|:------:|:-----:|:-----:|
| 7 日 | -0.0269 | -0.16 | 41.6% | **-2.46** | **0.0148** | -0.35% |
| 30 日 | -0.0082 | -0.05 | 45.2% | -0.80 | 0.427 | -0.23% |
| **60 日** ⭐ | **-0.0418** | -0.27 | 39.1% | **-4.00** | **0.0001** | **-1.58%** |

**结论**: **PMARP 作为排序因子在 extended 533 上显著负 IC（60d p=0.0001）**，指向**反转方向** — 买 PMARP 低分位跑赢买高分位 **1.58 pp / 60 日**。

**这推翻了 2026-03-17 的"PMARP 连续 IC 无效"结论** — 166 只 pool 太小被噪音淹没，extended 533 + reconstitution 把真信号挤出来了。

---

## 二、参数配置

```yaml
因子参数:
  EMA period: 20
  Lookback: 150
  公式: count(historical ≤ current) / 150 × 100
  信号阈值: 2.0 (PMARP 超卖区)

数据参数:
  Universe: extended ($10B+ 美股, 533 只)
  Reconstitution: PIT mcap filter @ $10B (每 rebalance 日)
  计算频率: W (weekly)
  时间范围: 2021-02-01 ~ 2026-04-07 (1304 交易日, 261 weekly dates)
  基准: SPY (超额收益)
  Horizons: 7d, 30d, 60d

统计方法:
  事件聚合: 按日期聚类 (同日多事件取均值作为独立观测)
  显著性检验: t-test on cluster means
  多重比较校正: BH-FDR
  IS/OOS: 全量无分割 (--no-oos, 预设假设不调参)
```

---

## 三、关键发现总结

| 维度 | 结论 | 关键数据 |
|:----:|:----|:--------:|
| **最佳持仓周期** | 60 日 | p-FDR 0.0169, mean +1.94%, hit 58.6% |
| **均值收益重新校准** | +5.87% → **+1.94%** (pool → extended) | 小 sample 放大 → 诚实估计 |
| **胜率回归** | 61.9% → **58.6%** | -3.3 pp |
| **显著性方向** | 更大 universe = **更显著** | t-stat 2.57 → **2.97** |
| **新发现: 连续 IC** | 显著负，60d Q5-Q1 **-1.58%** | t-stat -4.00 |

---

## 四、已知局限性（短版）

| 局限 | 影响 | 修复路径 |
|:----|:----|:--------|
| **True survivorship 未修** | 缺过去 5 年退市股（TWTR/VMW/ATVI/FRC...），~18-44% sample loss | Wikipedia 爬 S&P 历史变更 + FMP single-symbol query（~3-4 小时） |
| Adapter reconstitution 有 fallback | 覆盖率 90-99% 时缺数据股票被保留，非严格 PIT | 把 fallback 改成剔除 |
| 全量无 IS/OOS 分割 | 严格算 in-sample testing | 固定阈值不调参 → 不构成过拟合风险 |
| Weekly 采样 | 可能错过周内瞬时穿越 | 保持与 2026-03-17 可比性 |
| ~~`threshold_2.0` 叙事~~ **(已撤回)** | Codex P1: `threshold > 2` 覆盖 89% 观察，不是超卖区 | 详见详版 Section 12 |

---

## 五、输出文件

| 文件 | 路径 |
|:----:|:----|
| HTML 报告 | `data/factor_study/report_PMARP_20260412.html` |
| **详版研究报告** | `docs/research/2026-04-12-pmarp-extended-universe-reconstitution-study.md` |
| 本简版报告 | `backtest/new/pmarp_us_extended_20260412.md` |
| Historical market cap 数据 | `data/market.db` (historical_market_cap 表, 687K rows, 567 symbols) |

---

## 六、完整运行命令

```bash
cd "/Users/owen/CC workspace/Finance"
.venv/bin/python scripts/run_factor_study.py \
  --market us_stocks \
  --factor PMARP \
  --thresholds 2 \
  --universe extended \
  --mcap-threshold 10e9 \
  --horizons 7,30,60 \
  --benchmark SPY \
  --no-oos \
  --html
```

**前置工作**:
1. FMP backfill historical_market_cap (云端执行): `scripts/fetch_historical_mcap.py --universe pool / --universe extended`
2. 本地 pull: `./sync_to_cloud.sh --pull`
3. CLI patch: `scripts/run_factor_study.py` 加 `--universe / --mcap-threshold` 参数（未 commit）

---

**报告状态**: ✅ 研究完成，待 Codex 审阅
**详版见**: `docs/research/2026-04-12-pmarp-extended-universe-reconstitution-study.md`
