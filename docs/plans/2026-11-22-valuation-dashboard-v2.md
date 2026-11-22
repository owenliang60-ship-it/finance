# 估值大屏 v2（OWE-13 迭代 + OWE-14 首批台账）实施计划

**Goal**：在 v1 只读底座上，把页面升级为「4 面板实时 SVG 大屏 + 分部驱动营收 + 净利率法全区间演算 + 台账参考情景」，并交付 MU、TSM 两份真实台账（真库退出码 0）。

**North Star**：`docs/design/north-star.md` 第二层「分析层 → 基本面分析（季频）」。v1 计划：主干 `docs/plans/2026-11-22-valuation-scenario-workflow.md`。

**环境**：worktree `.worktrees/valuation-owe13`；解释器 `"/Users/owen/CC workspace/Finance/.venv/bin/python"`；真库 `--db "/Users/owen/CC workspace/Finance/data/market.db"`；node v22 仅用于测试。

## 1. 文件范围

| 动作 | 文件 | 说明 |
|---|---|---|
| 不动 | `terminal/prosperity/`、`src/data/`、`CLAUDE.md`、`scripts/valuation.py` 主流程 | CLI 只改 docstring 的计划指针 |
| 加法 | `terminal/valuation/facts.py` | `FactPack` 加一个字段 `next_q4` |
| 加法 | `terminal/valuation/ledger.py` | 可选键扩展，`schema` 仍为 1 |
| 小改 | `terminal/valuation/gates.py` | 只动 B1 判据，新增 S1；P/C/G 不动 |
| 改 | `terminal/valuation/kernel.py`、`assets/kernel.js` | 删 `bridge_eps`、`scenario_state`；加 6 个函数 |
| 改 | `terminal/valuation/engine.py` | `_body` 重组；`pe_history`、`mild_zone`、`cycle_replays`、`incremental_margins`、`_cyclical`、`_status`、`_meta` 复用 |
| 重写 | `assets/state.js`、`assets/ui.js`、`assets/page.css`、`terminal/valuation/render.py` | `_e`、`_asset`、`_payload_json`、`_gates`、`_sources` 复用 |
| 新增 | `assets/charts.js` | 纯函数，输出绘图清单，不碰 DOM |
| 新增 | `terminal/valuation/ledgers/MU/`、`terminal/valuation/ledgers/TSM/` | `ledger.toml` + `sources/*.txt` |
| 改 | `tests/fixtures/valuation/ACME/ledger.toml`、`docs/skills/valuation/SKILL.md` | 夹具加情景；SOP 同步 |

**v2 明确的减法**
- 删除沙盒（保存情景再填权重），由固定 4 情景权重取代。
- 删除营收总滑块上的指引刻度；过门控的指引全部进文字备注，不进算术。
- 增量利润率三读数（历史 / 街口 / Boss）保留在 payload，只在审计栏列一行。
- `[[drivers]]` 保留为收入增量滑块；MU、TSM 台账不设。

## 2. 布局与数据流

```
┌──────────────────────────── KPI Strip（全宽）────────────────────────────┐
│ 标的·日期 │ Ref Price │ Mkt Cap │ NTM Window │ 隐含P/E │ Custom │ EV │ 徽章 │
├────────────┬─────────────────────────┬─────────────────────────────────┤
│ Control    │ Panel 1 情景区间条       │ Panel 2 分部堆叠柱               │
│ Deck       ├─────────────────────────┴─────────────────────────────────┤
│ 320px      │ 营收拆分逻辑卡（HTML，紧贴 Panel 2 下沿，跨两列）            │
│ sticky     ├─────────────────────────┬─────────────────────────────────┤
│            │ Panel 3 季度营收走势     │ Panel 4 EPS × P/E 热力矩阵       │
├────────────┴─────────────────────────┴─────────────────────────────────┤
│ <details id="audit"> 门控报告 + 出处 + sha256 + 引文（默认收起）          │
└─────────────────────────────────────────────────────────────────────────┘
```

- 宽度 ≥1280px：CSS Grid `320px 1fr 1fr`，控制台 `position: sticky`，拖滑块时四面板始终可见。
- 宽度 <1280px：面板两列，控制台移到面板下方，不 sticky。
- 数据流不变：`facts + ledger + gates → engine.build_payload → render → HTML`。JS 侧 `state → reduce → derive → charts（绘图清单）→ ui（createElementNS）`。

## 3. 数据核对（2026-11-22 真库只读实测）

| 项 | 工单 | 真库 | 结论 |
|---|---|---|---|
| MU 最新季营收 / 街口 EPS | 54,229M / 33.42 | 54,229M / 33.42 | 一致 |
| MU 分部合计 | 54,223M | 偏差 −0.011% | G1a 通过 |
| MU 净利率：中位 / 18–20 谷 / 21–24 谷 | 23.1% / 10.5% / −56.4% | 23.1% / 10.5%（2019-11-28）/ −56.4%（2023-03-02） | 一致 |
| MU 最新季净利率 | 70.8% | 70.7%（街口 EPS × 摊薄股本 ÷ 营收） | 口径差 0.1pp |
| MU 共识隐含净利率 / 隐含 NTM P/E | — | 72.6% / 3.40x | 隐含 P/E 低于历史 P10（6.86x） |
| TSM 最新季营收 / EPS | 1,494.243B TWD / 147.15 | 一致 | 一致 |
| TSM 美元营收 | US$46.72B | `fmp_earnings.revenue_actual` US$46.06B | 差 1.4%；台账不登记美元营收 |
| TSM 净利率：中位 / 18–20 谷 / 21–24 谷 | 37.8% / 27.7% / 36.8% | 37.8%（全部 41 季）/ 27.7%（2019-06-30）/ 36.8%（2024-06-30） | 36.8% 只在窗口 2021-09-01 至 2024-06-30 成立；日历 2021–24 的最低是 36.1% |
| TSM 共识隐含净利率 / fx0 / 隐含 P/E | — | 50.4% / 31.778 / 14.78x | — |
| TSM 未来 4 季 Q 行分析师数 | — | 7 / 7 / 2 / 2；4 季合计是 NTM 混合总额的 1.0134 倍 | Panel 3 缩放系数 0.9868 |

