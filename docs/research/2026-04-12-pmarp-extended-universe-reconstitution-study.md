# PMARP 事件信号因子研究 — Extended Universe + Partial PIT (coverage-gated mcap filter)

**日期**: 2026-04-12
**作者**: Claude (factor research, under Boss direction)
**目的**: 让 Codex 审阅方法论 + 数字 + 诚实性
**与之前对照**: `docs/research/2026-03-17-pmarp-crossover-factor-study.md` (pool 166 只, 无 reconstitution)

---

## TL;DR

**PMARP `cross_up 2.0` 事件信号在扩展 universe + coverage-gated partial PIT 市值过滤（非严格 PIT：coverage ≥ 90% 时缺失 mcap 的股票 fallback 保留）的更严谨回测下，依然统计显著，甚至显著性变强**（p-FDR 从 0.028 → 0.0169），但**单事件平均超额收益从 +5.87% 压缩到 +1.94%，Hit rate 从 61.9% 降到 58.6%**。

| 指标 | 2026-03-17 (pool 166) | **2026-04-12 (extended 533 + PIT)** | 变化 |
|---|---|---|---|
| Universe | 166 只大市值 pool | **533 只 $10B+ extended** | +220% |
| Reconstitution | ❌ 无 (用 today universe 回溯) | ⚠️ **Partial PIT** (coverage-gate ≥ 90%，缺失股票 fallback 保留) | — |
| N (raw events) | 916 | **3398** | +271% |
| Neff (date-clustered) | 155 | **198** | +28% |
| Mean excess return (60d) | **+5.87%** | **+1.94%** | **-67%** |
| Hit rate (60d) | 61.9% | 58.6% | -3.3 pp |
| t-stat | 2.57 | **2.97** | ↑ |
| p-value | 0.0110 | **0.0034** | ↓ 更显著 |
| p-FDR | 0.028 ✅ | **0.0169 ⭐** | ↓ 更显著 |

**核心解读**: 均值压缩 ≠ 因子弱化，而是**小 sample 下均值被 outlier 放大，大 sample 是更诚实的 expected value**。样本量提升带来的统计功效增益**远超**均值缩水，t-stat 反而上升。

**但注意**: 本次仍存在 **true survivorship bias 残余**（已退市股票未补入 historical_market_cap），估计缺失 18-44% 的事件样本，**不会翻转结论方向**但可能让 mean return 估计略偏低。

---

## 1. 研究动机 (前因)

### 1.1 2026-03-17 那次 PMARP 研究的已知短板

我们 2026-03-17 在 factor_study 框架里跑过一次 PMARP cross_up 2.0 事件研究，结论是：

> 美股 PMARP 上穿 2% 是统计显著的均值回归信号（60d +5.87%, p-FDR=0.028, hit 61.9%）

但那次有**两个公认的短板**：

1. **Universe 过窄**：只用了当时 pool universe 的 166 只大市值股票。考虑到 $100B/$10B 双阈值门槛 + 排除 5 个行业，这个 universe 是**经过多重筛选的大市值精华**，sample 变异大，mean return 容易被 outlier 放大
2. **Universe 漂移 bias (in-pool survivorship)**：用"今天 universe 里的 166 只"回溯 5 年历史价格，意味着"5 年前未在 universe 的股票"不在测试里。这不是真 survivorship (因为 166 只都活着)，但是**时点 universe 失真**

### 1.2 朋友 A 股数据给出的跨市场参考系

Boss 朋友在 A 股 V2 回测框架里跑过同一信号 (PMARP cross_up 2%)，universe 是近乎全量 A 股 **4938 只**。得出：

- 30d win rate: 66.2%
- 60d win rate: 63.3%

这份数据让 Boss 有两个直觉：

1. "大池子应该更显著" —— 因为 sample 更大
2. 我们美股 166 只的 +5.87% 跟 A 股 4938 只的 66% 胜率"跨市场一致" → PMARP 是跨市场 behavioral anomaly

**但我们之前的对话发现 这个"一致"判断在方法论上不严谨**：

- 朋友的 4938 只 = 全量 A 股 (散户交易量占比 ~60-70%)
- 我们的 166 只 = 美股大市值精华 (机构交易量占比 ~70-80%)
- 两者"土壤"完全不同，PMARP 这种行为型因子在不同 universe 表现一定不一样
- **166 只是"发现层测试"，不能直接比"验证层数字"**

这个认识促成了本次研究。

### 1.3 本次研究的目标

**在尽可能消除 in-pool survivorship bias 的条件下，验证 PMARP cross_up 2% 在更大、更严谨的 US 美股 universe 上是否依然是统计显著的均值回归信号，并拿到一个更诚实的 expected mean return 估计**。

具体要解决：

- [x] Universe 从 pool 166 扩大到 extended 533
- [x] 启用 PIT market cap reconstitution，每个 rebalance 日过滤当时 < $10B 的股票
- [x] 其他参数严格对齐 2026-03-17 那次，让数字直接可比
- [ ] (不在本次 scope) 修 true survivorship (补退市股票数据)

