# Extended Primary Universe — Stop A 执行中途 Handoff（CC → Codex）

> **日期**: 2026-08-19
> **交接原因**: Boss 指示暂停 CC session，Stop A 剩余工作移交 Codex
> **Worktree**: `/Users/owen/CC workspace/Finance/.worktrees/extended-primary-universe`
> **Branch**: `codex/extended-primary-universe`（base main@8ca3da9，未 merge，对主仓库零影响）
> **HEAD**: `ddecd94`
> **Plan（binding）**: `docs/plans/2026-08-16-extended-primary-universe-implementation.md`（v2.4，Boss PASS）
> **Spec（最高权威）**: `docs/design/requirements.md`（R1–R14）
> **执行 ledger（必读）**: `.superpowers/sdd/2026-08-16-extended-primary-universe-implementation/progress.md` — 全部 17 条裁决、每个 task 的 fix loop 记录、deferred minors 清单都在里面，本文档只做导航和压缩摘要

> **Codex 完成更新（2026-08-19）**: 本文以下内容保留为历史交接现场；Codex 已完成 B2 fix loop、B3–B5、T21、Stop A final review fix wave 与收尾 gate。最终全量为 **2700 passed / 原有同一 15 failed / 4 skipped**，`check_core_references.sh` 为 57 条已解释兼容/Stop G 引用。当前状态已推进到 **等待 Boss 验收 Stop A → Stop B**；仍未 merge、push、部署或修改 crontab。

---

## 一、当前进度总览

| 阶段 | 状态 |
|---|---|
| Stop 0 北极星修订 | ✅ `e78d290`（+ 分支文档 `b362f54`） |
| T1–T19 | ✅ 全部完成并过独立审查（含 fix loop 闭环） |
| T20-B1（矩阵行 1–5） | ✅ 完成并过审（`3800241`→`571abaa`） |
| **T20-B2（矩阵行 6–13）** | ⚠️ **代码已完成（`bd7aed1`→`ddecd94`，10 commits），独立审查未闭环** —— 见「二、第一件事」 |
| T20-B3（行 14–16 forward 线） | ⬜ 未开始 |
| T20-B4（行 17–20 回测） | ⬜ 未开始 |
| T20-B5（行 21–23） | ⬜ 未开始 |
| T21 文档收尾 | ⬜ 未开始 |
| Stop A 收尾验收 + 整分支 final review | ⬜ 未开始 |

测试现状：全量 `pytest tests/` = **2675 passed / 15 failed / 4 skipped**。15 个失败全部为 main 既有基线（见「五、失败基线」），本分支零新增——这是每个后续 batch 的硬 gate。

每个 task 的实现/审查/修复详情见 `.superpowers/sdd/2026-08-16-extended-primary-universe-implementation/` 下的 `task-N-brief.md` / `task-N-report.md` / `review-*.diff`。

## 二、第一件事：B2 fix loop（审查已完成，发现 2 Critical + 2 Important）

B2 独立审查（opus，diff `review-571abaa..ddecd94.diff`）在 handoff 后回传，**verdict = Needs fixes**。审查全文见 ledger 尾部「B2 REVIEW VERDICT」段。Codex 不需要重做审查——直接进 fix round 1，修复以下四项后做 scoped re-review，全清才进 B3：

**Critical 1 — post-bootstrap 时 sub-$10B Core 票完全失去价格采集（行 #6 自身验收标准"覆盖 ≥ 现状"未达成）**。审查者逐行 trace 证实：bootstrap 后 FMP 腿只拿 overlay tier（`price_fetcher.py:131`），yfinance 腿 = base − tier（`update_data.py:137`），而 bootstrap 的首个 membership snapshot 只含 `raw ∩ eligible`（extended $10B+ screener，`bootstrap_security_master.py:351`）——Core 里低于 $10B 的手动票（`source ∈ {analysis,manual}`，B5 行 #22 点名的那批）两条腿都不覆盖，`daily_price` 从 bootstrap 当日起静默断流，双腿各自报成功。测试 fixture 甚至编码了这个洞（`test_price_tier_split.py:20,23` 的 LEGACY_CORE 含 base 外的票）但断言只看 BASE_UNIVERSE（`:93`）。修法（审查者给出，二选一或都做）：① `_yfinance_price_leg_targets` 在 universe.json 仍存在期间并入 legacy Core pool；② price step 顶部加显式断言 `set(legacy_core) ⊆ set(fmp_targets) ∪ set(yf_targets)`，不满足即 fail loud——后者同时给 Stop C 一个「#22 未跑先兜底」的 tripwire（#22 与 bootstrap 的先后顺序目前无任何保障）。