**由此定的规则**
- 净利率历史刻度只由引擎从库里算，页面只有这一个来源。`secondary_note` 不得重复写这些历史数字（去掉工单注释里「10Y 历史净利率…」那一句），否则同页会出现两套数。
- 周期谷底刻度按台账 `[[cycles]]` 窗口取窗口内最低季度净利率，标签用周期 `label`。

## 4. 内核合约（`kernel.py` ↔ `assets/kernel.js`）

规则沿用 v1：同名同参；入参先过 `fin`；缺失返回 `None`/`null`；求和用显式左到右循环；同构测试要求 Python 与 JS 结果逐位相等。

**保留**：`fin`、`zone`、`to_usd`、`implied_pe`、`scenario_value`、`pct_diff`、`pct_rank`、`weighted_value`。
**删除**：`bridge_eps`、`scenario_state`。

**新增**

| 函数 | 定义 | 返回 `None` 的条件 |
|---|---|---|
| `seg_revenue(bases, adjs, driver_delta)` | `Σ bases[i] × (1 + adjs[i])`，再加 `driver_delta` | 长度不等、为空、任一非有限 |
| `implied_margin(eps, shares, rev)` | `eps × shares / rev` | `shares ≤ 0` 或 `rev ≤ 0` |
| `margin_eps(rev, margin, shares)` | `rev × margin / shares`（运算顺序固定） | `shares ≤ 0` |
| `quarter_path(shape, total)` | `[shape[i] × total / Σshape]` | `Σshape ≤ 0`、任一 `shape[i] < 0` |
| `grid_pos(levels, x)` | 等距网格上的分数下标，夹在 `[0, n−1]` | `n < 2`、非严格递增、`x` 缺失 |
| `live_state(s)` | 见下 | — |

**`live_state(s)`**
入参键：`bases, adjs, driver_delta, margin, shares, fx, pe, pe_half, price, rev_cons, half_width, eps_mode, direct_eps`。
- `rev = seg_revenue(bases, adjs, driver_delta)`
- `eps_mode == "margin"`：`net_income = rev × margin`，`eps_local = margin_eps(rev, margin, shares)`
- `eps_mode == "direct"`（B1 未过）：`eps_local = fin(direct_eps)`，`net_income = None`
- `eps_usd = to_usd(eps_local, fx)`
- `eps_state`：`"pos"`（>0）/ `"nonpos"`（≤0）/ `"missing"`
- `value = scenario_value(pe, eps_usd)`；`value_lo = scenario_value(pe − pe_half, eps_usd)`；`value_hi = scenario_value(pe + pe_half, eps_usd)`
- `zone = zone(rev, rev_cons, half_width)`，只用于提醒，不影响任何数值
- 出参键固定 11 个：`rev, net_income, eps_local, eps_usd, eps_state, implied_pe, value, value_lo, value_hi, value_vs_price, zone`

**不变量**
1. 初始态：`adjs` 全 0、`margin = margin_cons`、`pe = pe_init`、`pe_half = 0`、`fx = fx0`，则 `|value / price − 1| ≤ 1e-12`。
   - `margin_cons = implied_margin(eps_cons, shares, rev0)`，其中 `rev0 = seg_revenue(bases, 全 0, 0)`。
   - `pe_init = implied_pe(price, 初始态 eps_usd)`。
2. `eps_mode == "margin"` 且输入有限时，`rev、net_income、eps_local、eps_usd` 在滑块全区间恒为有限数。`eps_state == "nonpos"` 时 `value` 为 `None`，页面显示「负盈利（周期谷底）」。
3. 点任一参考情景后，`derive(state).live` 与 payload 里该情景的 `out` 逐键相等。
4. `weighted_value` 不改：权重合计偏离 100 超过 0.01，或正权重情景的 `value` 缺失，返回 `None`。
5. payload 仍用 `allow_nan=False`；页面文本零 `NaN/undefined/Infinity`。

## 5. 台账扩展（向后兼容，未知键仍报错）

| 位置 | 新键 | 规则 |
|---|---|---|
| `[segments]` | `axis_rationale` | 可选，非空字符串 |
| `[segments]` | `roles` | 可选，字符串列表，长度 = `names` |
| `[segments]` | `colors` | 可选，长度 = `names`，每项匹配 `^#[0-9a-fA-F]{6}$`（要写进 SVG 属性，必须严格） |
| `[segments]` | `secondary_note` | 可选，非空字符串 |
| `[[segments.quarters]]` | `pct_derived` | 可选布尔；真 = 该季数值由披露百分比折算 |
| `[assumptions]` | `consensus_weight` | 可选，0–100 |
| `[[scenarios]]` | `id, label, tone, thesis, seg_adjs, net_margin, pe_mid, pe_half, weight` | 全必填 |