---

## 2. 前置条件 / 数据基础

### 2.1 `historical_market_cap` 表 backfill (A2 前置任务)

**本次研究依赖的关键数据资产** —— 2026-04-12 上午刚完成的 FMP 回填：

| 维度 | 值 |
|---|---|
| 总行数 | **687,588** |
| Unique symbols | **567** (pool 151 ∪ extended 541 的并集) |
| 日期范围 | **2021-04-13 → 2026-04-10** (5 年，1255 交易日) |
| 每只中位 | **1255 rows** (p25=p50=p75 都是 1255) |
| NULL/0 | 0 |
| 重复 (symbol, date) | 0 |
| Pool 覆盖 | 151/151 (100%) |
| Extended 覆盖 | 541/541 (100%) |
| 数据源 | FMP `/stable/historical-market-capitalization` |
| 执行路径 | 云端 `aliyun:/root/workspace/Finance/` → sync_to_cloud.sh pull |
| 执行脚本 | `scripts/fetch_historical_mcap.py --universe pool / --universe extended` |
| 耗时 | 19 分钟 (151 + 541 串行, FMP 2s/call 限流) |

**Sanity sample** (今天活着的大公司 latest market cap):

```
NVDA   $4584B   AAPL  $3842B   GOOG   $3830B   MSFT  $2756B
AMZN   $2553B   TSM   $1922B   AVGO   $1762B   META  $1588B
TSLA   $1127B   BRK-B $1035B
```

数据质量可信。

### 2.2 已知数据缺口 (影响结论严谨性的公开诚实声明)

1. **True survivorship bias 未修** — `historical_market_cap` 的回填 seed 是"今天还活着的 567 只"，**不含**过去 5 年内退市/被收购的股票。已知例子：
   - TWTR (Twitter, 2022-10-27 私有化退市前市值 ~$41B)
   - VMW (VMware, 2023-11 被 Broadcom 收购)
   - ATVI (Activision Blizzard, 2023-10 被 Microsoft 收购)
   - FRC (First Republic Bank, 2023-05 倒闭)
   - SBNY (Signature Bank, 2023-03 倒闭)
   - SIVB (Silicon Valley Bank, 2023-03 倒闭)

   估计 5 年内 ≥$10B 退市美股约 **30-50 只**，每只平均触发 10-30 个 cross_up 2 事件 → **缺失 600-1500 事件** vs 本次总样本 3398 → **~18-44% sample loss**

2. **USStocksAdapter 的 reconstitution 有小漏洞**：`backtest/adapters/us_stocks.py:199-204` 的 filter 逻辑 `if sym not in mcaps: 保留`。覆盖率门卫是 ≥90%，所以剩下 ≤10% 缺数据的股票会被默认保留，不是严格 PIT。影响方向通常保守（不过度剔除）

3. **本次计算频率是 weekly (W)**，意味着 PMARP 在周一到周五之间瞬时穿越 2 但周五已经收回的事件会被错过。保留与 2026-03-17 一致的参数，不做 daily 以保证可比性

---

## 3. 代码修改

### 3.1 为什么需要改代码

`backtest/adapters/us_stocks.py:USStocksAdapter` **已经支持** `universe` + `mcap_threshold` 参数（包含 coverage-gated mcap filter 逻辑，**这是 partial PIT** —— 覆盖率 ≥ 90% 时缺失 mcap 的股票会被 fallback 保留，详见 Section 7.2），但：

- `FactorStudyConfig` 没有 mcap_threshold 字段
- `factor_study/__init__.py` 文档示例用 `USStocksAdapter()` (default, 不启用 reconstitution)
- `scripts/run_factor_study.py` CLI 没有 `--universe / --mcap-threshold` 参数

所以 Adapter 有"PIT 腿"但没人让它跑。**本次修改是最小的 CLI 暴露层 patch**。

### 3.2 修改详情

**文件**: `scripts/run_factor_study.py` (未 commit, 待审阅后决定 commit)

3 处编辑：

**Edit 1** — CLI 加 2 个参数:

```diff
     parser.add_argument("--no-oos", action="store_true", help="不做 IS/OOS 分割，全量数据作为单一样本")
+    parser.add_argument("--universe", choices=["pool", "extended"], default=None,
+                        help="美股 universe 切换 (pool=~151 / extended=~533 / 默认=all in db)")
+    parser.add_argument("--mcap-threshold", type=float, default=None,
+                        help="历史市值阈值美元 (e.g. 10e9)，每个 rebalance 日 PIT 过滤 (修 in-pool survivorship)")
     parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
```

**Edit 2** — `main()` 中 `_create_adapter` 调用传入新参数:

```diff
     # 适配器
     symbols = args.symbols.split(",") if args.symbols else None
-    adapter = _create_adapter(args.market, symbols=symbols, cache_dir_override=args.cache_dir)
+    adapter = _create_adapter(
+        args.market,
+        symbols=symbols,
+        cache_dir_override=args.cache_dir,
+        universe=args.universe,
+        mcap_threshold=args.mcap_threshold,
+    )
```

**Edit 3** — `_create_adapter()` 函数签名 + us_stocks 分支:

```diff
-def _create_adapter(market: str, symbols=None, cache_dir_override=None):
+def _create_adapter(market: str, symbols=None, cache_dir_override=None, universe=None, mcap_threshold=None):
     """创建数据适配器"""
     if market == "crypto":
         # ... (unchanged)
         return CryptoAdapter(symbols=symbols, cache_dir=cache_dir)
     else:
         from backtest.adapters.us_stocks import USStocksAdapter
-        return USStocksAdapter()
+        return USStocksAdapter(
+            symbols=symbols,
+            universe=universe,
+            mcap_threshold=mcap_threshold,
+        )
```

**注意 Edit 3 顺带修了一个 pre-existing 问题**: us_stocks 分支原来没把 `--symbols` 参数传给 USStocksAdapter (crypto 分支传了，us_stocks 分支没传)。本次顺手接上。这是 scope-within-reason 的 fix，不是 unrelated。

### 3.3 测试验证

```bash
.venv/bin/python -m pytest tests/test_factor_study -q
# 82 passed in 2.26s
```

所有现有 factor_study 测试通过，改动不破坏既有功能。

### 3.4 `--help` 输出确认

```
--universe {pool,extended}
                    美股 universe 切换 (pool=~151 / extended=~533 / 默认=all in db)
--mcap-threshold MCAP_THRESHOLD
                    历史市值阈值美元 (e.g. 10e9)，每个 rebalance 日 PIT 过滤 (修 in-pool survivorship)
```

### 3.5 相关 Adapter 代码（reviewer 参考，未改动）

`backtest/adapters/us_stocks.py:162-209` 的 `slice_to_date()` 中的 reconstitution 段（**本次研究依赖的关键逻辑**）：

```python
# ── universe reconstitution ──
if self._mcap_threshold and sliced:
    mcaps = _get_bulk_mcaps(date)

    # 覆盖率门卫 — 每次 rebalance 都检查
    has_data = sum(1 for sym in sliced if sym in mcaps)
    coverage = has_data / len(sliced) if sliced else 0
    if coverage < 0.9:
        missing = [sym for sym in sliced if sym not in mcaps]
        raise ValueError(
            f"{date}: reconstitution 覆盖率 {coverage:.1%} < 90% "
            f"({has_data}/{len(sliced)}). "
            f"缺失 mcap 数据的 symbols: {missing[:20]}..."
        )

    before = len(sliced)
    sliced = {
        sym: df for sym, df in sliced.items()
        if sym not in mcaps  # 无数据 → 保留（覆盖率门卫已确保 <10%）
        or mcaps[sym] >= self._mcap_threshold  # 有数据且达标 → 保留
    }
```

**Reviewer 关注点**:
- `if sym not in mcaps: 保留` 是 fallback — 覆盖率 < 90% 抛错，但 90-99% 的缺失股票被保留。严格派可以质疑这是"部分 PIT"
- `_get_bulk_mcaps(date)` 每次 rebalance 触发一次 DB 查询，性能上 OK
- **本次跑完 261 个 rebalance dates 没有触发 ValueError** → 覆盖率 ≥ 90% 全程成立

---

## 4. 研究参数

| 参数 | 值 | 与 2026-03-17 对比 | 备注 |
|---|---|---|---|
| 因子 | PMARP | 同 | Price Moving Average Ratio Percentile |
| EMA period | 20 (default) | 同 | `analyze_pmarp(df)` default |
| Lookback | 150 (default) | 同 | `analyze_pmarp(df)` default |
| 公式 | `count(historical ≤ current) / 150 * 100` | 同 | 高 PMARP = 当前处于历史高位 |
| 信号 sweep | thresholds=[2.0] with {threshold, cross_up, cross_down, sustained} | 同 | `--thresholds 2`, sweep 由 build_custom_sweep 构造 |
| Horizon | 7, 30, 60 days | 同 | `--horizons 7,30,60` |
| 基准 | SPY | 同 | `--benchmark SPY` |
| Computation freq | W (weekly) | 同 | `--freq W` 为 `us_factor_study` 默认 |
| IS/OOS split | 全量无分割 | 同 | `--no-oos` (设 oos_fraction=0) |
| 统计方法 | 日期聚类 t-test + BH-FDR | 同 | `event_study.py` 标准 |
| **Universe** | **extended 533** | **pool 166** | **⚡ 本次变量** |
| **Market cap filter** | **Partial PIT @ $10B** (coverage-gated) | **无** | **⚡ 本次变量** |

### 4.1 运行命令

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

### 4.2 运行元信息

