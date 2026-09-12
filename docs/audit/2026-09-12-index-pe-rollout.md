# 三指数 PE 上线前记录

Boss于2026-09-12批准合并、push和晨报上线。主线起点4bb2906，验收分支3db5a9b（69个提交）。新数据先在当日生产副本演练，再在共享写锁、SQLite事务与备份下推广；不覆盖整库、不发额外Telegram测试消息。

## 合并提交范围（审批时）

```text
ad85659 docs(valuation): plan SOXX historical TTM PE proxy
ece3b58 docs(valuation): harden SOXX plan against mcap contamination
015b3cd test(fmp): freeze SOXX disclosure FX and split contracts
87aee12 feat(fmp): add disclosure FX and split endpoints
81fbb36 feat(store): add auditable historical basket valuation tables
7fab8e0 feat(valuation): normalize SOXX disclosure history
17506ec feat(valuation): gate historical market cap anomalies
6e82e99 feat(valuation): compute as-of SOXX GAAP TTM proxy
98856f1 feat(valuation): orchestrate idempotent SOXX history backfill
e9fbb5e feat(valuation): query and export basket TTM PE history
9560a64 feat(valuation): verify SOXX historical PE read-only
cd398c1 docs(valuation): document SOXX historical TTM PE pipeline
d1f25b3 fix(valuation): use FMP disclosure identity for CREE alias
44990ff fix(valuation): harden source identity and audit gates
ded698b docs(valuation): record SOXX read-only dry-run
1fb42ff fix(valuation): verify persisted SOXX evidence
d47f6a9 docs(valuation): record final audit boundaries
4b96d47 fix(valuation): close SOXX audit safety gaps
a82a446 docs(valuation): plan three-index weekly PE morning chart
1029910 docs(valuation): revise three-index PE plan per CC audit (R1-R7)
eb75969 Merge branch 'main' into codex/index-pe-morning-chart
f103497 Merge branch 'codex/soxx-historical-ttm-pe' into codex/index-pe-morning-chart
aae1576 docs(valuation): freeze post-merge test baseline
c17a4aa fix(valuation): fail-closed empty refresh window replacement
d90377d refactor(valuation): generalize historical basket disclosures
30ae539 docs(valuation): correct Task 1 stale file refs to post-merge reality
fc69283 fix(valuation): correct SOXX history boundary to 2021-09-20
bc34812 feat(store): add weekly basket PE history contract
0ec48a8 fix(store): reject cross-version overwrite of any existing weekly PE row
2172a55 feat(valuation): compute hindsight NTM basket earnings
a89813a fix(valuation): anchor hindsight window and narrow member guards
6f4bbda fix(valuation): share one aggregate PE kernel and fix mixed-tz acceptance
62b27af feat(valuation): declare a market-cap convention per share-class group
9255de1 feat(store): add an append-only backfill run manifest with repair hashes
12b889b refactor(valuation): thread basket config through the backfill stages
d82443a feat(valuation): backfill three-index weekly PE history
7008568 fix(valuation): close three fail-open gaps in the weekly PE backfill
0cd1913 fix(valuation): close historical dual-class gap structurally
46b6b4d docs(valuation): record Boss stage-1 review rulings in contracts
2956b8e fix(valuation): close five information-crossing and evidence gaps
2af463c fix(valuation): compare the frozen week set in both directions
0b9b9c1 fix(valuation): validate the frozen manifest instead of displaying it
f774779 feat(store): bind published rows to the run that wrote them
628c49d docs(valuation): pin full-rewrite certification contract pending Task 5 ruling
34e6742 fix(valuation): a run declares one outcome, once, at the end
a36fac0 fix(valuation): require terminal events to land in run order
94fd03e docs(valuation): C1 ruled — full-window rewrite with in-transaction pruning
f55a927 docs(research): five-year dual-class mcap band study — 1% band validated
2b51195 fix(valuation): close pre-C1 verification and schema gaps
2b54bd2 fix(valuation): publish verified weekly windows atomically
87f16a0 feat(valuation): compute and certify six-basket PIT consensus PE
96416f9 feat(morning): render and embed three-index weekly valuation chart
9fb7634 merge: align index PE pipeline with current Finance main
89beeb2 fix(valuation): bound bootstrap requests and disclosure window
9dae7ac docs(valuation): record local acceptance and bounded cloud trial
6a34b02 fix(valuation): isolate legacy PE guard and preserve failure diagnostics
83e074c docs(valuation): record isolated trial identity blockers and repair plan
dc61062 fix(valuation): separate filer and issuer identity with source evidence gates
26cec1b fix(valuation): withhold unverified Fox and News share-class caps
e249a69 docs(valuation): record issuer repair acceptance and remaining evidence gaps
78411b9 data(valuation): add reviewed issuer evidence for twelve historical securities
ebc5b78 docs(valuation): record issuer evidence progress and remaining identity contract decision
c7595c6 fix(valuation): resolve verified issuer keys without mandatory LEI
a85d883 data(valuation): close historical issuer gaps with reviewed SEC identities
d7110cd fix(valuation): quarantine invalid historical caps without aborting basket
641c29d docs(valuation): record verified issuer closure and partial offline PE results
803ba91 fix(valuation): contain invalid vendor market-cap ranges
93c8a6f fix(valuation): bind reviewed security aliases to financial issuers
3db5a9b docs(valuation): certify full weekly history and nine PIT vintages
```