**Critical 2 — 行 #8 forwarder 静默收缩生产 cron 的 forward-estimates 目标集**。`run_forward_data.sh:25` → `--forward-estimates --scope=all` → `_resolve_target_symbols`（`update_data.py:83-85`）= `Core ∪ get_extended_only_symbols()`。迁移前 = Core ∪ extended；post-bootstrap forwarder 返回 base − overlay_tier，于是 `(base ∩ tier) ∖ Core` 掉出目标集——**在 extended 里但不在 Core 里的持仓/watchlist 票**（非科技 $10B–$100B 持仓即中招）失去周频 yfinance forward estimates。B3 行 #14 只保 FMP forward 线的 holdings overlay，本条 yfinance 线的同类暴露没有任何矩阵行覆盖，是 B2 引入的。`verify_forward_coverage.py:90` 的分母也随之漂移。修法：`--scope=all` 的 union 改为显式并入 overlay tier（或 forwarder 的替代语义补回 tier），并加测试锁定「持仓票不丢」。

**Important 3 — 行 #12 的 parity 测试是同义反复**。两次运行喂同一张预制 rank 表，`old ⊆ new` 对任何实现恒真，测不出真正的回归机制：`rs_rank` 是横截面百分位（`rs_rating.py:128`），池从 ~209 扩到 ~950 会重排名，Core 票可能跌破 `THEME_RS_THRESHOLD=80` 从主题里消失。修法：fixture 改喂价格序列，`run_momentum_scan` 在两种池规模下真跑，断言旧票不消失（或有损失时显式列清单）。

**Important 4 — 两条价格腿各自独立探测 bootstrap 状态**。第一腿成功、第二腿瞬时失败（DB 锁等）时：FMP 收缩到 ~50 票 + yfinance 腿返回 `[]`，全 base universe 当日无价——唯一信号是 stdout print、exit 0。修法：base universe 解析一次、两腿共用；失败路径升级为 `logger.error`。

**Minors（ledger 记档，final review triage）**：#5 yfinance 腿降级用 print 非 logger.warning；#6 行 #9 commit 超出行文件清单（已自报）；#7 bootstrap 探测三次全量读（可加廉价 has_membership()）；#8 `--refresh-universe` 分支的 `symbols =` 赋值已死代码。

**审查确认无误的部分**（不必重查）：pre-bootstrap 回退机制真实有效（current_base_universe 是 raise 不是返回空）；`968246b` --all 双跑修复正确；#10 conditional-required 偏差被审查者裁定优于字面 required=True；#11 的测试清单编辑有更强断言补偿。

## 三、剩余工作清单（按序）

### T20-B3（行 14–16，forward 线——本矩阵语义最敏感的批次）
- #14 `src/data/fmp_forward_ingestion.py:390-409`：union 改 `resolver(base=extended, overlays=(holdings,watchlist,benchmarks)) ∪ ETF baskets ∪ MAGS`；fail-fast 改为 SM/eligible 空。**Parity 用「允许损失清单」语义（R2-P1-5）**：`旧 union − 新 union ⊆ {SM reason ∈ {secondary_share_class, etf, fund}}`，损失清单显式列出（GOOGL 被 SM 去重是设计内损失，Alphabet 主类已拍板 = GOOG），其余票一只不许丢；universe_hash 变更在 manifest 正常滚动。
- #15 `scripts/update_fmp_forward.py:632-649`：双路径都换 resolver 注入（`--data-root` 测试路径给 file-based eligibility loader）；现有 forward 测试全量回归。
- #16 `scripts/verify_forward_coverage.py:88-91`：单分母 eligible + overlay 桶单列；桶计数之和 = 旧双桶并集。
- ⚠️ forward 线是 LIVE 生产周频快照（yfinance 对拍期），改动前先读 `tests/test_market_store_fmp_forward.py` 与 forward 全套测试，pre-bootstrap 必须优雅回退（merge 先于 bootstrap 部署，见「四、裁决」#B2 时序）。