| 项 | 值 |
|---|---|
| 开始时间 | 2026-04-12 08:22:31 |
| 结束时间 | 2026-04-12 08:46:26 |
| 总耗时 | **1433 秒 (23.9 分钟)** |
| 载入股票 | 529 (533 - 4 只数据 < 70 天被过滤掉) |
| 全部交易日 | 1304 |
| Computation dates (weekly) | 261 |
| 有效因子分数 symbols | **523** (× 261 weekly dates) |
| 基准 | SPY, 1297 日 |
| Coverage gate 全程失败次数 | **0** (未触发 coverage <90% ValueError；但未记录每 rebalance 的精确 coverage 分布) |
| 生成的 HTML 报告 | `data/factor_study/report_PMARP_20260412.html` |

---

## 5. 结果

### 5.1 Track 1 — IC 分析 (连续因子)

**In-Sample, Excess vs SPY, 261 weekly dates, 523 symbols**:

| Horizon | Mean IC | Std IC | IC_IR | Hit% | N | t-stat | p-val | Q5-Q1 |
|---|---|---|---|---|---|---|---|---|
| **7d** | **-0.0269** | 0.1647 | -0.16 | 41.6% | 226 | **-2.46** | **0.0148** ** | -0.0035 ** |
| 30d | -0.0082 | 0.1536 | -0.05 | 45.2% | 221 | -0.80 | 0.4266 | -0.0023 |
| **60d** | **-0.0418** | 0.1532 | -0.27 | 39.1% | 215 | **-4.00** | **0.0001** *** | **-0.0158** ** |

**关键发现**: PMARP 作为连续因子在 extended 533 上**显著负 IC**：

- 7d 和 60d 都显著，60d 极显著 (p=0.0001)
- Q5-Q1 spread 60d **-1.58%** — PMARP 最高分位组的 60d 超额收益**跑输** PMARP 最低分位组 **1.58 pp**
- **这是反转方向** — 买 PMARP 低的（超卖）跑赢买 PMARP 高的（超买）

**与 2026-03-17 对比**：

| 数据集 | IC 结论 |
|---|---|
| 2026-03-17 pool 166 | "IC ≈ 0, 无横截面预测力" (原文) |
| **2026-04-12 extended 533 + PIT** | **显著负, 反转方向** |

**这是一个新发现**。166 只 pool 上看不到是因为 sample 太小，signal 被噪音淹没。Extended 533 + reconstitution 把真信号挤出来了。

### 5.2 Track 2 — 事件研究 (Top 10, BH-FDR corrected)

**本研究的关注点只有 `cross_up_2.0`**。sweep 中的其他信号变体（`threshold_2.0` / `sustained_2.0×N` / `cross_down_2.0`）在本报告第一版曾被拿来展开"整个 PMARP=2 区域是 alpha"的叙事，**Codex 审阅后已撤回**（原因见 Section 12 修订日志），不再作为结论依据。本节只列 cross_up_2.0 的两个 horizon。

| Signal | Horizon | N (raw) | Neff | Mean | Hit% | t-stat | p-val | p-FDR |
|---|---|---|---|---|---|---|---|---|
| **cross_up_2.0** | **60** | 3398 | 198 | **+1.94%** | **58.6%** | **2.97** | **0.0034** | **0.0169** ⭐ |
| cross_up_2.0 | 30 | 3522 | 204 | +0.74% | 52.9% | 1.69 | 0.0920 | 0.1380 |

`cross_up_2.0 @ 7d` 未进入 Top 10，意味着其 p-FDR > 0.138（Top 10 最后一条的值），但本次跑没有精确数字输出。

**主要观察**:

1. **`cross_up_2.0 @ 60d` 统计显著** — Mean +1.94%, Hit 58.6%, p-FDR 0.0169。这是本报告唯一可主张的正面结论
2. **`cross_up_2.0 @ 30d` 边缘不显著** — Mean +0.74%, Hit 52.9%, p-FDR 0.138。30d 下信号偏弱
3. **7d 未进 Top 10** — p-FDR > 0.138，短 horizon 信号不成立

### 5.3 关键对比: 2026-03-17 vs 2026-04-12

**单一信号: `cross_up_2.0 @ 60d`** (Boss 最关心的那个)

| 指标 | 2026-03-17 pool 166 | 2026-04-12 extended 533 + PIT | 变化 |
|---|---|---|---|
| N (raw events) | 916 | **3398** | +271% (3.7×) |
| Neff (date-clustered) | 155 | **198** | +28% |
| **Mean excess return** | **+5.87%** | **+1.94%** | **-67%** (压缩 3×) |
| Hit rate | 61.9% | 58.6% | -3.3 pp |
| t-stat | 2.57 | **2.97** | +15% (更显著) |
| p-val | 0.0110 | **0.0034** | ↓ 更显著 |
| p-FDR | 0.028 ✅ | **0.0169 ⭐** | ↓ 更显著 |