## 原主目录 issue 文本的无损留档

原主目录以下两份未追踪文件与分支同名但内容不同。为保留原始过程和未解决的非PE问题，合并前完整保存于此；正文是历史记录，不代表本轮重新验证其中每一个市场数字或链接。

### 原 issue035（主目录未追踪版本）

# Issue 035: KLAC 10:1 拆股跨表调整不一致（daily_price 未回溯 / market_cap 股数未更新 / income 残留行）

- **日期**: 2026-06-30（发现于 AMAT/LRCX/GLW/KLAC 四票 forward PE + 业绩增长分析）
- **状态**: OPEN（市值表/价格表为 market.db 云端独占写入，本地不改，待云端侧修复 + 重拉）
- **影响范围**: market.db 中 KLAC 的 `daily_price` / `historical_market_cap` / `income_quarterly` 三表；任何跨 2026-06-12 的 KLAC 价格级滚动指标（晨报 PMARP / 量能异常 / β6M / EMA / RVOL）；KLAC 市值口径

## 背景

KLA Corporation（KLAC）于 **2026-05 公告、2026-06-12 生效 10-for-1 forward split**。FMP / yfinance 对拆股的回溯调整在不同表里**步调不一致**，导致同一只票在不同表呈现不同的 share basis。当前价（$248.64）与当前 TTM EPS（$3.53，拆股后口径）都是拆股后基准，**所以现价 PE 计算正确（TTM 70.4x / fwd 49x）**，但跨拆股的历史序列全部失真。

## 现象（三处不一致）

**1. `daily_price` 未做拆股回溯调整 —— 绝对价在 2026-06-12 有 ~10x 断崖**

```
2026-06-11  close 2411.64   (拆股前绝对价)
2026-06-12  close  254.54   (拆股后绝对价)   ← ~10x 断点
```

`change_pct` 看起来是拆股感知的（记录 +7% 而非 -89% 裸跌），但**收盘价绝对值未回溯**：6-12 之前是 ~$1900-2400，之后是 ~$250。任何基于价格水平的滚动计算（PMARP 百分位、EMA120、β 若用价位回归、RVOL 若 volume 也未 ×10）跨 6-12 全部被污染。KLAC `in_pool=1`（Technology，核心池）→ 进晨报三件套，6-12 之后的指标不可信。

**2. `historical_market_cap` 全程钉死 ~130.9M 股（拆股前股数）**

`market_cap = 固定 130.9M × daily close`：
- 拆股前（≤06-11）：130.9M × ~$1921 = ~$251B ✓ 正确（KLAC 真实市值）
- 拆股后（06-12 ~ 06-23，约 8 个交易日）：130.9M × ~$248 = **~$32B ✗（10x 偏低）**，真实应 ~$320-327B（web 确认 $323-339B）

即拆股后股数没跟着 ×10。最新行 2026-06-23 = $32.0B 是错的。

**3. `income_quarterly` 已回溯到拆股后口径，但最老一行残留未调整**

近 8 季全是拆股后（~1320M 股 / EPS ~$0.6-0.9），唯独窗口最老的 **2024-03-31 Q3 残留拆股前**（EPS $4.43 / 135.9M 股）。它不在常用的 8 季窗口内，但会当 Q3'25 的同比基数 → 算出 -82% 垃圾 YoY（真实应 +81%）。

## 根因（推测）

FMP/yfinance 对 split 的回溯调整按表/按拉取批次进行，调整未原子化：
- `income_quarterly` 在拆股后重拉时整体回溯了（漏了窗口边缘最老一行）
- `daily_price` 存的是**当日实际成交价**，历史段未回溯（或回溯任务未对 KLAC 触发）
- `historical_market_cap` 的股数字段是某次快照的常量，拆股后未刷新

## 规避 / 修复

**分析侧（已用）**：YoY 基数改用「按股数归一化」EPS——`eps_norm = eps × 该季股数 / 最新股数`，自动把残留行 /10 修正，对正常行 ≈ ×1 无害。现价 PE 不受影响（现价与现 EPS 同为拆股后）。

**数据侧（云端待修）**：
1. `daily_price`：重拉 KLAC 全历史的 split-adjusted close + volume（或对 ≤2026-06-11 的 close ÷10、volume ×10）
2. `historical_market_cap`：拆股后股数刷新为 ~1309M（或重拉），修 06-12~06-23 段
3. `income_quarterly`：补调 2024-03-31 行（EPS ÷10、股数 ×10）
4. 加一道 split 守卫：daily_price 单日 |change| 与 close 跳变背离（如 change_pct≈+7% 但 close 跳 -89%）时告警，提示未回溯拆股

**验证**：修复后 `market_cap / close` 全程应得稳定股数；`daily_price` 跨 6-12 无 10x 跳变；KLAC 晨报指标重算。

## 关联