`[[scenarios]]` 校验（任一不满足抛 `LedgerError` 并写明字段路径）
- 最多 3 条；`id` 唯一，且不得为 `consensus`、`custom`；`tone ∈ {bull, base, bear}` 且不重复。
- `seg_adjs` 长度：有 `[segments]` 时等于 `names` 长度，否则为 1；每项 ∈ [−0.60, 0.40]。
- `−1 < net_margin ≤ 1`；`pe_mid > 0`；`0 ≤ pe_half < pe_mid`；`0 ≤ weight ≤ 100`。
- 有情景时，`consensus_weight + Σ weight` 必须等于 100（±0.01）。

`Ledger` 数据类加：`segment_axis_rationale, segment_roles, segment_colors, segment_secondary_note, scenarios, consensus_weight`。

## 6. 门控改动（最小）

- **B1（判据替换）**：VERIFIED 当且仅当 `rev_cons > 0`、`shares > 0`、`eps_cons` 有限。未过：`eps_mode = "direct"`，分部与净利率滑块禁用，横幅说明。
- **B2（语义收窄）**：营收意外样本不足 8 季时 UNVERIFIED，只关掉黄色提醒，不禁用任何滑块。退出码规则不变（任何非 VERIFIED → 10）。
- **S1（新增，范围 `segment`）**：仅当台账登记了分部季度时产出。VERIFIED 当且仅当：`usable` 里最新的分部季度与库内最新已公告财报季相差 ≤ `ESTIMATE_MATCH_DAYS`，且各分部值 ≥0、合计 >0。未过：分部切分停用，退回单一总营收滑块；带 `seg_adjs` 的参考情景全部禁用并写原因。
- **G1a 说明文字**：`pct_derived = true` 的季度，detail 追加「百分比折算，对账为构造性」。状态判定不变。

## 7. Payload v2（`engine._body`）

保留键：`matrix`（只供周期双口径与样本脚注）、`consensus`、`margins`、`pe_sorted`、`guidance_notes`、`replay_note`。新增或改写：

```
consensus += {margin_cons, mkt_cap (= price × shares), ntm_window}
model = {
  mode: "segments" | "total",  eps_mode: "margin" | "direct",  direct_eps,
  currency, split_quarter,
  segments: [{name, role, color, latest_value, share, base}],        # total 模式为单元素「总营收」
  logic: {axis_rationale, secondary_note, g1_detail, pct_derived, formula_note} | null,
  init: {adjs, margin, pe, pe_half: 0, fx}
}
sliders = {seg: {min: -0.60, max: 0.40, disabled}, margin, pe, fx | null, drivers}
markers = [{slider: "margin" | "pe" | "fx", value, label, kind, source}]
margin_history = {n, n_missing, median, min, max, latest: {fiscal_date, value}}
scenarios = [{id, label, tone, thesis, weight, adjs, margin, pe, pe_half,
              enabled, reason, out, seg_rev, quarters}]              # 第 0 条恒为 consensus
cycles = [{id, label, enabled, reason, adj, margin, trough_quarter, rev_ratio, peak, trough}]
trajectory = {hist: [{fiscal_date, revenue}×4], future_dates[4], shape[4], analysts[4], scale, note} | {reason}
heatmap = {eps_levels[5], pe_levels[5], cells[5][5], pct[5][5], price_cell: [r, c],
           marks: [{id, r_pos, c_pos}]} | {reason}
ev_init = weighted_value(各情景 out.value, 台账权重)
selfcheck = [{name, in, out}]
```

**计算规则**
- `share[i] = values[i] / Σvalues`（用台账自身合计归一）；`base[i] = rev_cons × share[i]`。`formula_note` 固定写：「按最新已核实季报占比切分卖方 NTM 总营收；库内无分部级卖方共识，此为等比切分，不是分部预测，可在控制台独立调整。」
- 历史净利率逐季：`non_gaap` 用「街口 EPS × 该季摊薄股本 ÷ 营收」；`ifrs/gaap` 用「报表净利 ÷ 营收」。取库内全部已公告季；缺街口 EPS 的季跳过并计入 `n_missing`。
- 净利率滑块范围：候选集 = 全部历史季度净利率 + `margin_cons` + 各情景 `net_margin`。`min = floor(候选最小, 10pp)`，`max = ceil(候选最大 + 5pp, 5pp)`。取整前先 `round(x, 6)`。实测 MU 得 −60% 至 +80%，TSM 得 +20% 至 +65%。
- 净利率刻度：`10Y 中位`、每个周期 `{label} 周期谷底（季度日期）`、`最新季实绩`、`卖方共识隐含`。出处写窗口与口径。
- P/E 刻度沿用 v1 的 P10–P90；P/E 与汇率滑块范围沿用 v1。
- consensus 情景：`adjs` 全 0、`margin_cons`、`pe_init`、`pe_half = 0`；区间取 `to_usd(eps_low/eps_high) × pe_init`，标注「卖方 EPS 低/高值 × 现价隐含 P/E，拥挤度读数」。
- 周期回放 v2：复用 `cycle_replays` 的峰谷与启用判定。`adj = clamp(rev_ratio − 1, −0.60, 0.40)`（各分部同值）；`margin` = 窗口内最低季度净利率。点击只改 `adjs` 与 `margin`，不改 P/E 与汇率。
- 走势：`next_q4` = 当前快照下前 4 个未公告季的 Q 行。`scale = rev_cons / Σshape`。各情景 `quarters = quarter_path(shape, 情景 rev)`。不足 4 行或缺 `rev_avg` 时给 `reason`，Panel 3 只画历史。`note`：「未来 4 季 = 卖方季度形状 × 情景 NTM 总额比例，不含季度节奏假设」；`ntm_fy_blend` 时追加缩放系数与各季分析师数。
- 热力矩阵：EPS 锚点 = 共识、卖方低/高值、各启用情景的正 EPS（USD）；P/E 锚点 = `pe_init` 与各情景 `pe_mid ± pe_half`。两轴各取锚点最小到最大的 5 档等距。锚点不足 2 个不同值时，EPS 退回 `共识 × (0.5, 0.75, 1, 1.25, 1.5)`，P/E 退回历史 P10–P90。共识 EPS ≤0 时给 `reason`。`price_cell` = `|pct|` 最小的格。
- `selfcheck` 向量：初始态；每个分部滑块、净利率、P/E、汇率的两端；每个启用情景；每个启用周期回放。