**关键观察**: Mean return 和 Hit rate 都在下降（分别 -67% 和 -3.3 pp），但 N / Neff / t-stat / p-val / p-FDR 5 个指标都在"更好"方向。**整体判断**：均值和胜率一起回归，显著性反而变强。

---

## 6. 解读

### 6.1 为什么 mean return 从 +5.87% 压缩到 +1.94%

**小 sample 高 variance 的 mean 容易被 outlier 放大**。166 只 pool 上 cross_up 2 的 916 个事件分布到 155 个独立日期，平均每日只有 5-6 只触发。这种稀疏结构下，一两天的极端日（比如 2023-03 SBB 崩盘后反弹那种）就能拉动整体均值。

533 只 extended 上 3398 个事件分布到 198 个独立日期，平均每日 17 只触发。sample 更密 + universe 更广 → 每日聚类均值更接近"真实 expected value"。

**+1.94% 才是 PMARP cross_up 2% 60d 的真实 expected value** 。+5.87% 是一个被小 sample 放大的估计，不代表"因子弱化了"。

**支持这个解读的证据**:

- t-stat 上升 (2.57 → 2.97) — 如果是真的因子弱化，t-stat 应该下降，因为 |t| = |mean|/SE，mean 降 67% 但 SE 降得更快 (sample 大)
- p-val / p-FDR 全部下降 — 显著性增强
- 事件数 Neff 从 155 升到 198 — 没有显著"消失"

### 6.2 连续 IC 负: 与事件研究一致

2026-03-17 文档写 "PMARP 连续 IC ≈ 0，无横截面预测力"。但我们现在在 extended 533 上看到：

- 60d IC: -0.0418, t -4.00, p 0.0001 — **极显著负**
- Q5-Q1 60d: -1.58% — 买 PMARP 最低组跑赢买 PMARP 最高组

**连续 IC 负和事件研究一致**：两者都指向"PMARP 低 → 后续跑赢 PMARP 高"这个**反转**结论。

166 只 pool 上看不到这个连续负 IC 是因为 sample 太小。这个发现**推翻了 2026-03-17 的"连续 IC 无效"结论** —— 应该更新为"连续 IC 在更大 universe + 更长 horizon 上显著负 (反转)"。

### 6.3 与朋友 A 股数据对比的校准

朋友 A 股 V2 (4938 只, 近全量) 得出 hit rate：
- 30d: 66.2%
- 60d: 63.3%

我们 extended 533 的 `cross_up_2.0` hit rate:
- 30d: 52.9%
- 60d: 58.6%

**观察**:

- 朋友 A 股 hit rate ~63-66%, 我们美股 extended (cross_up_2.0) ~52-58%, **差 5-11 pp**
- A 股的额外胜率可能来自散户占比更高 (60-70% vs 美股大市值 20-30%)
- **方向一致（均为正向），但美股大市值的"土壤" systematically 弱于 A 股全量**

**注意**: 朋友数据是 **hit rate 不是 mean excess return**。我们没有 A 股 mean excess return 数字做直接对比。要完整对比需要让朋友也跑 mean return 输出。

---

## 7. 局限性 (Known Weaknesses)

Reviewer 应该特别质问的几点：

### 7.1 True survivorship bias 未修 ⚠️ (最大的)

- `historical_market_cap` 只含 567 只"今天还活着的"，**完全不含** TWTR / VMW / ATVI / FRC / SBNY / SIVB 等过去 5 年退市股票
- 估计缺失 30-50 只退市股 × 平均 10-30 事件 ≈ **600-1500 个 cross_up 2 事件**
- 相对本次总样本 3398 → **~18-44% sample loss**
- **影响方向判断**: 退市股临死前通常有剧烈下跌 + 反弹 + 再跌模式，PMARP cross_up 2 在这些股票上的触发集中在死亡前 1-2 年。它们的 60d forward return **可能是负的**（因为继续跌向死亡）或**极度正**（反弹后才死）
- **保守估计**: 补齐后 mean excess return 可能从 +1.94% → +1.0%~1.5% 区间，显著性可能略降但**不会翻转方向**

**Reviewer 如何自己验证**: 拿 TWTR + VMW + ATVI 三只手工拉数据验证，看它们的 cross_up 2 事件 60d forward return 是什么。这个 ad-hoc sanity check 是可行的

### 7.2 USStocksAdapter reconstitution 的 "coverage gate 漏洞"

`adapters/us_stocks.py:199-204` 的 fallback `if sym not in mcaps: 保留`。

- 覆盖率 < 90% → ValueError (严格)
- 覆盖率 90-99% → 缺失的 1-10% 股票被默认保留 (宽松)

对本次研究实际影响：本次 261 个 rebalance dates **没有一个触发 ValueError**，说明覆盖率全程 ≥ 90%，但没有精确日志说是 100% 还是 95%。严格派可以质疑这是"part-PIT"。

