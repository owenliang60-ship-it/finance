# SPY / QQQ / SOXX TTM + NTM P/E 晨报项目 — CC 审核交接

> 日期：2026-07-30  
> 主线：`main@8ca3da9`  
> 三指数计划：`codex/index-pe-morning-chart@a82a446`  
> SOXX 历史前置：`codex/soxx-historical-ttm-pe@4b96d47`  
> 状态：FMP Phase 1 数据底座 LIVE；SOXX 历史引擎已实现但未合并；三指数设计已批准、Task 0–9 尚未执行；未进入生产晨报  
> 边界：本文只供独立审核，不授权实现、merge、push、写生产 DB 或修改 cron。

## 0. 希望 CC 输出什么

请把这次工作当作金融方法论、数据管线和实施计划审核，不要只确认测试或 PNG 是否存在。

请按以下格式输出：

1. 具体做得好的地方；
2. 问题按 P0 / P1 / P2 排序，每条给 `file:line`；
3. 说明触发条件、错误结果和修复方向；
4. 单独回答第 8 节的问题；
5. 判断原计划应该原样执行、先修订再执行，还是拆阶段；
6. 明确结论：
   - `Ready to execute: Yes`
   - `Ready to execute: With plan fixes`
   - `Ready to execute: No`

不要把以下事实当成“已经实现”：

- `fmp_basket_valuation` 已建表；
- 临时环境曾算出三指数数值；
- worktree 中存在 PNG；
- SOXX 单指数 dry-run 通过；
- 晨报已有一个标题为 “SPY / QQQ / SOXX” 的择时表。

---

## 1. 一页结论

### 1.1 当前真实进度

最准确的描述是：

> **FMP forward 原始数据已周频上线；SOXX 历史 TTM 前置引擎在孤立分支完成；三指数产品设计和计划已批准；通用历史回填、指数 NTM 聚合、图表 producer、晨报接入和生产验收均未开始。**

按正式计划衡量：

| 环节 | 状态 |
|---|---|
| FMP estimates / earnings / ETF holdings | `LIVE` |
| SOXX historical TTM engine | 已实现、已审计、未合并 |
| 三指数设计 | Boss 已批准 |
| 三指数实施 Task 0–9 | `0/10` |
| `fmp_basket_valuation` | 表存在、`0 rows` |
| 历史 basket valuation 表 | 生产不存在 |
| 三指数正式 renderer | 不存在 |
| 晨报 PE section | 不存在 |
| 云端指数估值 cron | 不存在 |
| 生产状态 | `NOT LIVE` |

### 1.2 混乱来自哪里

1. Phase 1 项目名里有“估值”，但 Phase 1 实际只负责数据层；指数 P/E 聚合属于 Phase 2。
2. 晨报现有 “SPY / QQQ / SOXX” 表展示 PMARP/S2 择时，不是 PE。
3. SOXX 历史 PE 确实完成了，但只在未推送的本地分支。
4. 三指数 PNG 是一次性研究工件，没有已提交 producer、SSOT、verifier 或晨报 consumer。
5. main 的 `.claude/ongoing.md` 仍写“Phase 2 尚未立项”，但另一个 worktree 已有 Boss 批准的完整计划，tracker 已发生漂移。

### 1.3 审核前必须解决的口径冲突

SOXX 分支与三指数设计的“主 PE”不是同一指标：

| 口径 | 公式 | 当前 SOXX 约值 | 原定位 |
|---|---|---:|---|
| ETF 持仓权重口径 | `1 / Σ(w × NI/mcap)` | 51.07x | SOXX 分支主 PE |
| 全市值合并口径 | `Σmcap / ΣNI` | 39–41x | SOXX 分支次级；三指数设计统一口径 |

两者都合法，但回答的问题不同。计划的 `ttm_pe_gaap` 只保留一个字段，却同时声称复用 SOXX 已审计层，容易让实现者不知应该复用证据还是复用主指标。

这不是显示层小问题：它会让同一天 SOXX 显示 51x 或 39x，并改变历史 percentile。

---

## 2. 当前资产地图

### 2.1 main 与生产

```text
worktree: /Users/owen/CC workspace/Finance
branch:   main
HEAD:     8ca3da9
remote:   origin/main == 8ca3da9
cloud:    main@8ca3da9
```

main 已包含：

- FMP forward Phase 1；
- 周频 estimates / earnings / holdings；
- run manifest 与只读 verifier；
- 晨报 HTML/PDF/PNG delivery；
- 2026-07-30 新增的 `0b. 成交集中度`。