## 8. 四面板规范（`assets/charts.js`）

**通用合约**
- 每个面板一个纯函数 `panelN(view, payload)`，返回绘图清单：`[{t: "rect" | "line" | "path" | "circle" | "text", …}]`。`ui.js` 用 `createElementNS` 逐项落到 `<svg viewBox="0 0 560 340">`，每次渲染整面板重建，用 `requestAnimationFrame` 合并连续输入。
- `text_w(s, size)`：CJK 与全角字符按 `1.0 × size`，其余按 `0.58 × size`。每个 `text` 项带 `x, y, size, anchor, w`。
- 文字框定义：横向由 `anchor` 与 `w` 得出；纵向 `[y − size, y + 0.25 × size]`。
- **防重叠总则（测试逐面板断言）**：任意两个文字框不相交；所有文字框落在 viewBox 内。
- `spread(ys, gap, lo, hi)`：按 y 排序后把间距不足 `gap` 的标签推开，结果夹在 `[lo, hi]`。
- 金额格式 `fmt_money(x, currency)`：USD 前缀 `$`，TWD 前缀 `NT$`，其余「币种代码 + 空格」；≥1e12 用 `T`（两位小数），≥1e9 用 `B`（一位小数）。

**配色（字面色值，零 `var(--`）**

| 用途 | 色值 |
|---|---|
| Consensus / Bull / Base / Bear / Custom | `#6e7781` / `#1a7f37` / `#0969da` / `#cf222e` / `#5b4bdb` |
| Current Price（橙虚线）/ EV（深蓝点线） | `#d4730b` / `#1f3a8a` |
| 分部缺省调色板 | `#4c78a8 #f58518 #54a24b #b279a2 #9d755d #72b7b2` |
| 热力三锚 | `#d73027`（−）/ `#fee08b`（0）/ `#1a9850`（+） |

**Panel 1 · Scenario Fair Value Ranges vs. Current Price**
- 数据：`rows = [consensus, bull, base, bear, custom]`，每行 `{label, tone, lo, mid, hi, pct, eps_state}`；另有 `price`、`ev`。
- 几何：行标签列 x 0–96（右对齐于 92）；绘图区 x 100–380；数值标签列 x 388–552（左对齐）。首行中心 y=60，行距 52，条高 16，中值白点半径 4。
- 数值标签：`$815 (+37%) [713–917]`，字号 11。宽度超过 164 时去掉方括号段。`eps_state == "nonpos"` 的行不画条，标签写「负盈利（周期谷底）」。
- x 轴域：全部 `lo/hi/price/ev` 的最小 × 0.9 到最大 × 1.05。
- 竖线标签分带：`Current $594.51` 固定在顶带 y=34；`EV $747 (+26%)` 固定在底带 y=332；x 夹在绘图区内。两者不同带，不会相撞。
- EV 为 `null` 时不画线、不画标签。Custom 行条加粗描边。

**Panel 2 · NTM Revenue Stack by Segment**
- 数据：`bars = [consensus, bull, base, bear, custom]`，每柱 `{label, total, blocks: [{name, color, value}]}`。
- 几何：绘图区 x 48–552、y 40–292；5 柱等分，柱宽 56。柱顶总额字号 11；柱下标签 y=310。
- 色块内金额字号 10：仅当块高 ≥14 且 `text_w ≤ 52` 时显示，否则隐藏。
- 图例 y=330，按实测宽度从左排；总宽超过 504 时名称截到 10 字符加 `…`。
- 禁用的情景柱画灰色虚框，写「不可用」。

**营收拆分逻辑卡（HTML，`#logic-card`）**
- 标题行：「营收拆分逻辑 · 最新已核实季 {split_quarter}」+ G1 徽章。徽章文字：`pct_derived` 为真写「G1a 百分比折算（构造性）· G2 引文通过」，否则写「G1a 对账通过 · 偏差 x.xx%」。
- 正文：`axis_rationale`；分部小格（色点、名称、最新季金额、占比、`role`）；`secondary_note`；`formula_note`。
- `mode == "total"` 时整卡替换为一句原因（无台账分部 / S1 未过 / G3d 重述待确认）。
- 文字是普通 HTML，自动换行，不存在 SVG 文字溢出问题。