### T20-B4（行 17–20，回测——默认值不许翻）
- #17 `backtest/adapters/us_stocks.py:277-298`：**新增** `"eligible_extended"` 选项；`else` 默认保持 market.db-all **不变**；bare `USStocksAdapter()` 逐字节回归。
- #18 `backtest/event_study/runner.py:203,354-360`：`_read_symbols` 容忍裸 list 与 dict 两种 schema（照抄 `_load_universe` :1510-1522 模式）。
- #19 `scripts/run_rs_backtest.py:223`：choices 增 `extended_true` + `eligible_extended`。
- #20 `backtest/adapters/us_stocks.py:203-209`：`strict_mcap=True` 参数，False 时 warn+排除缺数票并列清单；默认 True 现状不变。

### T20-B5（行 21–23）
- #21 `src/data/fundamental_fetcher.py` 六处 `symbols=None` 默认 → `ValueError` + DeprecationWarning（`--fundamental` 已经 T11 走内核）；显式传参路径回归不变。
- #22 新建 `scripts/migrate_core_watchlist.py`：`Core − eligible − ETF` → company.db watchlist（幂等）；SOXX 确认在 forward ETF baskets、不入 watchlist 且输出说明归属。**代码本 task 备好，执行在 Stop C**。注意 T16 的 `add_to_watchlist` 有 `IS_CLOUD` 云端写入防护。
- #23 `"pool"` selector → 读 `data/pool/archive/universe.json` 冻结版 + DeprecationWarning；两路径都缺 → 明确报错指向 `eligible_extended`。
- **明示不迁移**（加注释即可，勿动）：`scripts/rs_universe_scan.py:60-68`（intentional 独立广扫）、`scripts/backfill_social.py`（已 archive）、`terminal/freshness.py:173-191`、`backtest/breadth_study` 的 `universe_variant` ~40 处（**禁止触碰**）。

### T21 文档收尾
`ARCHITECTURE.md` + `CLAUDE.md`（Data Desk 股票池表：Extended 单文件路径修正、新增 SM/membership/vintage/coverage 行）+ `docs/CHANGELOG.md`（里程碑 + `--scope core` 走内核行为新增 + P1 价格线切分）。遵循文档刷新四原则（简洁/不写易腐数字/只描述当前态/不重画图）。北极星已在 Stop 0 完成，勿动。

### Stop A 收尾验收（plan 原文四条）
1. 基线 4 文件不回归：`tests/test_extended_universe_manager.py tests/test_update_data_scope.py tests/test_market_store.py tests/test_market_store_fmp_forward.py`（已从 79 增长，全绿即可）；全量套件零新增失败（vs 15 基线）。
2. `bash scripts/check_core_references.sh` 清单 == T20 已完成行 + Stop G 待删项，无计划外引用（当前 66 条，B3–B5 完成后应显著收敛；exit 0 是 Stop G 的验收不是 Stop A 的）。
3. 对照 `feedback_plan_self_audit_blind_spots` 八类盲点自审。
4. **整分支 final review**（用最强可用模型，diff 范围 `git merge-base main HEAD`..HEAD），入口给它 ledger 里的 deferred-minors 与 parked 清单做 merge 前 triage——特别是 **T10-M2（`main()` 在持锁前构造 MarketStore，Stop C/D 前必修，一行 reorder）** 和 T17-M2（`_publish_cache` 只 catch OSError）。final review 的发现走一轮 fix wave + 一次 scoped re-review。
5. 全清后交 Boss 验收 → Stop B。**禁止自动 merge / push / 部署 / 改 crontab**（Stop B–G 每步单独 Boss 批准）。

## 四、必须遵守的既定裁决（覆盖 plan 原文处，以此为准）

完整 17 条见 ledger；影响剩余工作的关键项：