main 不包含：

- SOXX historical TTM producer；
- 三指数 historical producer；
- forward basket valuation producer；
- 三指数 renderer；
- 晨报估值 section。

### 2.2 三指数计划 worktree

```text
path:    /Users/owen/CC workspace/Finance/.worktrees/index-pe-morning-chart
branch:  codex/index-pe-morning-chart
HEAD:    a82a446
base:    f0f30f4
vs main: 14 behind / 1 ahead
remote:  none
```

唯一独有 commit：

```text
a82a446 docs(valuation): plan three-index weekly PE morning chart
```

tracked diff 只有：

```text
docs/plans/2026-07-19-index-pe-morning-chart-design.md
docs/plans/2026-07-19-index-pe-morning-chart.md
```

合计 `+781` 行，无代码、无测试。

未跟踪一次性工件：

```text
reports/valuation/index_pe_ttm_ntm_weekly_20260730.png
reports/valuation/index_pe_ttm_ntm_weekly_20260730.csv
reports/valuation/index_pe_ttm_ntm_weekly_20260730.json
```

### 2.3 SOXX historical worktree

```text
path:    /Users/owen/CC workspace/Finance/.worktrees/soxx-historical-ttm-pe
branch:  codex/soxx-historical-ttm-pe
HEAD:    4b96d47
base:    db86d75
vs main: 16 behind / 18 ahead
remote:  none
status:  clean
diff:    38 files, +7,829/-3
```

已实现：

- disclosure / FX / split endpoints；
- 历史持仓、日期语义和身份规范化；
- TERN→TER authoritative identity correction；
- CREE→WOLF whole-key fallback；
- KLAC / MCHP HMC 污染检测、forced refresh、quarantine；
- PIT GAAP TTM；
- auditable tables 与窄 CRUD；
- backfill / query / read-only verifier；
- 日频和周频 SOXX PNG；
- 多轮 review 修复。

未完成生产动作：

- 未 merge；
- 未 push；
- 未部署；
- 未写生产估值表；
- 未接 cron；
- 未接晨报。

核心审计文档：

```text
/Users/owen/CC workspace/Finance/.worktrees/soxx-historical-ttm-pe/
  docs/audit/2026-07-14-soxx-historical-ttm-pe-review-handoff.md
```

### 2.4 当前数据库资产

最新 Phase 1 数据：

| 项目 | 值 |
|---|---:|
| snapshot | 2026-07-25 |
| run | weekly / complete |
| target universe | 1,068 |
| quarter success | 1,009 |
| failure count | 59 |
| 最新 estimate rows | 17,590 |
| 最新 estimate symbols | 1,043 |
| 最新价格 | 2026-07-29 |
| 最新 HMC | 2026-07-28 |

正式 weekly snapshots：

```text
2026-07-13
2026-07-18
2026-07-25
```

最新 holdings：

| basket | raw | included |
|---|---:|---:|
| SPY | 505 | 501 |
| QQQ | 108 | 101 |
| SOX | 33 | 30 |
| IGV | 110 | 107 |
| XLF | 80 | 77 |

MAGS 使用静态配置。

估值输出：

```text
fmp_basket_valuation exists, rows = 0
```

生产不存在：

```text
basket_ttm_valuation
basket_weekly_pe_history
fmp_fund_disclosure_holdings
fx_daily
```

### 2.5 当前晨报与 cron

生产任务：

```text
08:00 Tue–Sat  finance_market_report
10:45 Sat      finance_forward（market_db_writer lock）
```

当前 forward 顺序：

```text
yfinance forward
→ FMP Phase 1 ingestion
→ Phase 1 verifier
```

没有 basket valuation / history refresh / index chart step。

2026-07-30 晨报于 08:05 成功发送 `morning_report_2026-07-29.html`，内容有：

- `0. 大盘择时因子`；
- `0b. 成交集中度`；
- PMARP；
- 量能异常；
- Dollar Volume。

不含 TTM、NTM、P/E 或估值图。

现有三指数表的列是：

```text
指数 / PMARP / PMARP 2%上穿 / S2参与度 / S2触发
```

此外，`terminal/morning_html_report.py` 目前只支持 heading、subtitle、alerts 和 table，没有 typed image block 或 data URI。

---

## 3. 一次性研究图的边界

as-of `2026-07-29`、estimate snapshot `2026-07-25`：

