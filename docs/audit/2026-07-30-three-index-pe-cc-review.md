# 三指数 PE 晨报项目 — CC 独立审核报告

> 日期：2026-07-30
> 审核对象：`docs/audit/2026-07-30-three-index-pe-morning-report-cc-handoff.md`
> 审核基准：main@8ca3da9 / codex/index-pe-morning-chart@a82a446 / codex/soxx-historical-ttm-pe@4b96d47
> 方法：三个并行只读核查（git/DB 状态、main 代码能力、SOXX worktree 资产）+ plan 引用逐条 grep 对齐
> 结论：**Ready to execute: With plan fixes**

---

## 1. 做得好的地方

1. **hindsight / PIT 语义隔离是全计划最强的部分。** 设计从 schema 字段名、图例颜色、测试断言到 issue 文档四层强制 "后视镜 NTM ≠ 历史可交易 forward P/E"，最大方法论风险（把 ex-post 当信号）被系统性设防（design §2.2/§7，plan §5 防线 1-5）。
2. **事实声明准确率高。** 逐项核验 ~35 项，git/DB/云端三段 17/19 完全一致；DB 数字（snapshot 1068/1009/59、estimates 17,590/1,043、holdings 五篮子行数）全部精确吻合。这份 handoff 是可信的审计基础。
3. **SOXX 前置资产质量超出 handoff 的保守描述。** 修复资产（TERN/CREE alias、KLAC/MCHP sanity）全部是配置驱动的通用逻辑而非一次性脚本（`terminal/historical_market_cap_sanity.py` 全文无 ticker 分支；alias 走 `config/soxx_symbol_aliases.json` 三重 exact-match）；8 个新测试文件 2,162 行零既有测试改动。
4. **plan 引用对得上真实代码。** `--image-report/--image-delivery html/--no-telegram` 旗标（`scripts/morning_report.py:2794-2805`）、六个 forward 测试文件、`fwd_pe_blend/fwd_pe_ntm` 列（`src/data/market_store.py:554-555`）全部存在。没有踩"plan 伪代码引用幻觉"的坑。
5. **存储边界设计正确。** 三表分工（PIT forward / 日频 TTM 证据 / 周频图表物化）主键语义、写入方、生命周期都不同，方案 B/C 的否决理由成立。
6. **handoff 自身的"不要把 X 当成已实现"清单**（§0）方向正确，5 条全部核实为真——尤其 `fmp_basket_valuation` 确实 0 行、三指数 producer/renderer 确实零命中。

## 2. 问题清单

### P0-1：主 PE 口径冲突确认为真，且比 handoff 描述多一层 gate 缺口

**事实**：SOXX 分支主指标是 holding-weighted `1/[Σ(w×NI/mcap)/Σw]`（`terminal/historical_basket_valuation.py:269-298` → 字段 `rebalance_weighted_ttm_pe_gaap_proxy`），aggregate `Σmcap/ΣNI` 只是次级（`:301-312` → `uncapped_mcap_basket_pe_gaap`）。而设计文档 §2.1 已冻结三指数统一用 aggregate，plan §3.1 同。

**handoff 没点到的更深一层**：次级指标**不受 90% coverage gate 约束**——`historical_basket_valuation.py:473-475` 中 coverage<90% 时 primary 为 None 但 secondary 照常写值。Task 4 说 "wrapper 复用原 audited backfill"，若直接提升 `uncapped_mcap_basket_pe_gaap` 进 `ttm_pe_gaap`，等于把一个未过门控的指标当成了已审计指标。

**触发条件**：Task 4 实现者按 "复用已审计层" 字面理解，提取 SOXX 引擎的主字段（51x）或未门控的次级字段。
**错误结果**：同一天 SOXX 显示 51x 或 39x；TTM 线与 NTM 两条线（plan §3.1 均为 aggregate 公式）口径不一致，同面板三线不可比；五年 percentile 整体漂移 ~25%；或发布 coverage 不足的点。
**修复方向**：plan Task 4 增加显式契约——`ttm_pe_gaap := Σmcap/ΣNI`，由同一 covered set 计算，且 `mcap_coverage_ttm >= 0.90` gate 必须作用于该 aggregate 指标本身；holding-weighted 仅留 `basket_ttm_valuation` 诊断字段，不进 weekly 表、不进图。

### P1-1：SOXX 分支存在潜在数据丢失 bug（本次核查新发现，handoff 未提）