| # | 裁决 | 影响 |
|---|---|---|
| R10 | 内核 ratios 用 legacy limit=4（非 limit_quarters=8） | 已实现，勿改 |
| R11 | 一切 runner 传**完整 UTC timestamp** 作 observed_at（纯日期会归一到 T00:00:00Z 导致同日重采集撞 vintage PK） | B3+ 若触碰采集调用点必须遵守 |
| R12 | `rebuild_profiles_json` = **merge-with-table-priority**（非 plan 原文的 rebuild；表按 symbol 优先，外来 JSON 条目保留到 Stop G） | 勿"修正"回 rebuild |
| R13 | reconcile 自持锁 → **Stop E 的 reconcile cron 行必须去掉 `FINANCE_CRON_RESOURCE_KEY`**（wrapper 锁+子进程自锁同文件 = 永远 exit 75）；events 行保持 wrapper 持锁不变 | Stop E 执行时 |
| R17 | verifier 的 excluded_other 桶保留语义但计数上浮到文本摘要，不设阈值 | 已实现 |
| 时序 | **merge（Stop B）先于 bootstrap（Stop C）部署**：所有日频生产路径（价格/晨报/scan/indicators）pre-bootstrap 必须带日志的优雅回退 legacy，membership 建立后自动切换；采集/修复类工具（backfill/reconcile）则 fail-loud | B3 的 #14/#15 同样适用 |
| 命名 | coverage_status.dataset = **表名**（income_quarterly…+ "identity"）；backfill jobs.dataset = **dataset key**（income…）；join 时勿混 | B3/#16 与 T18 已处理，新代码注意 |
| 锁 | backfill runner 与 reconcile 自持 `/tmp/finance-cron-locks/resource-market_db_writer.lock`——**绝不**再套 cron_wrapper 的同名 resource key | Stop C/D/E runbook |

## 五、失败基线（gate 参照）

15 个 pre-existing 失败（已在 base commit 用隔离 worktree 验证与本分支完全一致）：
`test_morning_report.py` ×5（骨架 DB 缺 concept-registry 行）、`test_telegram_routing.py` ×2（缺 `PORTFOLIO_SHEET_ID`）、`test_breadth_buy_quality.py` ×7 + `test_pipeline_scratchpad.py::test_collect_data_logs_errors` ×1（live-data/网络依赖）。
每个 batch 结束跑全量，**失败集必须与这 15 个逐行一致**。

## 六、环境与纪律（不变项）

- venv 绝对路径：`"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest ...`（worktree 不共享 venv）
- worktree 无 live data：一切测试 tmp fixture，禁碰 `data/`
- 云端 Python 3.10：禁 f-string 反斜杠、禁 match/case
- 严格 TDD：每行/每 task 先 RED（留证据）再 GREEN；**精确文件 commit，禁 `git add -A`**；消息前缀 `feat(universe):` / `fix(universe):`（矩阵行带 `[matrix #N]`）
- plan 行号来自 2026-08-18 审计，动手前 grep 重定位
- 每 task/batch 实现后做独立审查（spec + quality 双结论），Critical/Important 进 fix loop 到清零
- `universe.json` 双端并集 merge 只增不减——任何任务禁止把 extended 名单写入该文件（T16 已切断写侧并有字节不变测试）

## 七、给 Stop C–G 的累积运维备注（执行 runbook 时逐条核对）

1. **Stop C 顺序**：issue 046 备份裁剪（Boss 已知待批）→ 部署 → **bootstrap 必须在第一次周六 weekly refresh cron 之前跑完**（T17 对空 SM fail-loud，否则周六 cron 中止）→ bootstrap 之后才可触发任何 `rebuild_profiles_json`。
2. **Stop C/D 前必修**：backfill runner `main()` 持锁前构造 MarketStore（首次 schema 变更时是锁外写）——一行 reorder，final review fix wave 处理。
3. Stop D 用 runner 直跑（nohup），闸门是 backfill 结束后的独立步骤；`--resume` 对每 symbol 重拉全部 5 dataset（partial resume 不便宜，T8 内核 API 所致）；run header 可能在有 over-cap fetch_failed 时仍标 complete——判定看 `run_progress` 不看 header。
4. Stop E：reconcile cron 行去 resource key（裁决 R13）；`--fundamental --scope events` exit 0 不反映 dataset 失败，告警靠周日 reconcile Telegram 摘要。
5. 06:30 `run_market_data_pipeline.sh` 存在 price/broad 重叠拉取（幂等但浪费窗口）——B2 遗留 ops note，T21 文档或 Stop C 时定夺。
6. GOOG 的 T2 fixture 是合成的（主仓库 profiles.json 无 GOOG）——bootstrap 首跑会真实拉取，无需额外动作，知悉即可。

## 八、启动命令

```bash
cd "/Users/owen/CC workspace/Finance/.worktrees/extended-primary-universe"
# 1. 读本文档 + ledger
# 2. 验证现场：
git log --oneline -5        # HEAD 应为 ddecd94
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/ -q 2>&1 | tail -3   # 2675 passed / 15 failed
# 3. 从「二、第一件事」B2 审查开始
```