| 指数 | TTM | NTM | NTM mcap coverage |
|---|---:|---:|---:|
| SPY | 26.2954x | 20.0689x | 99.83% |
| QQQ | 30.3966x | 23.4160x | 100.00% |
| SOXX | 39.2424x | 18.6180x | 100.00% |

它能证明：

- 当前 NTM 数据覆盖足够；
- 三面板图视觉上可行；
- 研究环境能拼出五年周频序列。

它不能证明：

- 数据来自正式 SSOT；
- 历史 TTM 完全符合 disclosure/PIT/sanity 契约；
- producer 可在另一台机器确定性重建；
- verifier 通过；
- 晨报能自动生成和降级；
- 生产 writer 失败安全。

目录只有 PNG / CSV / JSON，没有已提交的 source manifest、methodology version、producer、query、verifier 或 tests。因此它只能作为视觉和可行性参考，不能作为正式 acceptance fixture。

---

## 4. 已批准的产品设计

设计：

```text
docs/plans/2026-07-19-index-pe-morning-chart-design.md
状态：Boss 已批准（2026-07-19）
```

### 4.1 产品形态

每日晨报加入一张 1800px 自包含 PNG：

- SPY / QQQ / SOXX 三个纵向 panel；
- 五年周频；
- 共享横轴；
- 不平滑；
- 不跨 gap 连线；
- 最新值、五年 percentile、coverage、as-of 可读。

三条序列：

1. **GAAP TTM P/E**  
   估值日市值 ÷ 当时已经公开的最近四个连续季度 GAAP NI；硬门控 `accepted_date <= valuation_date`。

2. **后视镜 NTM P/E**  
   估值日市值 ÷ 估值日之后四个实际财政季度 GAAP NI；明确 ex-post，不得冒充历史信号。

3. **真实 PIT NTM P/E**  
   使用每个 FMP weekly snapshot 当时可见的分析师共识；只从 2026-07-13 开始，不向过去补造。

### 4.2 尾部语义

最近不足四个未来实际季度时，设计允许：

```text
已有 actual + 最新 consensus
quality_tier = latest_consensus_tail
```

图上以紫色虚线表示。

这段既不是纯 hindsight actual，也不是 valuation-date PIT。是否进入历史 percentile、后续 actual 到齐后如何覆写，是本轮必须审核的点。

### 4.3 已冻结的共同门槛

- 亏损成员保留；
- numerator / denominator 使用同一 covered set；
- denominator `<=0` 不发布；
- mcap coverage `<90%` 不发布；
- HMC 最大 staleness 7 天；
- coverage 不能替代 jump sanity；
- gap 不 forward-fill；
- SOXX 2021-09 前保留空白；
- SPY/QQQ disclosure 只是 ETF proxy；
- live snapshot 是较弱 tail evidence；
- verifier 用 SQLite `mode=ro` 重算源证据。

### 4.4 存储边界

| 表 | 责任 |
|---|---|
| `basket_ttm_valuation` | 日频历史 TTM 与成员证据 |
| `basket_weekly_pe_history` | 三指数周频 TTM + hindsight 图表 SSOT |
| `fmp_basket_valuation` | 真实 PIT forward SSOT |

计划要求 narrow CRUD、whole-batch transaction、methodology version 防静默覆盖。

### 4.5 晨报边界

- 晨报只读 DB；
- 晨报不触网；
- 晨报不写估值表；
- PNG 以 data URI 内嵌 HTML；
- HTML 失败进入 PDF fallback；
- 不额外发送 Telegram photo；
- stale/unavailable 不阻断其他晨报；
- writer 继续使用 `market_db_writer` lock。

---

## 5. Task 0–9 计划与状态

完整计划：

```text
docs/plans/2026-07-19-index-pe-morning-chart.md
```