- Issue 019 / 033（worktree 空 market.db 影子）—— 同为 market.db 数据完整性类
- `docs/issues/036`（GLW GAAP vs core 口径）—— 同批分析发现的跨源口径问题
- 9b9461a `fix(indicators): compute_beta 剔除非正价格坏数据行` —— 价格脏数据影响指标的同类先例

### 原 issue037（主目录未追踪版本）

# Issue 037: 【误报 / 已解决】MU 最近两季"物理不可能"的财务——基于陈旧认知误判，实为 SEC 核实的真实超级周期数字

- **日期**: 2026-06-30（发现于内存上游 AMAT/KLAC/LRCX 设备产业链报告的 ground-truth 核对）
- **状态**: **RESOLVED — FALSE ALARM**（数据无误；本条保留为过程教训）
- **影响范围**: 无（market.db MU 数据正确）。本条记录的是**分析者认知层面的踩坑**，不是数据 bug

## 一句话

我一度判定 MU `income_quarterly` 最近两季（FY2026 Q2/Q3）"损坏、物理不可能"，并据此起草了数据污染 issue + 在 data_context 打了 ⛔ 警告。**联网核实 SEC 8-K + Micron 官方新闻稿后证明：那些数字逐行真实**——是 AI-HBM 超级周期下纯涨价驱动的真实井喷。错的是我的陈旧认知，不是数据。

## 我当时为什么误判（错误链条）

DB 显示 MU FY26 Q3（2026-05-28）：营收 $41.46B、毛利率 84.6%、净利 $28.24B、EPS $24.67、股价 $1,145；且 COGS 被"钉死"在 ~$6B 不随营收（13.6→23.9→41.5B）增长、`depreciation_and_amortization` 字段为 -$21.18B。

我的（错误）推理：
1. "DRAM/NAND 厂毛利率不可能 84.6%，2018 顶点也就 ~61%" ← **锚定了 2025 年及更早的陈旧毛利率认知**
2. "MU 单季 $41B 营收 = 年化 $166B，比肩台积电，不可能" ← **锚定了陈旧的营收量级**
3. "$1,145/股 × 11.45 亿股 = $1.31T 市值，存储厂不可能" ← **锚定了陈旧的股价/市值**
4. "COGS 钉死 + D&A 负值 = 源数据坏掉铁证"

## 真相（2026-06-30 联网核实）

- **SEC 8-K（EDGAR）+ Micron 官方 GlobeNewswire 新闻稿 + CNBC + StockTitan** 一致：FQ3 2026 营收 **$41.46B**、GAAP 净利 **$28.24B / $24.67 稀释 EPS**、毛利率 **84.9%（公司纪录）**、Q4 指引 $50B / GM ~86%。FQ2 同样逐行吻合（$23.86B / 74% GM / $12.08 EPS）。
- **股价**：MU ~$1,133–1,213（Macrotrends 52 周区间 ~$103→$1,200+），6/22 与 Anthropic 战略协议 + 6/24 blowout 财报催化。$1,145 真实。
- **84.9% 毛利率的物理解释**：DRAM 合约价单季 **+90-95% QoQ**（TrendForce）。营收三倍来自**纯涨价**而非放量 → COGS（多为固定折旧）基本不动维持 ~$6B → 毛利率机械地冲到 85%。我当成"造假铁证"的"COGS 钉死"，恰恰是纯涨价超级周期的**正确特征**。`D&A` 字段负值是 FMP 该行的符号/重分类怪异，不影响利润表三大指标（已与 SEC 逐项核对无误）。

## 教训（真正的踩坑）

1. **MEMORY 反模式"评估数据质量前必须先查 ground truth / 训练知识里的股价财务全部视作过期"——这次我险些反向踩中**：用陈旧 prior 把真实数据判成失真。该反模式的正确读法是**双向**的：既不能轻信 DB 也不能轻信记忆，唯一裁判是**当前 primary source**（SEC/公司 IR）。
2. **物理一致性论证有边界**：它能否定"内部自相矛盾"（如负折旧），但**不能否定"超出我记忆量级"的真实极端值**。84.9% GM 看似违反"制造业物理"，实为价格冲击下的真实结果。下"物理不可能"结论前，必须先用 primary source 校准量级基线。
3. **两个 subagent（数据 agent + R3）都说"真实"时我仍坚持己见**——怀疑精神可贵，但应导向**独立验证**（我最终直接 WebSearch SEC）而非**固执于 prior**。怀疑的出口是查证，不是否决。
4. 流程上做对的一点：**起草了 issue 但没急着 commit / 没据此删 MU 数据**，先验证后定论，避免了把错误固化进代码库。

## 关联

- `docs/issues/035`（KLAC 拆股跨表失真，**真实** data issue）/ `036`（GLW 口径，真实）—— 同批分析（2026-06-30）的另两条，那两条是真 bug，本条是误报
- MEMORY: `feedback_verify_ground_truth_before_quality_judgment` —— 本条是该反模式的"反向"实战案例，建议在该卡补一句"prior 也是过期数据，量级判断同样需 primary source 校准"
- 报告产物：`reports/semicap-upstream-2026-06/`（MU 8 季表已全部启用）

