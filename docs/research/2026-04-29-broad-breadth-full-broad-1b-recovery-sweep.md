# Broad Breadth Full Recovery Sweep ($1B+ PIT) — QQQ/SOXX

Updated: 2026-04-29

## Reviewer Brief

这是一份给 CC review 的统计研究报告，不是交易指令，也不改生产逻辑。

本报告回答一个很窄的问题：

> 在完整 broad universe 口径下，是否存在一个可复现的「市场参与度从低位恢复」信号，可以预测 QQQ / SOXX 后续 5/10/20/60 个交易日收益？

结论很直接：

**没有找到可上线的 recovery 参数。**

- 使用 `$1B+` point-in-time 市值 eligibility 后，Full sample 中 `event_n >= 10` 的参数结果共有 `1304` 行。
- 同时满足 `q_value < 0.1` 且 `bootstrap_p_value < 0.1` 的行数为 `0`。
- 最好看的 raw candidate 主要集中在 `low_to_trigger` recovery，但事件数通常只有 `10-19` 次，FDR 校正后全部失效。
- OOS 里看起来收益很大的行几乎都是 `event_n = 3`，统计上只能当作 hypothesis scent，不能当作策略证据。

最终判断：**broad breadth recovery 可以保留为研究假设，但不能作为 QQQ/SOXX timing gate 或独立交易信号上线。**

## Why This Report Exists

前一版 broad breadth 研究先用了“每天最新可得市值 >= $10B”的主口径。这其实更像 large-cap / mega-cap broad，不是完整 broad。

Boss 指出后，本轮改为更接近真实 broad 的 `$1B+` point-in-time eligibility：

- `$10B+`: 大中盘偏核心，样本更干净，但不是完整 broad。
- `$1B+`: 更接近全市场广度，纳入更多 mid/small-cap，噪声更大，但符合“完整 broad”的研究目标。

这份报告专门补充 recovery sweep，因为原始 pre-registered broad breadth study 失败后，最可能仍有信息的方向不是“高广度是否看多”，而是：

> 当参与度从极低水平重新站上某个阈值，是否代表市场风险偏好恢复，进而利好 QQQ / SOXX？

也就是说，它检验的是“恢复事件”，不是每天按 breadth 分数直接线性预测收益。

## Universe And Data

主口径：

- Universe: broad `$1B+` point-in-time market cap eligibility.
- 市值判定：每个交易日只使用当日之前最新可得 historical market cap，不使用未来市值。
- Staleness gate: historical market cap 距离价格日期超过 `90` 天则剔除。
- Price source: local `market.db.daily_price`，read-only。
- Market cap source: local `market.db.historical_market_cap`。
- ETF exclusion: QQQ / SOXX / SPY / DIA / IWM / VTI / VOO / IVV / SOXL / SQQQ / TQQQ 不进入 breadth universe，避免把目标或宽基 ETF 混入参与度分母。
- Delisted overlay: 仅叠加现有 `delisted_large_caps.json` 中的 21 只大型退市/并购股票 sidecar。注意，这不是完整 survivorship-free broad。

数据覆盖：

| Item | Value |
| --- | ---: |
| Effective sample | 2021-06-22 -> 2026-04-28 |
| OOS split | 2025-01-01 |
| Latest date | 2026-04-28 |
| Latest eligible count | 2480 |
| Latest breadth MA20 | 62.7% |
| Latest breadth MA50 | 60.0% |
| Mean active eligible count | 2254.8 |
| Mean with-delisted_partial eligible count | 2261.4 |
| Mean delisted overlay coverage ratio | 31.0% |
| Max PIT staleness exclusions/day | 1 |

Coverage caveat:

`with_delisted_partial` 只是“部分 survivorship overlay”。它能补回一些大盘退市样本，但不能代表 `$1B+` broad universe 的完整退市历史。因此，本报告的统计结论可以用来否定“现在这套数据已经足够支持上线”，但不能证明真实 survivorship-free universe 下绝对不存在 recovery edge。

## Signal Definition

先对每只 eligible 股票计算：

```text
above_MA_N = close > SMA_N(close)
breadth_N = count(above_MA_N) / eligible_count
signal_N = SMA5(breadth_N)
```

本 sweep 深挖不同 MA window 和不同 recovery threshold：

- MA windows: `10, 20, 30, 40, 50, 60, 80, 100`
- Trigger thresholds: `20%, 25%, 30%, 35%, 40%, 45%, 50%, 55%, 60%`
- Low thresholds: `15%, 20%, 25%, 30%, 35%`
- Cooldown: `20` trading days after each event

事件族有两种：

