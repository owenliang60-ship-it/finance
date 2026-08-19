# Extended Primary Universe — Stop A 执行中途 Handoff（CC → Codex）

> **日期**: 2026-08-19
> **交接原因**: Boss 指示暂停 CC session，Stop A 剩余工作移交 Codex
> **Worktree**: `/Users/owen/CC workspace/Finance/.worktrees/extended-primary-universe`
> **Branch**: `codex/extended-primary-universe`（base main@8ca3da9，未 merge，对主仓库零影响）
> **HEAD**: `ddecd94`
> **Plan（binding）**: `docs/plans/2026-08-16-extended-primary-universe-implementation.md`（v2.4，Boss PASS）
> **Spec（最高权威）**: `docs/design/requirements.md`（R1–R14）
> **执行 ledger（必读）**: `.superpowers/sdd/2026-08-16-extended-primary-universe-implementation/progress.md` — 全部 17 条裁决、每个 task 的 fix loop 记录、deferred minors 清单都在里面，本文档只做导航和压缩摘要

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

## 二、第一件事：闭环 B2 审查

B2 的 review package 已生成：`.superpowers/sdd/.../review-571abaa..ddecd94.diff`（10 commits，~80KB）。CC 派出的审查 agent 未回传结果即中断，**Codex 必须自己完成这次审查**（或重新做一次独立审查），重点核查项按优先级：

1. **sub-$10B Core 票的价格覆盖**（最重要）：P1 切分后 FMP 只跑 overlay tier、yfinance 跑 current base universe（$10B+）。一只低于 $10B 的 Core 票、又不在 holdings/watchlist/benchmarks 里，bootstrap 前/后它的日频价格由谁采？逐行 trace `scripts/update_data.py` + `src/data/price_fetcher.py` + `src/data/extended_price_fetcher.py` 给出带行号的结论。若存在覆盖缺口即 Important，进 fix loop。
2. `968246b`（#6 `--all` 双跑 yfinance 的自修）正确性。
3. #8 forwarder 在 membership 为空时的等价性。
4. #12 scan_themes parity 测试是否真冻结了旧行为（新票可进、旧票不得消失）。
5. B2 实现者自报的三个偏差是否可接受：#10 `--universe` required-unless-`--symbols`；#9 为去重多改了 `overlays.py`+`price_fetcher.py`；#11 编辑了 `tests/test_legacy_telemetry.py` 的 known_unmigrated 清单（记录迁移进度，非掩盖回归）。

审查发现 Critical/Important → 修复 → scoped re-review，全清后才进 B3。

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