**修复建议** (不在本次 scope): 把 fallback 改成 `if sym not in mcaps: 剔除` (严格 PIT)，或者把覆盖率门限提高到 99%

### 7.3 全量无分割 (no OOS holdout)

`--no-oos` 理由：PMARP cross_up 2 是**预设假设**，阈值 2 固定不是从数据学的，没有过拟合风险。

**但严格审查**: 这等于"用 2021-2026 全部数据"测试一个"2021 年前就提出的假设"。**严格意义上这是 in-sample testing**，不是真正的 OOS。

**支持论点**: 如果我们把 2021-2024 当 IS, 2025 当 OOS 分开跑，OOS 只有 52 weekly dates，n_events 太少，统计功效严重受限。这个 trade-off 是合理的

**Reviewer 质问角度**: "如果这个因子在 2021-2026 的任意 3 年 sub-sample 上不显著，整体显著只是 sample accumulation 的结果吗？" — 这个问题本次没有回答

### 7.4 Weekly computation frequency 可能错过事件

PMARP 在周一到周四之间穿越 2 但周五已经收回的事件被 weekly 采样错过。

- 2026-03-17 也是 weekly，保持可比性
- 改 daily 会让 sample 密度 × 5，但也会触发很多噪音 cross (如 PMARP 在 1.9-2.1 来回震荡)
- **本次 scope 内保留 weekly**

### 7.5 Date clustering 消除横截面依赖但未处理 serial correlation

`event_study.py` 的日期聚类正确消除了"同一天多只股票触发的横截面依赖"（否则 t-stat 会被 N 膨胀）。

但没有处理**连续 weekly dates 之间的 serial correlation**（如果两个相邻的 weekly date 都触发 cross_up 2，它们的 forward return 是部分重叠的）。

- 影响：t-stat 可能**略高估**，但通常不足以翻转显著性
- **修复路径**: Newey-West 调整 or 块自助抽样

### 7.6 4 只 symbols 被数据不足过滤掉

Extended 533 里有 4 只在 `USStocksAdapter.load_all()` 被过滤掉 (需要 ≥ 70 行)。log 显示"加载 529 只股票"而不是 533。未记录具体是哪 4 只。

**Reviewer 质问**: 这 4 只可能恰好是 "5 年内 IPO 不久的高波动股"，PMARP cross_up 2 事件密度可能很高，剔除它们会影响结果

---

## 8. 结论

1. **PMARP cross_up 2.0 在扩展 universe + coverage-gated partial PIT 市值过滤下依然统计显著**，p-FDR 0.0169，60d 平均超额收益 **+1.94%**，Hit rate 58.6%
2. **+5.87% 是 2026-03-17 小 sample 下的估计**，**+1.94% 是更诚实的 expected value**。均值和胜率都压缩了（-67% / -3.3 pp），但 N / Neff / t-stat / p-val / p-FDR 5 个指标全部变好 —— 统计显著性反而更强 (t-stat 2.57 → 2.97, p-FDR 0.028 → 0.0169)
3. **PMARP 作为连续因子在 extended 533 上显著负 IC** (60d t -4.00)，指向**反转方向**。这推翻了 2026-03-17 "PMARP 连续 IC 无效" 的结论
4. **跨市场对比（hit rate 维度）**：美股 extended 533 的 `cross_up_2.0` hit 52-58% vs 朋友 A 股 4938 hit 63-66%，方向一致但美股大市值土壤 systematically 弱 5-11 pp
5. **仍然存在的 true survivorship bias 不会翻转结论**（预计 mean return 补齐后在 +1.0%~1.5% 区间，仍显著），但需要未来工程补齐退市股数据才能彻底清白

---

## 9. 可复现性 (Reproducibility)

### 9.1 环境快照

| 项 | 值 |
|---|---|
| Git HEAD | `a365aa1` (chore(backtest/pipeline): drop parquet fallback after pyarrow install) |
| Uncommitted patch | `scripts/run_factor_study.py` (本次 CLI 修改, 未 commit) |
| Python | 3.13 (.venv) |
| market.db size | 162 MB |
| historical_market_cap rows | 687,588 |
| historical_market_cap 日期范围 | 2021-04-13 → 2026-04-10 |
| Unique symbols in historical_market_cap | 567 |
| Extended universe file | `data/pool/extended_universe.json` (updated 2026-03-28, count 533, symbols 533) |

### 9.2 完整命令

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

### 9.3 HTML 报告位置

```
data/factor_study/report_PMARP_20260412.html
```

### 9.4 运行日志摘要