| Event family | Definition | Intuition |
| --- | --- | --- |
| `cross_up` | `signal_N` 从 trigger 下方上穿 trigger | 普通恢复上穿 |
| `low_to_trigger` | `signal_N` 先跌到 low 以下，然后再上穿 trigger | 先恐慌/低参与度，再恢复 |

例子：

`low_to_trigger, MA50, low=30%, trigger=45%` 的含义是：使用 MA50 参与度的 5 日均线；先观察到参与度跌到 30% 以下，之后当它重新上穿 45% 时触发一次 recovery event。

## Return And Baseline

目标资产：

- QQQ
- SOXX

Forward return:

```text
forward_return_H = target close at T+H / target open at T+1 - 1
```

这样做是为了避免同日收盘信号和同日收盘收益之间的前视偏差。信号在 T 日收盘后可知，最早按 T+1 open 参与。

测试 horizons:

- 5 trading days
- 10 trading days
- 20 trading days
- 60 trading days

Baseline:

- 不是和 0 比，而是和同年份随机日子的 forward return baseline 比。
- 这样至少部分控制 2021、2022、2023、2024、2025 不同市场环境造成的收益基准差异。

统计输出：

| Field | Meaning |
| --- | --- |
| `event_n` | 该参数在样本内触发的可用事件数 |
| `mean_return` | event 之后目标资产平均 forward return |
| `baseline_mean_return` | 同年份基准 forward return |
| `diff_mean` | event return - baseline return |
| `hit_rate` | event 后 forward return 为正的比例 |
| `p_value` | 单侧 t-test，检验 event 是否优于同年份 baseline |
| `q_value` | 同一测试族内对 p-value 做 BH-FDR 多重检验校正后的值 |
| `bootstrap_p_value` | bootstrap/empirical gate，检验 event improvement 是否稳健大于 0 |

## Pass/Fail Rule

这次 sweep 是 exploratory，所以不能只看 raw p-value。

采用的硬门槛：

```text
Pass = q_value < 0.1 AND bootstrap_p_value < 0.1
```

含义：

- `q_value < 0.1`: 在这一批参数搜索里，经过 FDR 多重检验校正后仍然足够靠前。
- `bootstrap_p_value < 0.1`: 通过重抽样经验检验后，改善不是只靠少数点偶然撑起来。
- 两者同时满足才算“值得进入下一轮 hardening”。

这不是说“信号有 90% 概率为真”。它只是一个研究筛选门槛：在扫了大量参数之后，只有同时过 FDR 和 bootstrap 的候选，才有资格继续讨论。

事件数解释：

- `event_n < 10`: 只看方向，不做严肃判断。
- `event_n = 10-14`: 勉强可读，但统计功效很弱。
- `event_n >= 15`: 才比较适合作为下一轮 hardening 的最低起点。

本报告展示 `event_n >= 10` 的 Full sample leaders，是为了透明说明“最好看的 raw result 长什么样”；不是说 `N=10` 已经足够上线。

## Sweep Size

| Item | Value |
| --- | ---: |
| Parameter rows | 16896 |
| Event rows | 7531 |
| Full sample with-delisted_partial rows with `event_n >= 10` | 1304 |
| Rows passing `q < 0.1` and `bootstrap < 0.1` | 0 |

解释：

`16896` 是完整参数网格在 target / horizon / sample / universe variant 上展开后的结果行数。真正用于判断 full broad recovery 的主观察集是：

```text
sample = Full
universe_variant = with_delisted_partial
event_n >= 10
```

这个集合有 `1304` 行，没有任何一行通过双重门槛。

## Full Sample Leaders

下面是每个 target/horizon 下按 `diff_mean` 排名最高的一行。所有收益均为简单收益率。

| Target | Horizon | Event | MA | Low -> Trigger | N | Mean | Baseline | Diff | Hit | p | q | Bootstrap |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| QQQ | 5d | low_to_trigger | 10 | 15% -> 50% | 10 | 1.79% | -0.05% | 1.84pp | 70.0% | 0.069 | 0.548 | 0.144 |
| QQQ | 10d | low_to_trigger | 50 | 30% -> 45% | 10 | 3.29% | 0.19% | 3.10pp | 90.0% | 0.021 | 0.546 | 0.042 |
| QQQ | 20d | low_to_trigger | 10 | 15% -> 50% | 10 | 2.13% | 0.04% | 2.09pp | 60.0% | 0.213 | 0.577 | 0.267 |
| QQQ | 60d | cross_up | 40 | -> 25% | 10 | 5.22% | 2.22% | 3.00pp | 70.0% | 0.132 | 0.869 | 0.198 |
| SOXX | 5d | low_to_trigger | 50 | 35% -> 50% | 11 | 3.30% | 0.51% | 2.79pp | 81.8% | 0.013 | 0.568 | 0.052 |
| SOXX | 10d | low_to_trigger | 50 | 30% -> 45% | 10 | 4.75% | 0.77% | 3.98pp | 60.0% | 0.102 | 0.639 | 0.116 |
| SOXX | 20d | low_to_trigger | 40 | 35% -> 60% | 10 | 5.67% | 1.69% | 3.98pp | 80.0% | 0.088 | 0.712 | 0.194 |
| SOXX | 60d | cross_up | 50 | -> 30% | 11 | 7.05% | 3.43% | 3.62pp | 54.5% | 0.200 | 0.829 | 0.255 |