`terminal/historical_market_cap_sanity.py:339-354` 的 `refresh_market_cap_windows` 在 API 返回空时无条件 `replace_historical_market_cap_range(..., [])`——**空响应会删掉整个 range 的 HMC 行**。生产实际跑的是 `backfill_soxx_historical_pe.py:539-548` 的内联版本（`if not rows: continue`，安全），但被单测覆盖的是危险的库函数版（`tests/test_historical_market_cap_sanity.py:251`），跑的那版反而没有集成测试。
**触发条件**：Task 1/4 泛化重构时任何调用方改用库函数版 + FMP 一次 503/空响应。
**错误结果**：静默清空某 symbol 数周 HMC，下游 TTM 序列出现假 gap。
**修复方向**：Task 0 合并后、Task 1 重构前统一两个实现（库函数改为空响应 fail-closed），补集成测试。

### P1-2：verifier 的 PE 聚合层是手抄复制件，口径切换时必然双改

`verify_basket_ttm_pe.py:397-406` 与 `historical_basket_valuation.py:278-290` 数学逐字相同，90% 阈值各存一份（verifier `:30` 常量 vs producer `:273` 默认参数）。issue048 只讲了共享内核 "同错同过"，没讲这段复制粘贴的反向风险。
**触发条件**：P0-1 修复把主口径切到 aggregate 时只改了 producer。
**错误结果**：verifier 假红（或改了 verifier 忘了 producer 的假绿）。
**修复方向**：聚合公式共享同一纯函数（承认 verifier 定位是 tamper/evidence 检查器）；独立性按 issue048 的异构抽样 reconciliation 补足，不维持手抄双份。同时 Task 4 的 manifest 要求应显式对齐 issue048 列的四个 recurring 关闭条件（append-only manifest 含 pre/post refresh row hash 等）。

### P1-3：Task 0 的验收数字和冲突面描述都需修订（风险实际更低）

- plan `:202` 写 "15 个历史分支 commits"，实际 18（audit 基准 d47f6a9 后又加了 1 个审计修复 commit `4b96d47`）。按现 plan 验收会立即假失败。
- handoff §5.1 声称 5 文件双侧修改，实际 merge-base `db86d75` 之后 main 侧 11 个文件与 SOXX 侧 38 个文件**交集只有 `ARCHITECTURE.md`**；`src/data/fmp_client.py`、`fmp_forward_ingestion.py`、`market_store.py`、`CLAUDE.md` 均只有分支侧改动。合并风险被高估。
- **修复方向**：Task 0 改为：feature branch 先 `git merge main`（消化 14 behind，含 volconc 新测试），再 `git merge --no-ff codex/soxx-historical-ttm-pe`，验收数字改 18 commits / 冲突预期仅 ARCHITECTURE.md，组合基线加入 volconc parity 测试。
- 另注意：SOXX 分支最终代码从未产出一份完整成功的 dry-run 工件（其 audit §10.1，代理 503 致 MKSI/MTSI/MU 为空）——Task 4 的全量 dry-run 必须视为首次完整验证，不能引用旧工件当基线。

### P1-4：Task 7 三处漂移需先冻结

1. **插入位置**：原计划 "大盘择时之后、PMARP 之前" 已被 `0b 成交集中度` 占位。**建议方案 B（0c，放 0b 之后）**：main 刚为 0b 建了 frozen-fixture parity 测试（a23b2e3），插在 0b 前会重排现有 section、触碰 parity 基线。
2. **`*D. Dollar Volume*` 是 Telegram 分段硬标记**（`scripts/morning_report.py:104` `split_marker`），任何重编号不得触碰该字符串；文本面 `D.` 与 HTML/PNG 面 `3.` 编号本就不一致，新 section 沿用 `0c` 前缀可完全绕开。
3. **HTML renderer 没有 block type 概念**（`terminal/morning_html_report.py:33-44`，全文 50 行，靠 `rows is None and not columns` 推断）。plan 写 "增加最小 `block.type == image` 分支" 实际是引入一个新分派机制，且 `tests/test_morning_html_report.py` 仅 89 行、覆盖很薄——工作量和测试扩面要按新机制预估，不是加一个 case。

### P1-5：hindsight tail 的演化契约缺失

tail 点会随每周 actual 落地从 `latest_consensus_tail` 演化为 `actual_only`，这是**同一 methodology 下的合法数据更新**，但 plan Task 2 只定义了 "同 version 幂等 / 异 version 拒绝覆盖"，没定义 tail 重写规则。
**触发条件**：第二次 weekly run 重算最近 12 个月的 hindsight 点。
**错误结果**：要么被幂等语义卡住无法更新，要么静默重写无审计痕迹。
**修复方向**：Task 2 增加契约——每次 weekly run 重算所有 tail 未满的点；`quality_tier` 只允许单向升级（estimate→actual），降级必须写 warning；percentile 只对 `actual_only` 点计算（见第 3 节 Q5）。

### P1-6：Task 9 测试基线已过期

原计划基线是 2026-07-19 环境；此后 main 合入了 volconc 整条线（`tests/test_morning_report.py` 现 2,898 行）。"5 个 Concept Registry 环境失败" 与 SOXX audit 记录的 "14 failed" 也需在合并后重新冻结。执行前重跑 full suite 并记录新基线，作为 Task 0 验收的一部分。