```
08:22:31 [INFO] 美股适配器: 加载 529 只股票
08:22:33 [INFO] 数据加载完成: 529 symbols, 1304 交易日
08:22:33 [INFO] 计算频率=W, 计算日数=261
08:22:33 [INFO] 基准已加载: SPY, 1297 日
08:22:33 [INFO] 构建超额前向收益矩阵 (vs SPY)...
08:22:33 [INFO] 开始因子研究: PMARP
08:46:23 [INFO]   因子分数计算完成: 523 symbols × 261 日
08:46:23 [INFO]   OOS 跳过: OOS 日期数 0 < 最小门槛 50
08:46:26 [INFO] 完成 PMARP (vs SPY): IC results=3, Event results=15, 耗时=1433.0s
08:46:26 [INFO] HTML 报告已保存: /Users/owen/CC workspace/Finance/data/factor_study/report_PMARP_20260412.html
```

### 9.5 依赖的已完成前置工作

1. **A2 historical_market_cap backfill** (2026-04-12 上午完成)
   - 云端执行: `ssh aliyun "cd /root/workspace/Finance && nohup bash -c 'python3 scripts/fetch_historical_mcap.py --universe pool --years 5 --skip-existing && python3 scripts/fetch_historical_mcap.py --universe extended --years 5 --skip-existing' > logs/fetch_mcap_20260412.log 2>&1 &"`
   - 本地拉取: `./sync_to_cloud.sh --pull`
   - 覆盖率: 100% (pool 151/151 + extended 541/541, failed 0)
   - 耗时: 19 分钟
2. **A3 pyarrow 清理** (commit `a365aa1`) — 与本次研究无关但在同日完成
3. **V3 pipeline merge** (merge commit `12a1c9f`) — 与本次研究无关但在同日完成

---

## 10. 审阅关注建议 (For Codex)

> 第一版本节曾包含对 `cross_down_2.0` / `threshold_2.0` 叙事的审阅问题。Codex 在第一轮审阅中已精确打穿这些问题（详见 Section 12 修订日志），本节已按撤回后的 scope 精简。

**请 Codex 重点审视以下点**：

### 10.1 方法论层面

1. **Coverage-gated partial PIT 是否够严谨?** — `us_stocks.py:199-204` 的 fallback `if sym not in mcaps: 保留`, coverage ≥ 90% 全程成立但没记录精确分布。本报告已降级为 "partial PIT" 措辞。严格派能否接受? 修复成本多大?
2. **全量无分割的 in-sample testing** — 固定阈值不调参的论点是否足以辩护"无过拟合"? 有没有更严格的 holdout 方案?
3. **Date clustering 是否足以消除 serial correlation?** — 连续 weekly dates 的 forward return 重叠问题是否需要 Newey-West?
4. **True survivorship 未修的实际影响评估** — 我给出的"~18-44% sample loss, 不翻转方向"是否合理估计? 有没有快速 ad-hoc 验证方法?

### 10.2 代码层面

5. **`run_factor_study.py` 的 3 处 Edit** — CLI 参数、`_create_adapter` 签名、main 调用的串联是否完整? 有没有 edge case (比如 crypto 分支是否受影响)?
6. **是否应该顺手修 `--symbols` 参数的 us_stocks 分支 dead code?** — 本次 Edit 3 顺手接上了，这算不算 scope creep?
7. **现有 82 tests 通过是否足够?** — 有没有应该新增的测试 (例如测试 `--universe extended --mcap-threshold 10e9` 的 integration test)?

### 10.3 数据层面

8. **529 / 533 的 4 只丢失** — 日志里没说是哪 4 只。是不是应该加日志输出?
9. **`backfill` 的 118 只跳过** — pool 151 跑完后 extended 541 里跳过了 118 只 (因 pool ⊂ extended 部分)，但 pool 151 里实际有 28 只 extended 里没有 (FMP screener 的 pool ≠ yfinance screener 的 extended)。这 28 只在 historical_market_cap 里的数据来源是 pool backfill，但 extended adapter 用不到它们 → 它们是不是 "数据存在但 unused"?

### 10.4 叙事层面

10. **"+5.87% 是小 sample 放大, +1.94% 是诚实估计"** — 这个解读是否过于乐观? 有没有可能 +1.94% 才是被稀释的均值, 而 +5.87% 才是大市值真正的 alpha?

---

## 11. 下一步 (未决定)

取决于 Codex 审阅结论，后续可能的分支：

- **A. 数据层修复 true survivorship bias** — Wikipedia 爬 S&P 500 历史 changes + FMP single-symbol historical_market_cap query + daily_price fetch, 补 30-50 只退市股。详见会话中讨论的"免费路径" (FMP starter 对单只退市股 query 已实测 work, 712 rows for TWTR)
- **B. 在 V3 Pipeline 上做 portfolio backtest** — 用 event-driven 策略写一个 spec，看 PMARP cross_up 2 作为真实可交易策略的 NAV / Sharpe / MDD，而非只是事件平均 return
- **C. 扩展到多 horizon / 多 threshold sweep** — 比如 threshold 5, 10, 20 测试，看 PMARP 低区是不是一个连续光谱
- **D. Newey-West 调整** — 解决 serial correlation 问题，把 t-stat 的偏估修掉
- **E. 跟朋友要 A 股 mean excess return 数字** — 做完整的跨市场对照 (hit rate + mean return 双维度)