读法：

- QQQ 10d 的 `MA50, 30% -> 45%` 是最值得注意的 raw candidate：`diff_mean +3.10pp`，`hit_rate 90%`，raw p 和 bootstrap 都好看。
- 但它只有 `N=10`，而且 FDR 后 `q=0.546`，说明在整个参数搜索空间里，它并没有脱颖而出。
- SOXX 5d 的 `MA50, 35% -> 50%` raw p 和 bootstrap 接近，但 FDR 后同样失败。

## Best Q-Value Cluster

按 `q_value` 排，最靠前的候选几乎都集中在 QQQ 10d 的 `low_to_trigger`：

| Target | Horizon | Event | MA | Low -> Trigger | N | Diff | p | q | Bootstrap |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| QQQ | 10d | low_to_trigger | 50 | 30% -> 45% | 10 | 3.10pp | 0.021 | 0.546 | 0.042 |
| QQQ | 10d | low_to_trigger | 10 | 25% -> 40% | 19 | 1.73pp | 0.050 | 0.546 | 0.100 |
| QQQ | 10d | low_to_trigger | 10 | 35% -> 40% | 30 | 1.15pp | 0.059 | 0.546 | 0.128 |
| QQQ | 10d | low_to_trigger | 40 | 25% -> 55% | 10 | 2.23pp | 0.063 | 0.546 | 0.112 |
| QQQ | 10d | low_to_trigger | 40 | 25% -> 50% | 10 | 2.73pp | 0.072 | 0.546 | 0.130 |

这个 cluster 有研究价值：

- 方向一致：都是从低参与度恢复。
- 目标一致：QQQ 10d 最明显。
- 参数相邻：MA10/40/50，trigger 多在 40-55%。

但它仍然不是 deployable edge：

- 最好的一组只有 `N=10`。
- `q=0.546` 距离 `0.1` 很远。
- 参数空间很宽，raw p-value 很容易被搜索过程放大。

更准确的表达是：

> broad breadth recovery 有“味道”，但当前样本长度和 survivorship 口径不足以证明它有稳定 alpha。

## OOS Leaders Are Too Small

OOS 从 2025-01-01 开始。表面上看，OOS 的部分 recovery row 很强：

| Target | Horizon | Event | MA | Low -> Trigger | N | Mean | Baseline | Diff | Hit | p | q | Bootstrap |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| QQQ | 10d | low_to_trigger | 10 | 20% -> 45% | 3 | 8.24% | 0.78% | 7.46pp | 100.0% | 0.045 | 0.740 | 0.006 |
| QQQ | 20d | low_to_trigger | 10 | 20% -> 40% | 3 | 11.75% | 1.15% | 10.59pp | 100.0% | 0.076 | 1.000 | 0.024 |
| SOXX | 10d | low_to_trigger | 10 | 20% -> 45% | 3 | 13.82% | 2.52% | 11.30pp | 100.0% | 0.059 | 1.000 | 0.014 |
| SOXX | 20d | low_to_trigger | 10 | 20% -> 40% | 3 | 21.91% | 4.45% | 17.46pp | 66.7% | 0.144 | 1.000 | 0.078 |

这些行不能用于上线，原因很简单：

- `event_n = 3`，一个事件就能极大改变结论。
- OOS 覆盖的是 2025-2026 这段强趋势环境，对科技/半导体尤其友好。
- q-value 仍然失败，说明它不是在参数族里稳健突出的结果。

所以 OOS 只能说明：**2025 之后有几次低位恢复确实踩到了 QQQ/SOXX 的强反弹，但事件太少。**

## Interpretation

这轮结果比“完全没有信号”稍微复杂一点。

可以观察到的 pattern：