**Panel 3 · Quarterly Revenue Trajectory**
- 数据：`hist[4]`、`future_dates[4]`、`lines = [{id, tone, values[4]}]`（consensus 灰虚线；bull/base/bear 细实线；custom 线宽 3）。
- 几何：绘图区 x 52–468、y 40–292；右侧标签列 x 472–556。8 个点等距。x 轴标签 `YY-MM` 加 `A` 或 `E`，字号 10，y=310。
- 橙色竖点线画在最新已公告季（第 4 个点）。各情景线都从该点出发。
- 行末标签（`Base 58.4B`，字号 10）先按终点 y 排，再过 `spread(ys, 12, 46, 292)`。
- 口径注释用面板下方 HTML `<p class="note">` 显示 `trajectory.note`。

**Panel 4 · Valuation Matrix Heatmap**
- 数据：`eps_levels[5]`（高在上）、`pe_levels[5]`、`cells[5][5]`、`pct[5][5]`、`price_cell`、`marks`（Base 静态、Custom 实时）。
- 几何：网格 x 84–552、y 52–272（格宽 93.6、格高 44）。列头 y=44；行头右对齐于 x=80。
- 着色：`t = clamp(ln(value / price) / ln 2, −1, 1)`，即腰斩为满红、翻倍为满绿，三锚线性插值。`|t| > 0.7` 时格内文字用白色。
- 格内两行：金额（字号 11）与带符号涨跌幅（字号 10）。涨跌幅用文字标出，不只靠颜色。
- 高亮：`price_cell` 橙色 2px 描边；Base 蓝色描边加圆点；Custom 紫蓝圆点按 `grid_pos` 连续定位，并描边最近格。Custom 超出网格时点贴边；`eps_state == "nonpos"` 时点贴底边并标「负盈利」。
- 表底三行用 HTML `<p class="note">`：周期股双口径（Peak TTM × P10 / Mid TTM × P50）、历史 P/E 样本口径、卖方拥挤度读数。

**KPI Strip 与控制台（HTML）**
- KPI 元素：`#kpi-price`、`#kpi-mktcap`、`#kpi-window`、`#kpi-implied-pe`、`#kpi-custom`（金额 + 相对现价）、`#kpi-ev`（权重合计 100 才显示数值，否则显示原因）、`#badge-gates`（点击展开并滚到 `details#audit`）。
- 预设按钮条：`button[data-preset="consensus" | "<情景 id>" | "cycle:<id>"]` 与 `#btn-reset`。禁用按钮的 `title` 写原因。标题写「参考情景预设（可随时拖动滑块与修改权重覆盖）」。
- 滑块：`#sl-seg-<i>`（色点、共识基数、百分比、实时金额）、`#sl-margin`、`#sl-pe`、`#sl-fx`（仅非 USD）、`#in-pe-half`。
- 刻度：轨道上画 2px 细线（不可点）；可点击刻度做成滑块下方的换行按钮组 `button.tick`，`title` 含出处。按钮组自动换行，所以刻度值接近时也不会重叠。
- `#pill-zone`：`zone == "tail"` 时显示黄色胶囊「已超出近 20 季 ±2σ 温和区，建议同步参考历史下行期净利率刻度」。不清空任何数值。
- 权重：`#w-consensus`、`#w-<情景 id>`，初值取台账。合计不为 100 时 `#w-note` 行内提示，EV 线与 EV 数值隐藏。
- 页脚声明改为：「本页呈现事实反推、台账登记的参考情景预设与你输入的情景演算；参考情景与权重是可覆盖的假设，不是模型结论。」

## 9. 任务拆解

每个 Task 固定：写失败测试 → 跑红 → 实现 → 跑绿 → 提交。全量命令：
`"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/terminal/valuation -q`

### Task 1 · 内核 v2 与同构
文件：`kernel.py`、`assets/kernel.js`、`test_valuation_kernel.py`、`test_valuation_kernel_parity.py`。
断言：
- `seg_revenue([3840e6, 2560e6], [0.10, 0.05], 0.0) == 6912e6`；长度不等、空列表、含 `NaN` 均返回 `None`。
- `implied_margin(10.0, 100e6, 6400e6) == 0.15625`；`rev = 0` 或 `shares = 0` 返回 `None`。
- `margin_eps(6912e6, 0.18, 100e6)` 约等于 `12.4416`；`margin_eps(4e9, -0.5642, 1e9)` 为负的有限数。
- `quarter_path([1, 1, 2], 8.0) == [2.0, 2.0, 4.0]`；全 0 返回 `None`。
- `grid_pos([6, 7, 8, 9, 10], 8.5) == 2.5`；`x = 3` 得 `0.0`；`x = 99` 得 `4.0`。
- `live_state` 初始态：`value` 约等于 `price`（相对误差 1e-12），`value_vs_price` 绝对值 ≤1e-12。
- `live_state` 负净利率：`eps_state == "nonpos"`，`value is None`，`rev、net_income、eps_local、eps_usd` 有限。
- `live_state` 的 `direct` 模式：`net_income is None`，`eps_local == direct_eps`。
- 同构：`CASES` 覆盖的函数集合恰为 14 个（8 个保留 + 6 个新增）；`live_state` 至少 12 例（含 `NaN`、`Infinity`、`undefined`、`fx = None`、`pe_half > pe`）；Python 与 node 结果逐位相等。
提交：`feat(valuation): net-margin kernel with segment revenue and parity`

### Task 2 · 台账扩展、`next_q4`、门控 B1/S1
文件：`ledger.py`、`facts.py`、`gates.py`、ACME 夹具、对应三个测试文件。
ACME 夹具追加：`axis_rationale`、`roles`、`secondary_note`、`consensus_weight = 5`，以及三条情景：