| Task | 原计划 | 当前状态 | 关键漂移/审核点 |
|---|---|---|---|
| 0 | 合并 SOXX historical 证据层 | 未开始 | 计划写 15 commits，实际 18；分支落后 main 16 commits；FMP/store 有重叠修改 |
| 1 | 泛化为 SPY/QQQ/SOXX composition pipeline | 未开始 | config、normalizer、tests 均不存在；SPY/QQQ disclosure 代理语义需验证 |
| 2 | 新增 `basket_weekly_pe_history` 与窄 CRUD | 未开始 | 表和 CRUD 均不存在；method version 与可变 tail 的关系需审核 |
| 3 | hindsight NTM actual/estimate engine | 未开始 | 模块和 tests 不存在；tail 并非 PIT，也非纯 actual |
| 4 | 三指数五年 backfill + read-only verifier | 未开始 | producer/verifier 不存在；SOXX gap、HMC refresh、manifest 独立性是核心 |
| 5 | FMP Phase 2 六 basket PIT 聚合 | 未开始 | `fmp_basket_valuation=0`；`terminal/forward_valuation.py` 不存在 |
| 6 | 三面板 Pillow renderer | 未开始 | 正式 renderer 不存在；当前 PNG 只是 research artifact |
| 7 | HTML data URI + PDF fallback | 未开始 | HTML renderer 尚不支持 image；原插入位置被新 `0b` section 占用 |
| 8 | weekly operations / stale / docs | 未开始 | forward cron 已约 101 分钟；应继续共用 writer lock但需重新测 SLO |
| 9 | 全量 gates / review / Boss 停点 | 未开始 | 专项测试不存在；原 full-suite baseline 已过期 |

计划中的主要缺失文件：

```text
config/index_pe_baskets.json
terminal/hindsight_ntm_valuation.py
terminal/forward_valuation.py
terminal/index_valuation_chart.py
scripts/backfill_index_pe_history.py
scripts/verify_index_pe_history.py
tests/test_index_pe_basket_config.py
tests/test_index_holdings_normalizer.py
tests/test_market_store_basket_weekly_pe.py
tests/test_hindsight_ntm_valuation.py
tests/test_backfill_index_pe_history.py
tests/test_verify_index_pe_history.py
tests/test_forward_valuation.py
tests/test_index_valuation_chart.py
```

### 5.1 Task 0 的真实 merge 风险

SOXX 分支与 main 都修改：

```text
src/data/fmp_client.py
src/data/fmp_forward_ingestion.py
src/data/market_store.py
ARCHITECTURE.md
CLAUDE.md
```

不能把 Task 0 当成纯机械 merge。必须保留：

- main 的 FMP Phase 1 ingestion / manifest / verifier 契约；
- SOXX 的 disclosure / FX / split / historical store 契约；
- 两侧全部测试。

### 5.2 Task 7 的产品漂移

原计划写：

```text
大盘择时 → 三指数估值 → PMARP
```

main 现有：

```text
0. 大盘择时 → 0b. 成交集中度 → 1. PMARP
```

实施前应冻结：

```text
方案 A: 0 大盘择时 → 0a 三指数估值 → 0b 成交集中度 → 1 PMARP
方案 B: 0 大盘择时 → 0b 成交集中度 → 0c 三指数估值 → 1 PMARP
```

### 5.3 Task 9 的基线漂移

原计划记录的是 2026-07-19 的全量测试环境。此后 main 新增了大量晨报代码和测试。执行前必须重新冻结：

- current main 全量基线；
- current morning-report targeted suite；
- Python 3.10 baseline；
- current DB schema；
- pre-existing failures。

---

## 6. 已发现的风险

### P0 候选：主 PE 口径未统一

详见第 1.3 节。需要明确：

- schema 是否同时保存 holding-weighted 和 aggregate-mcap；
- 晨报主图展示哪条；
- percentile 用哪条；
- 三指数横向比较是否必须同口径。

### P1 候选：两个分支均明显落后 main

```text
index plan branch: 14 behind
SOXX branch:       16 behind
```

应先做语义 merge/rebase 审计，再开始 TDD tasks。

### P1 候选：hindsight consensus tail 可能误导

需要明确：

- 名称是否应改为 `hybrid hindsight tail`；
- 是否计入 percentile；
- actual 到齐后能否改写旧行；
- methodology version 如何记录。

### P1 候选：verifier 独立性不足

SOXX audit 已承认 producer/verifier 共享纯计算内核，且 repair provenance 没有 immutable manifest。一次性研究可以接受；升级为 recurring weekly product 后需判断是否足够。

### P1 候选：historical composition 只是 ETF proxy

需要验证：

- SPY/QQQ 五年 disclosure coverage；
- available/effective date；
- off-cycle changes；
- stale composition；
- live-tail interval；
- cash、derivative、share-class rows。

### P1 候选：tracker 与真实状态冲突

main `.claude/ongoing.md` 写“Phase 2 尚未立项”，而本分支设计已批准。计划修订后应把 tracker 改成：

```text
规划完成 / 待执行 / 非 LIVE
```

### P2 候选：一次性工件缺 provenance

不应直接提交为 golden fixture。正式 producer 完成后应重建并解释差异。