### P2（执行前顺手修，不阻塞）

1. **配置文件位置**：`config/index_pe_baskets.json` 应进 `config/baskets/` 目录（`update_fmp_forward.py:133` `load_basket_configs` 已有加载惯例），不在 config/ 根另起平行文件。
2. **新表白名单注册**：`basket_weekly_pe_history` 需显式注册进 `market_store.py:606` 的表白名单机制（Task 2 实现细节，写进 RED test）。
3. **既有耦合点**：`scripts/verify_fmp_forward.py:320` 已在读 `fmp_basket_valuation`（当前空表走空路径）；Task 5 开始写入后该 verifier 行为改变——本就在 Task 5 修改范围内，但要写进其 RED tests。
4. **测试命名去歧义**：`test_index_holdings_normalizer.py`（历史 disclosure normalizer）与既有 `test_fmp_forward_ingestion.py` 覆盖的 `normalize_holdings`（live snapshot）是两个不同 normalizer，命名应明确区分以免误判重复。
5. **handoff 文档事实修正**：§5.1 冲突文件清单（只剩 ARCHITECTURE.md）；§2.2 一次性工件是 4 个文件（漏 `_with_percentiles.png`）；SOXX HEAD SHA 回填 `4b96d47`。
6. **仓库卫生**：repo 根目录存在 0 字节的 `market.db`/`company.db` 占位文件（真库在 `data/`），照错误路径查询会静默得到空库，建议删除或加 .gitignore 说明。
7. **tracker 漂移**：`.claude/ongoing.md:48` "Phase 2 尚未立项" 在计划修订获批后更新为 "规划完成 / 待执行 / 非 LIVE"。
8. **cron SLO**：101 分钟数字本轮未独立复测；新增步骤全部是本地计算（无新增逐股网络调用），预期增量有限，但 Task 8 应按 step 记录耗时落 `summary_json` 后实测确认。

## 3. 第 8 节问题解答

### 金融方法论

**Q1 主指标**：aggregate full-market-cap（`Σmcap/ΣNI`）。决定性理由：plan §3.1 的 hindsight NTM 和 PIT NTM 公式都是 aggregate，TTM 若用 holding-weighted 则同面板三条线不可比；且 aggregate 是设计已冻结口径、三指数横向可比。holding-weighted 回答的是 "持有该 capped-weight ETF 的盈利收益率"，是另一个问题。

**Q2 双指标分工**：aggregate 进主图和 percentile；holding-weighted 只留在 `basket_ttm_valuation` 诊断字段，不进 weekly 表、不进图。SPY/QQQ 不需要展示 holding-weighted。

**Q3 near-zero warning**：需要。周期行业 ΣNI 微正时 PE 会爆到 100x+（SOXX 在半导体下行期真实可能）。建议：basket earnings yield < 1%（即 PE>100x）时写 warning，图上 y 轴截断 + 数值标注，不隐藏不平滑。

**Q4 proxy 标注**：必须。面板标题或脚注写明 "成分来自 ETF 披露代理，非官方指数历史 P/E"。成本极低，诚实度必要。

**Q5 tail 进 percentile？**：不进。percentile 只对 `actual_only` 点计算。TTM 线本身无 tail 问题不受影响；hindsight 最新点几乎必然含 estimate tail，纳入会让 "五年分位" 混入两种口径。

**Q6 PIT NTM 三个周点**：按设计原样——coral 点 + 短线，图例注明 "自 2026-07-13 起累积"，不给 percentile、不外推、不改写日期。这已是最诚实的展示。

### 数据与 PIT

**Q7 restatement vintage**：`accepted_date <= valuation_date` 不能完全解决——issue047 已承认 re-fetch 拿到的是最新 vintage。对估值图（非可交易信号回测）可接受，但必须作为已知限制写进方法论文档，不要在本项目内试图解决 vendor vintage 问题。

**Q8 hindsight 不做 accepted-date gate**：符合定位。ex-post 本义就是 "事后全知"，加 gate 反而制造第三种混合口径。

**Q9 fiscal-quarter 去重**：规则够，补两个测试：同一 fiscal quarter 的 actual 与 estimate period-end 对齐（fiscal calendar 变更 / 52-53 周年）；actual 与 estimate 的 fiscal key 必须由同一函数生成。

**Q10 SPY/QQQ composition 日期可行性**：API probe 已验证日期覆盖，publishable 比例只有 dry-run 能定量。保留为 Task 4 dry-run 的 go/no-go 检查点，现在不必拍板。

**Q11 7天 HMC + 90% + jump sanity**：结构上够（新鲜度/覆盖率/一致性三类互补），前提是 gate 作用于 aggregate 指标本身（P0-1）且 90% 按 mcap coverage 计。