| id / tone | seg_adjs | net_margin | pe_mid ± pe_half | weight |
|---|---|---|---|---|
| bull | [0.10, 0.05] | 0.18 | 9 ± 1 | 20 |
| base | [-0.10, -0.05] | 0.14 | 8 ± 1 | 50 |
| bear | [-0.40, -0.30] | 0.08 | 7 ± 1 | 25 |

断言：
- v1 的最小台账与两份冒烟台账仍能加载（向后兼容）。
- 逐条 `LedgerError`：`roles` 长度不符；`colors = ["red", …]`；`seg_adjs` 长度不符；`seg_adjs` 含 `0.5`；`pe_half >= pe_mid`；`tone` 重复；`id = "custom"`；权重合计 95；`[[scenarios]]` 内未知键。
- `FactPack.next_q4`：ACME 为 4 行，财报日 `2026-12-31` 至 `2027-09-30`；快照过期时为空元组。
- ACME 全量台账：全部 VERIFIED，含 `S1`。
- 删掉 ACME 最新季分部、只留上年同季：`S1` 为 UNVERIFIED，退出码 10。
- 没有 `[segments]` 的台账：结果里没有 `S1`。
- 去掉 FY 共识行：`B1` 仍为 VERIFIED（v1 的 `test_missing_fy_rows_disable_bridge` 改写为此）。
- `shares = 0`：`B1` 为 UNVERIFIED。
- `pct_derived = true`：`G1a` 的 detail 含「百分比折算」。
提交：`feat(valuation): scenario ledger schema, next-quarter rows, S1 segment gate`

### Task 3 · 引擎 payload v2
文件：`engine.py`、`test_valuation_engine.py`。
断言（ACME，`pytest.approx`）：
- `model.mode == "segments"`；`base == [3840e6, 2560e6]`；`share == [0.6, 0.4]`；`consensus.margin_cons == 0.15625`；`sliders.pe.init == 8.0`。
- `scenarios[0].id == "consensus"` 且 `out.value` 约等于 80。
- 情景值：bull `111.9744`（区间 `99.5328–124.416`）；base `65.9456`；bear `22.9376`；`ev_init == 65.10208`。
- `scenarios[1].seg_rev == [4224e6, 2688e6]`；每个情景 `Σ quarters` 约等于 `out.rev`。
- `sliders.margin`：`min == 0.0`、`max == 0.25`；`markers` 含 `10Y 中位`（0.10）、`最新季实绩`、`卖方共识隐含`。
- `cycles[0]`：`enabled`、`adj == 0.0`、`margin == 0.10`。
- `heatmap.pe_levels == [6, 7, 8, 9, 10]`；`eps_levels` 首尾为 `3.2768` 与 `12.4416`；`cells[i][j] == scenario_value(pe_levels[j], eps_levels[i])`。
- `trajectory.scale == 1.0`（ACME 逐季加总）；`thin=True` 变体下 `scale` 约等于 `rev_cons / Σshape` 且 `note` 含「缩放」。
- 分部未过门控（G1a 冲突）：`mode == "total"`，单元素 `base == [rev_cons]`，三条情景 `enabled is False` 且 `reason` 非空，`ev_init is None`。
- `selfcheck` 每条的 `out` 等于 `kernel.live_state(in)`；名称含 `init`、`seg:0:min`、`margin:max`、`scenario:bull`、`cycle:2025-26`。
- 整页熔断时 payload 仍只有 `meta` 与 `status`。
- 库内事实含 `NaN` 时仍抛 `ValueError`。
提交：`feat(valuation): segment-driven payload with scenarios, trajectory, heatmap`