### P2 候选：cron SLO 需重测

当前 forward natural run 约 101 分钟。新增 valuation/history/verifier 应放在同一 writer lock 内，但需按 step 记录耗时和失败语义。

---

## 7. 建议的最小恢复顺序

这不是替代原计划，只是依赖排序建议：

### Stage A：先修计划，不写生产

1. 决定 holding-weighted vs aggregate-mcap；
2. 两个 feature branch 对齐 current main；
3. 更新 Task 0 commit count、测试基线和晨报 section 顺序；
4. 明确 verifier manifest 与 hindsight tail overwrite 契约。

### Stage B：先建数据产品

5. 执行 Task 1–4；
6. 用临时 DB 生成正式三指数 TTM+hindsight；
7. 与一次性图比较并解释差异，不要求机械相等。

### Stage C：接真实 PIT

8. 执行 Task 5；
9. 聚合 2026-07-13 / 07-18 / 07-25 snapshots；
10. 六 basket verifier 通过，晨报只消费三指数。

### Stage D：产品与生产

11. 执行 Task 6–9；
12. 本地 PNG + HTML + PDF 目视验收；
13. Boss 单独批准 cloud backfill、merge、push、deploy；
14. 等自然 08:00 晨报验收；
15. tracker 更新为 LIVE。

---

## 8. 请 CC 必须回答的问题

### 金融方法论

1. 三指数主指标应选：
   - holding-weighted earnings-yield reciprocal；
   - aggregate full-market-cap P/E；
   - 两者都保留？
2. 若两者都保留，哪条进主图，哪条只诊断？
3. 亏损成员保留时，除 denominator `<=0` 外，是否需要 near-zero sensitivity warning？
4. SPY/QQQ 是否必须在标题写 `proxy`，避免误称官方历史 P/E？
5. hindsight consensus tail 是否进入五年 percentile？
6. PIT NTM 目前只有三个周点，如何展示最诚实？

### 数据与 PIT

7. `accepted_date <= valuation_date` 是否足够处理 restatement vintage？
8. hindsight actual 不做 accepted-date gate 是否符合明确 ex-post 定位？
9. actual + estimate 的 fiscal-quarter 去重规则是否足够？
10. composition available/effective date 对 SPY/QQQ 是否可行？
11. 7 天 HMC + 90% coverage + jump sanity 是否足够？
12. recurring product 是否必须新增 immutable repair/run manifest？

### 架构与范围

13. SOXX branch 应先独立 merge main，还是只 merge 到三指数 feature branch？
14. 三张估值表的边界是否合理，还是重复过多？
15. methodology version 如何支持 tail 从 estimate 变 actual？
16. verifier 与 producer 共享计算内核是否可接受？
17. HTML typed image + data URI 是否是最小实现？
18. 三指数估值应放在 `0b 成交集中度` 前还是后？
19. 101 分钟 forward cron 加步骤后，是否继续单 wrapper、分 step 日志？
20. 原计划是否过大，是否应拆成：

```text
Phase 2A: 当前三指数 PIT NTM + 当前值
Phase 2B: 五年 TTM + hindsight
Phase 2C: 图表 + recurring operations
```

请从最小交付、数据风险和 review 难度给出明确建议。

---

## 9. 审核导航与命令

### 9.1 重点文件

三指数设计与计划：

```text
docs/plans/2026-07-19-index-pe-morning-chart-design.md
docs/plans/2026-07-19-index-pe-morning-chart.md
```

Phase 1：

```text
docs/design/2026-07-09-fmp-forward-eps-valuation-spec.md
docs/plans/2026-07-11-fmp-forward-eps-data-layer.md
docs/audit/2026-07-13-fmp-forward-eps-phase1-rollout.md
scripts/update_fmp_forward.py
scripts/verify_fmp_forward.py
scripts/run_forward_data.sh
src/data/fmp_forward_ingestion.py
src/data/market_store.py
```

SOXX worktree：

```text
/Users/owen/CC workspace/Finance/.worktrees/soxx-historical-ttm-pe
```

重点：

```text
docs/audit/2026-07-14-soxx-historical-ttm-pe-review-handoff.md
docs/plans/2026-07-14-soxx-historical-ttm-pe.md
docs/research/2026-07-14-soxx-historical-ttm-pe-dry-run.md
scripts/backfill_soxx_historical_pe.py
scripts/query_basket_ttm_pe.py
scripts/verify_basket_ttm_pe.py
terminal/historical_basket_valuation.py
terminal/historical_market_cap_sanity.py
```