**Q12 immutable manifest**：是，必须。升级为 recurring weekly product 恰好触发 issue048 自己列的条件，Task 4 的 manifest 要求应逐条对齐其四个关闭条件。

### 架构与范围

**Q13 SOXX 分支合并路径**：只合进三指数 feature branch，不单独进 main。实际冲突面仅 ARCHITECTURE.md；SOXX 产物表尚未被生产使用，单独 merge 无独立价值、多一道 review。顺序：feature 先合 main，再合 SOXX 分支。

**Q14 三表边界**：合理，保留。三表主键语义（snapshot_date vs valuation_date）、写入方、生命周期都不同；用 `fmp_basket_valuation` 兼做周频历史反而会混 PIT 与 ex-post。修正项只有：新表注册白名单 + verifier 增加 weekly 表与日频证据表同日对账。

**Q15 methodology version 与 tail 演化**：version 管口径变化，不管数据演化。tail estimate→actual 用 `quality_tier` 单向升级 + 每周重算 tail 未满点表达（见 P1-5），不 bump version。

**Q16 共享内核**：一次性 backfill 可接受，recurring 不够——且实况比 issue048 更糟（聚合层是手抄双份，见 P1-2）。方向：内核统一共享 + 异构抽样 reconciliation 补独立性。

**Q17 HTML typed image + data URI**：是最小可行路径，但要按 "引入新分派机制" 而非 "加一个 case" 预估（renderer 现无 type 概念，测试仅 89 行）。

**Q18 位置**：`0b 成交集中度` 之后（0c）。避免重排触碰 volconc parity 基线和 `*D. Dollar Volume*` split_marker；估值是慢变量，排在择时因子之后语义合理。

**Q19 cron**：继续单 wrapper；新步骤进 `update_fmp_forward.py` 的 Python run 语义（继承 manifest running→complete/failed 状态机），不在 shell 加平行步骤；per-step 耗时写 summary_json，dry-run 实测 SLO。

**Q20 是否拆阶段**：不拆成三个独立计划，拆成**三个审批停点**，且不按 handoff 的 2A（先做当前 PIT）顺序——那会把动生产 cron 的风险前置。保持 plan 原任务顺序：
- **停点 1**（Task 0-4）：历史 TTM+hindsight 数据产品 + verifier，全程离线 dry-run 验收；
- **停点 2**（Task 5）：打开 `fmp_basket_valuation` 生产写入；
- **停点 3**（Task 6-9）：图表 + 晨报 + 运维 + 部署审批。
风险最低的顺序是先离线后在线，最小交付物（正式历史数据产品）也在停点 1 就能给 Boss 看。

## 4. 执行方式判断

**先修订再执行**（不是原样执行，也不必推倒拆分）。设计本身（口径冻结、三线语义、存储边界、晨报降级）经核验是健全的，前置资产比 handoff 描述的更扎实（合并冲突面只有一个文档、修复逻辑全部可复用）。需要修的是 plan 层的 7 处：P0-1 口径契约、P1-1 空响应 bug、P1-2 verifier 手抄件、P1-3 Task 0 数字与顺序、P1-4 Task 7 位置冻结、P1-5 tail 契约、P1-6 基线重冻结。全部是文字级修订 + 一个小 bug fix，半天内可完成，不需要回北极星。

## 5. 结论

```
Ready to execute: With plan fixes
```

修订完成并经 Boss 批准后，按三停点顺序执行 Task 0-9。本轮未做任何实现、merge、push、生产 DB 写入或 cron 修改。

---

## 附：handoff 事实核验汇总

| 声明 | 判定 |
|---|---|
| git 状态（HEAD/worktree/ahead-behind/diffstat） | A1-A6、A9 全部一致 |
| §5.1 五文件双侧冲突 | **不一致**：交集仅 ARCHITECTURE.md |
| §2.2 一次性工件 3 个文件 | **不一致**：4 个（漏 `_with_percentiles.png`） |
| DB 数字（runs/estimates/holdings/价格新鲜度） | 全部精确一致（真库为 `data/market.db`） |
| 生产缺表四张 | 一致 |
| 云端 main@8ca3da9 + 两条 cron | 一致 |
| 晨报 section 顺序 / 择时表列名 / renderer 能力 | 一致（renderer 实况比声明更弱：无 type 分派） |
| 14 个计划文件不存在 | 一致 |
| `fmp_basket_valuation` schema/CRUD 存在、0 行 | 一致（另有 `verify_fmp_forward.py:320` 既有 reader） |
| ongoing.md "Phase 2 尚未立项" | 一致（`:48`） |
| SOXX 分支已实现清单与自认限制 | 一致（主 PE 口径为 holding-weighted 确认属实） |