### Task 4 · 前端：state、charts、ui、css、render
文件：`assets/state.js`、`assets/charts.js`、`assets/ui.js`、`assets/page.css`、`render.py`、`test_valuation_state_js.py`、新增 `test_valuation_charts_js.py`、`test_valuation_render.py`。
状态：`{adjs, margin, pe, pe_half, fx, drivers, weights, preset_id}`。动作：`seg`、`set`、`driver`、`preset`、`weight`、`reset`。
预设语义：`consensus` 与参考情景会把 `fx` 重置为 `fx0`；周期回放只改 `adjs` 与 `margin`。
断言（node 子进程，沿用现有拼接方式）：
- `derive(init_state)` 与 `selfcheck[0].out` 逐键相等。
- 对每个启用情景：`derive(reduce(init, preset)).live` 与 `scenarios[i].out` 逐键相等。
- 全区间扫描：每个分部滑块取 `[min, max]`、净利率取 `[min, 0, max]`、P/E 取 `[min, max]` 的全部组合，`live.rev、net_income、eps_local、eps_usd` 均为有限数；`KPI Custom` 文本永不等于 `—`。
- 净利率取最小值时文本为「负盈利（周期谷底）」。
- 权重 `5/20/50/25`：`ev` 约等于 `65.10208`；把 bull 改成 10：`ev === null` 且原因为「权重合计不为 100」。
- 越界 `seg` 值被夹到 `[-0.60, 0.40]`；`reset` 恢复台账权重。
- `direct` 模式下 `seg` 与 `margin` 动作不改变状态。
- 四个面板在三种状态（初始、全部滑块最小、全部滑块最大）下：文字框两两不相交，全部在 `0 ≤ x ≤ 560`、`0 ≤ y ≤ 340` 内；清单里没有非有限坐标。
- Panel 2：块高 <14 的色块没有对应文字项。
- Panel 3：行末标签的 y 两两相差 ≥12。
- Panel 1：权重合计非 100 时，清单里没有 EV 线和 EV 标签。
- 构造一条超长数值标签（金额 12,345,678）：Panel 1 标签去掉方括号段后宽度 ≤164。
断言（render）：
- 必备元素：`id="p1"` 至 `id="p4"`、`id="logic-card"`、`id="deck"`、`id="sl-seg-0"`、`id="sl-margin"`、`id="sl-pe"`、`id="in-pe-half"`、`id="w-consensus"`、`id="w-bull"`、`data-preset="bull"`、`data-preset="cycle:2025-26"`、`id="btn-reset"`、`id="pill-zone"`、`id="kpi-custom"`、`id="kpi-ev"`、`id="badge-gates"`、`<details id="audit"`（无 `open`）。
- DOM 顺序：KPI → `deck` → `p1` → `p2` → `logic-card` → `p3` → `p4` → `audit`。
- 仍然成立：无 `<script src`、`<link`、`@import`、`url(`、`var(--`、`<img`；四个 JS 文件无 `innerHTML`、`outerHTML`、`insertAdjacentHTML`、`document.write`；内联脚本顺序为 `kernel, state, charts, ui` 且与文件逐字相等。
- 禁词表不变（`目标价`、`target price`、`建议仓位`、`买入`、`卖出`、`NaN`、`undefined`、`Infinity`）；新声明文字出现在可见文本里。
- 台账字符串 `</script><b>` 写进 `thesis`、`axis_rationale`、`roles` 后，页面里只出现转义形态。
- 熔断页：没有 `id="p1"`、没有 `type="range"`、没有 `id="deck"`；有横幅和门控表。
- `sl-fx` 仅在非 USD 台账出现。
提交：`feat(valuation): four-panel SVG dashboard with live control deck`

### Task 5 · MU、TSM 真实台账，SOP，真库验收
文件：`terminal/valuation/ledgers/MU/`、`terminal/valuation/ledgers/TSM/`、新增 `test_valuation_real_ledgers.py`、`docs/skills/valuation/SKILL.md`、`scripts/valuation.py`（仅 docstring）。
步骤：
1. 使用已从一手官网与 SEC EDGAR 实抓并提取的纯净原文（路径见工单，或直接从以下一手 URL 复抓），原样存为 `sources/<id>.txt`（UTF-8），用 `shasum -a 256` 写入台账：
   - MU 一手 URL（SEC EX-99.1，域名 `sec.gov`）：`https://www.sec.gov/Archives/edgar/data/723125/000072312526000018/a2026q4ex991-pressrelease.htm`（含 FQ4-26 与 FQ4-25 四大 BU 营收及 Non-GAAP EPS $33.42 原文）
   - TSM 一手 URL（官方 3Q26 Management Report PDF，域名 `investor.tsmc.com`）：`https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2026-10/f83780000ddc0c3b48e57a87eab6814c4ab35058/3Q26%20ManagementReport.pdf`（含 3Q26 平台占比 68%/21%/4%/4%/1%/2% 及 3Q25 同比与制程占比原文）
2. **硬规则**：`sources/*.txt` 不得手写或改写。抓取失败，或原文数字与工单不符，立即停下上报，不提交台账。
3. 每条 `quote` 取原文里含分部数字的逐字子串，规范化后 ≥20 字符。
4. MU 台账：`eps_basis = "non_gaap"`、`cyclical = true`、`unit = 1e6`、四个 BU 值与角色按工单；沿用冒烟台账的两个 `[[cycles]]` 窗口；三条情景与权重按工单；另登记一条 `[[eps_facts]]`（33.42）。
5. TSM 台账：`eps_basis = "ifrs"`、`reporting_currency = "TWD"`、`unit = 1e9`、四个平台值按工单、`pct_derived = true`；两个 `[[cycles]]` 用与 MU 相同的窗口；三条情景与权重按工单。
6. 两份 `secondary_note` 都不写历史净利率数字（见第 3 节规则）。TSM 台账不登记美元营收。
7. `SKILL.md`：首段与禁止事项改为「参考情景与权重只来自台账 `[[scenarios]]`，新建或改动须经 Boss 确认」；字段表补新键；7 步结构与退出码行不变。

断言（`test_valuation_real_ledgers.py`，不依赖真库，独立实现校验逻辑）：
- 两份台账 `load_ledger` 通过。
- 每个 `[[sources]]`：文件存在，sha256 与台账一致，URL 主机在 `t1_domains` 内。
- 每条 `quote` 规范化空白后是对应原文的子串，且 ≥20 字符。
- 分部合计 × `unit`：MU 对 `54_229e6` 偏差 ≤1%；TSM 对 `1_494_243_284_000` 偏差 ≤1%。
- 权重合计 100；MU 四个 `seg_adjs` 与工单逐项相等；TSM 同理。
- 两份台账的全部字符串不含禁词。

真库验收（只读；先后各跑一次 `shasum -a 256 data/market.db`，两次必须相同）：
```
… scripts/valuation.py MU  --ledger terminal/valuation/ledgers/MU/ledger.toml  --db "<真库>" --as-of 2026-11-22 --out /tmp/valuation-MU.html
… scripts/valuation.py TSM --ledger terminal/valuation/ledgers/TSM/ledger.toml --db "<真库>" --as-of 2026-11-22 --out /tmp/valuation-TSM.html
```
两条命令的退出码都必须是 0，`non_verified` 为空数组。