1. `low_to_trigger` 明显比单纯 `cross_up` 更像有效事件。
2. 最好的 raw candidate 多集中在 MA10 / MA40 / MA50。
3. QQQ 的 10d horizon 比 5d/20d/60d 更干净。
4. SOXX 的 raw diff 更大，但波动也更大，FDR 更难过。
5. recovery 信号更像“risk-on rebound marker”，不是持续型 trend regime。

但作为策略证据，它失败在三点：

1. **多重检验不过**：raw p-value 好看的行在 16896 行搜索空间里不稀奇，FDR 后没有 survivor。
2. **事件数不足**：很多最好看的参数只有 10 次左右事件；OOS 更是只有 3 次。
3. **survivorship 未完全关闭**：当前 delisted overlay 是 partial，不足以支撑强结论。

我的研究判断：

> broad breadth recovery 不是“已经证伪到永远不用看”，而是“当前数据和统计门槛下，不足以上线”。如果未来要继续，只能作为下一轮 hardening hypothesis，而不是调参继续找赢家。

## What Would Be Needed To Promote It

如果 CC reviewer 认为值得继续，下一轮不应该继续扩大参数网格，而应该缩小成一个预注册 hypothesis：

```text
Signal: low_to_trigger
MA: 10 or 50
Low: 20-30%
Trigger: 40-45%
Target: QQQ first, SOXX second
Horizon: 10d
Minimum event_n: preferably >= 30
```

然后做 hardening：

- 更长历史：至少覆盖 2018 Q4、2020 COVID、2022 bear、2023 AI rebound。
- 完整 survivorship overlay：不只是 21 只 large-cap delisted，而是 `$1B+` broad 历史退市/并购样本。
- 固定参数后再 OOS：不能继续 sweep 后挑最优。
- 加入 execution realism：T+1 open 可执行性、10-20 bps cost、信号公布延迟。
- 检查 regime dependency：确认它是不是只在 2025 AI bull leg 有效。

## Review Checklist For CC

请重点审这些点：

- Universe: `$1B+ PIT` 是否足够代表“完整 broad”？是否还应该包括 micro/small cap below $1B？
- Survivorship: partial delisted overlay 是否会系统性高估 recovery 后收益？
- Event construction: `low_to_trigger` 的 armed/cross/cooldown 逻辑是否合理？有没有重复触发或漏触发？
- Return timing: `T+1 open -> T+H close` 是否正确避免前视？
- Baseline: 同年份随机日 baseline 是否足够，还是应该用 matched volatility / matched drawdown / SPY regime baseline？
- Multiple testing: 当前 FDR family 按 `universe_variant, sample, event_family, target, horizon` 分组是否太宽或太窄？
- OOS interpretation: 是否应直接把 `event_n < 10` 的 OOS leaders 从报告主表移到 appendix？
- Promotion rule: `q < 0.1 AND bootstrap < 0.1` 是否过严、过松，还是符合 exploratory sweep 的纪律？
- Next step: 是彻底停止，还是冻结一个 QQQ 10d low_to_trigger hypothesis 做更长历史 hardening？

## Artifacts

Generated files:

- `data/breadth_study_1b/recovery_sweep.csv`
- `data/breadth_study_1b/recovery_sweep_events.csv`
- `data/breadth_study_1b/daily_breadth.csv`
- `data/breadth_study_1b/coverage_audit.csv`
- `docs/research/2026-04-29-broad-breadth-full-broad-1b-study.md`
- `docs/research/2026-04-29-broad-breadth-full-broad-1b-recovery-sweep.md`

Relevant code:

- `backtest/breadth_study/core.py`
- `backtest/breadth_study/recovery_sweep.py`
- `scripts/run_broad_breadth_study.py`
- `scripts/run_broad_breadth_recovery_sweep.py`

Reproduction command used for this report shape:

```bash
"/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/run_broad_breadth_recovery_sweep.py \
  --market-db "/Users/owen/CC workspace/Finance/data/market.db" \
  --output-dir data/breadth_study_1b \
  --overlay-json "/Users/owen/CC workspace/Finance/data/pool/delisted_large_caps.json" \
  --report-path docs/research/2026-04-29-broad-breadth-full-broad-1b-recovery-sweep.md \
  --min-market-cap 1000000000
```

## Bottom Line

完整 broad 口径没有救回这个信号。

最公平的结论是：

> 市场参与度 recovery 在 QQQ 10d 上有一些 raw pattern，尤其是 `low_to_trigger`，但在 `$1B+ broad` 上经过多参数 sweep、FDR 和 bootstrap 后没有统计 survivor。当前不能上线，只能作为未来更长历史、更完整 survivorship 数据下的预注册假设。