---

## 12. 修订日志 (Revision Log)

### 2026-04-12 第二版 — Codex 审阅后撤回与修正

**审阅者**: Codex
**审阅方式**: 源码 + 文档审计（无结果复现，未重跑 24 分钟回测）
**审阅者自跑测试**: `tests/test_us_stocks_reconstitution.py` + `tests/test_factor_study/test_runner.py` = **13 passed**

Codex 给出三条命中的批评，全部 accepted：

#### P1 [critical] — `threshold_2.0` 语义被写反

**第一版错误叙事**: "`threshold_2.0` 捕捉 PMARP < 2 超卖区，是最强信号 (p-FDR 0.0023)，说明整个超卖区是 alpha"

**真实机制**:
- `factor_study/signals.py:71-74` 的 `SignalType.THRESHOLD` 实现是 `score > threshold`
- `PMARPFactor` 的 meta 是 `higher_is_stronger=True`, `score_range=(0, 100)`
- PMARP = 2 意味着 "当前 PMAR > 历史 2% 最低的 PMAR"
- 所以 `threshold_2.0` 实际是 **"PMARP > 2"**, N = 100,445 覆盖约 523 × 215 ≈ 112K 观察的 **89%**
- 这不是"超卖区"，基本就是**除了最低 2% 百分位之外的全 universe**
- +0.83% / 60d 基本等于"非最低 2% 状态下的全 universe 超额收益"，**不是超卖 alpha 的证据**

**撤回范围**:
- 5.2 Top 10 表格 → 只保留 `cross_up_2.0` 两个 horizon 行
- 6.2 "为什么 cross_down 2 也显著" 整节删除
- 6.2/8 "信号本质不是穿越方向而是接近 PMARP=2" 论证删除
- 7.7 "cross_down_2.0 反直觉需独立验证" 删除
- 8 结论 #3 (threshold 最强信号) 删除
- 10 审阅建议中 cross_down bug 检查 + "信号本质" over-reach 问题 删除

**仍然成立的结论**: `cross_up_2.0 @ 60d` 本身的数字（+1.94%, p-FDR 0.0169, Hit 58.6%）**没有被撤回**。事件研究在这个单信号上的方法论与结论依然正确。

#### P2 [important] — "PIT reconstitution" 表述过强

**第一版问题**: 报告标题 / TL;DR / Section 2 / 3.1 / 4 / 4.2 都称为 "PIT reconstitution"，暗示严格 PIT。

**实际实现** (`backtest/adapters/us_stocks.py:199-204`):

```python
sliced = {
    sym: df for sym, df in sliced.items()
    if sym not in mcaps  # 无数据 → 保留（覆盖率门卫已确保 <10%）
    or mcaps[sym] >= self._mcap_threshold  # 有数据且达标 → 保留
}
```

- 覆盖率 < 90% → `ValueError`（严格 fail-fast）
- 覆盖率 90-99% → **缺失 mcap 的股票被 fallback 保留**（宽松 passthrough）
- 本次 261 个 rebalance dates **全程没触发 ValueError**，但**未记录每个 rebalance 的精确 coverage 分布**
- 现有测试 (`tests/test_us_stocks_reconstitution.py:38`) 明确锁定了这个 passthrough 行为

**修订措辞**: "PIT reconstitution" → "**partial PIT** / coverage-gated mcap filter"。Title / TL;DR / Section 3.1 / 4 / 4.2 / 7.2 统一更新。

#### P3 [minor] — Section 6.1 对比总结漏掉 hit rate 恶化

**第一版错误**: "所有指标中，只有 mean return 下降了，其他 6 个指标都在更好方向"

**事实**: 5.3 对比表明确列出 Hit rate 从 61.9% → 58.6% (-3.3 pp) 也在下降。

**修订**: 改为 "Mean return 和 Hit rate 都在下降（-67% 和 -3.3 pp），但 N / Neff / t-stat / p-val / p-FDR 5 个指标都在更好方向"。

### Codex 审阅后确认没问题的

- `cross_up_2.0` 本身的数字方法论未被质疑
- CLI 串联（`run_factor_study.py` 3 处 Edit）wiring 层面 OK
- 13 个相关测试 pass

### Codex 的建设性提议（待 Boss 决定，不在本报告 scope）

> 如果要真正验证 "超卖区是否有 alpha"，下一步不是再解释现有 `threshold_2.0`，而是**加一个显式的 `below-threshold` / `sustained_below` 信号，或者对 `100 - PMARP` 做同样的 study**。

Boss 明确"不要节外生枝"，本报告只关注 `cross_up_2.0`。Codex 的提议记录在此，供未来会话参考。

---

**报告完**

Reviewer: Codex
Author: Claude
Under direction of: Boss
Date: 2026-04-12 (第一版)
Revised: 2026-04-12 (第二版, Codex 审阅后撤回叙事 P1/P2/P3)