人工对照值（2026-11-22 快照手算，容差 ±1%；库更新后会变）：

| | Consensus | Bull | Base | Bear | EV |
|---|---|---|---|---|---|
| MU | $594.51（0%） | $1,066（+79%） | $815（+37%） | $387（−35%） | $747（+26%） |
| TSM | $319.15（0%） | $439（+38%） | $273（−14%） | $152（−52%） | $278（−13%） |

提交：`feat(valuation): MU and TSM verified ledgers with reference scenarios`，`docs(valuation): skill SOP for v2 scenarios`

**收尾检查**
- `git diff --stat main -- terminal/prosperity src/data CLAUDE.md` 输出为空。
- 全量单测全绿、无 skip。
- 把本计划存入 `docs/plans/2026-11-22-valuation-dashboard-v2.md` 并随 Task 1 提交。

## 10. 浏览器真机验收（`file://` 打开 MU、TSM 两页，1440×900）

每一步之后执行 `window.__valuationAudit()`，`badText` 必须为空数组。

**两页通用**
1. 控制台零 error、零 warning；Network 除文档本身零请求；`document.documentElement.dataset.selfcheck === "pass"`。
2. 首屏同时可见 KPI 条、控制台、四个面板；`details#audit` 收起。
3. 初始态：`#kpi-custom` 等于现价且显示 `0.0%`；Panel 1 的 Custom 行与 Consensus 行中值重合。
4. `#kpi-ev` 显示数值，与上表 EV 相差 ≤1%；Panel 1 蓝色点线在该位置。
5. 逐个拖分部滑块到两端：Panel 2 的 Custom 柱对应色块变化，Panel 3 紫蓝线移动，Panel 1 Custom 条移动，Panel 4 紫蓝点移动。四处同时变，无可感延迟。
6. 把任一分部拖到 −60%：`#pill-zone` 出现黄色胶囊；`#kpi-custom` 仍是数值，全页不出现孤立的 `—`。
7. 依次点 `Bull`、`Base`、`Bear`：全部滑块跳到对应参数；Custom 条与对应情景条完全重合；数值与上表相差 ≤1%。
8. 点净利率的每个刻度按钮：滑块跳到该值；按钮 `title` 含出处；按钮之间不重叠。
9. 点 P/E 的 P10–P90 刻度：Panel 4 紫蓝点横向移动；超出网格时贴边。
10. 权重改为 `5/10/50/25`：`#w-note` 提示合计不为 100，`#kpi-ev` 显示原因，Panel 1 的 EV 线消失。改回后恢复。
11. 点 `#btn-reset`：回到第 3 条状态，权重回到台账值。
12. 点 `#badge-gates`：页面平滑滚到审计栏并展开；门控表全 VERIFIED；出处含 URL、sha256 与逐字引文。
13. 目视四个面板：没有任何文字相互覆盖或溢出边框。窗口缩到 1100px 后再看一遍。
14. 页面文本不含「目标价」「建议仓位」「买入」「卖出」。

**MU 专项**
15. 逻辑卡显示四个 BU 的金额、占比（33.2 / 30.0 / 24.2 / 12.6%）、角色，以及「G1a 对账通过 · 偏差 0.01%」。
16. 净利率滑块范围 −60% 至 +80%；刻度含 `10Y 中位 23.1%`、`2018–19 周期谷底 10.5%`、`2022–23 周期谷底 −56.4%`、最新季、卖方共识。
17. 点 `2022–23 周期`：分部滑块跳到约 −57%，净利率跳到 −56.4%；`#kpi-custom` 显示「负盈利（周期谷底）」；Panel 1 的 Custom 行无条；Panel 4 的点贴底边。
18. 点 `2018–19 周期`：分部约 −43%，净利率 10.5%，`#kpi-custom` 为正数。
19. 页面没有 `#sl-fx`。

**TSM 专项**
20. 逻辑卡徽章写「G1a 百分比折算（构造性）」；四个平台占比 68 / 21 / 8 / 3%。
21. `#sl-fx` 存在，初值约 31.78；拖动后 `#kpi-custom` 变化，Panel 2 的本币金额不变。
22. 净利率滑块范围 +20% 至 +65%。
23. `2018–19 周期` 按钮禁用，`title` 写「峰 2020-06-30 之后无季度」；`2022–23 周期` 可点。
24. Panel 3 下方注释含「财年混合」「缩放」与分析师数 7 / 7 / 2 / 2。
25. Panel 2 柱顶金额用 `NT$…T` 格式，色块内文字不溢出。

**熔断页**
26. 用 v1 的冲突夹具生成一页：只有横幅和门控表，没有面板、没有滑块、没有控制台。

## 11. 待 Boss 知悉的默认处理（不阻塞实现）

- 🎯 TSM 徽章写「百分比折算（构造性）」而不是「G1 通过」。理由：数值由库内营收乘百分比得出，对账不独立。
- 🎯 页脚声明与 SKILL 禁止事项改写：参考情景和权重视为台账里你签过字的假设。
- 🔧 TSM 的 `2018–19` 回放按钮按现窗口会禁用。要启用需收窄窗口，属台账决策。
- 🔧 Custom 的 P/E 半宽初值为 0，点参考情景时继承该情景的半宽，可在 `#in-pe-half` 手改。