晨报：

```text
scripts/morning_report.py
terminal/morning_html_report.py
scripts/run_market_report_pipeline.sh
tests/test_morning_report.py
tests/test_morning_html_report.py
tests/test_telegram_routing.py
```

### 9.2 分支核验

```bash
cd "/Users/owen/CC workspace/Finance"

git worktree list --porcelain
git rev-list --left-right --count main...codex/index-pe-morning-chart
git rev-list --left-right --count main...codex/soxx-historical-ttm-pe
git log --oneline main..codex/index-pe-morning-chart
git log --oneline main..codex/soxx-historical-ttm-pe
```

### 9.3 三指数 worktree

```bash
cd "/Users/owen/CC workspace/Finance/.worktrees/index-pe-morning-chart"

git status --short
git diff --stat main...HEAD
git show --stat a82a446
```

预期：

- tracked implementation 仍为零；
- `reports/valuation/` 为 untracked；
- 本交接文档为新增 untracked audit doc。

### 9.4 本地 DB 只读核验

```bash
cd "/Users/owen/CC workspace/Finance"

sqlite3 -header -column data/market.db "
SELECT snapshot_date, run_kind, status, target_count,
       quarter_success, quarter_failure_count
FROM fmp_forward_runs
ORDER BY snapshot_date DESC, rowid DESC
LIMIT 5;

SELECT COUNT(*) AS basket_valuation_rows
FROM fmp_basket_valuation;

SELECT MAX(date) AS latest_price FROM daily_price;
SELECT MAX(date) AS latest_hmc FROM historical_market_cap;
"
```

### 9.5 主线缺口搜索

```bash
cd "/Users/owen/CC workspace/Finance"

rg -n \
  "basket_weekly_pe_history|hindsight_ntm|index_valuation_chart|fwd_pe_ntm" \
  scripts terminal src tests ARCHITECTURE.md
```

预期：

- 能找到 `fmp_basket_valuation` schema/CRUD；
- 找不到三指数 producer/renderer；
- architecture 明写 Phase 2 才写估值。

### 9.6 云端只读核验

```bash
ssh aliyun '
  cd /root/workspace/Finance
  git branch --show-current
  git rev-parse --short HEAD
  crontab -l | grep -E "finance_forward|finance_market_report"
'
```

本轮禁止：

- backfill；
- 写 `market.db`；
- 改 crontab；
- merge；
- push；
- 将生产 checkout 到 feature branch。

---

## 10. 当前验收清单

- [ ] 正式 producer 生成 SPY/QQQ/SOXX 五年周频 PNG；
- [ ] 明确 holding-weighted / aggregate-mcap 主次；
- [ ] 三条线区分 TTM / 后视镜 NTM / PIT NTM；
- [ ] PIT NTM 不早于 2026-07-13；
- [ ] tail 标明 actual / estimate；
- [ ] SOXX 2021-09 前保留空白；
- [ ] SPY/QQQ 五年历史来自正式 backfill；
- [ ] latest / percentile / coverage / as-of 可读；
- [ ] 亏损成员与 denominator 风险处理通过；
- [ ] KLAC/MCHP 等异常不会污染；
- [ ] 正式估值写入 SSOT；
- [ ] read-only verifier 通过；
- [ ] HTML 自包含；
- [ ] PDF fallback 含图；
- [ ] 不重复发送 photo；
- [ ] stale/unavailable 不阻断晨报；
- [ ] writer lock / rc / alert 通过；
- [ ] 专属、相邻、全量测试零新增失败；
- [ ] Boss 目视批准；
- [ ] merge / push / deploy / cron 分别获批。

---

## 11. 当前判断，但不是 CC 审核结论

现有资产足以证明项目可行：

- forward 原始数据和 holdings 已 LIVE；
- SOXX 历史数据质量问题已在真实场景中暴露并修复；
- 一次性图证明视觉形态可行；
- 设计已正确区分 hindsight 与 PIT。

但当前不应直接机械执行旧计划。至少先闭环：

1. 主 PE 口径；
2. 两个落后分支与 current main 的语义合并；
3. hindsight tail overwrite / percentile；
4. verifier provenance；
5. 晨报 section 顺序和测试基线。

在 CC 审核和 Boss 批准修订前，正确状态是：

```text
PLANNED / ASSETS PARTIALLY READY / NOT IMPLEMENTED / NOT LIVE
```
