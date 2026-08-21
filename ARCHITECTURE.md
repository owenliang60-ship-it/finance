# Architecture — Finance Workspace

**未来资本 AI Trading Desk**

战略方向（"为什么"）见 [`docs/design/north-star.md`](docs/design/north-star.md)。
本文档描述**物理实现**（"代码在哪里、数据怎么流、怎么部署"）。

**Core Principle**: Claude IS the analyst. 系统生成结构化 prompt + 数据上下文，Claude 输出洞察，结果存档复用。

---

## Code Organization (Desk Model)

```
~/CC workspace/Finance/
├── terminal/      编排中枢（pipeline / commands / macro / tools / options / dashboard）
├── knowledge/     投资框架（OPRMS / 6 lens / debate / memo / alpha / meta）
├── src/           数据引擎 + 技术指标 + BTC 择时
│   ├── data/      所有数据 client + store + manager（FMP / FRED / yfinance / MarketData / Adanos）
│   ├── indicators/ PMARP / RVOL / 社交注意力
│   └── timing/    BTC dual-engine 择时
├── backtest/      离线 R&D 实验室（多个独立子框架）
│   ├── (top)      RS 策略回测引擎（engine/portfolio/metrics/rebalancer/sweep/optimizer）
│   ├── factor_study/ 通用因子有效性研究框架（IC + 事件研究）
│   ├── pipeline/  V3 focused factor validation pipeline
│   ├── event_study/ 标准化事件研究框架
│   ├── timing/    择时回测引擎（含 BTC dual-engine）
│   ├── breadth_study/ 广度研究框架（percentile upcross / buy quality）
│   └── research/  专题脚本（PMARP/RVOL/BBWP signal stats）
├── forge/         策略锻造引擎（agent loop + evaluator + holdout 隔离）
├── portfolio/     持仓管理（holdings / exposure / benchmark / live quote）
├── risk/          IPS + 暴露监控（骨架）
├── trading/       交易日志 + 期权策略库（骨架）
├── reports/       研究报告 + 回测产物 + concept registry
├── scripts/       运维脚本 + cron 入口 + 数据回填 + 每日推送
├── config/        股票池 + API 配置（settings.py）
├── data/          数据文件（market.db / company.db / pool / macro / scans）
├── tests/         pytest 测试套件
└── docs/          文档中心（design / plans / issues / postmortems / references / research / patterns）
```

> 子模块/子系统的设计与实现细节散落在 `docs/design/` 与 `docs/plans/` — ARCHITECTURE.md 不重复。

---

## 各 Desk 概述

### Terminal — 编排中枢

所有 user-facing function 在这里。

| 入口 | 用途 |
|------|------|
| `commands.py` | analyze_ticker / portfolio_status / position_advisor / company_lookup / run_monitor / theme_status |
| `pipeline.py` | 共享构建块（collect_data / lens prompts / debate / position sizing） |
| `macro_fetcher.py` + `macro_briefing.py` + `macro_snapshot.py` + `regime.py` | FRED 16 序列 → MacroSnapshot → 5 跨资产信号 + Regime 分类 |
| `tools/` | 协议化工具注册（FRED / FMP / MarketData） |
| `options/` | 期权策略子系统（IV tracker / chain analyzer / scenario analyzer / BS solver） |
| `dashboard.py` + `pdf_report.py` + `html_report.py` | 报告渲染 |
| `concept_classifier.py` + `company_concepts.py` | Concept Registry（公司业务标签体系） |
| `company_db.py` + `company_store.py` | Per-ticker 知识库 + SQLite 写入抽象 |

> **Concept Registry** 详见 `docs/plans/2026-04-28-company-concept-registry-phase1.md`；周频自动对齐（A3 weekly-sync）见 `docs/plans/2026-06-01-a3-weekly-concept-sync-{design,plan}.md` + runbook `docs/runbooks/a3-llm-queue-review.md`
> **Options Module** 详见 `docs/design/options_module_top_level_architecture.md`

### Knowledge Base — 投资框架（无市场数据）

| 子模块 | 内容 |
|--------|------|
| `oprms/` | DNA × Timing 仓位管理系统（SSOT in `models.py`） |
| `philosophies/` | 6 lens（deep_value / event_driven / fundamental_ls / quality_compounder / imaginative_growth / macro_tactical） |
| `debate/` | Bull/Bear 5 轮辩论协议 + 索罗斯 vs 马克斯 Alpha Debate |
| `memo/` | 9-bucket memo 模板 + evidence 分级 + scorer |
| `alpha/` | Red Team + Cycle Pendulum + Asymmetric Bet 求导层 |
| `meta/company_profiler.py` | 元提示词驱动的个性化分析 |
| `options/strategies/` | 24 strategy playbooks（_index.md 快速查找） |

### Backtest Desk — 离线 R&D 实验室

不在生产管道内。每个子框架独立解决一类问题。

| 子框架 | 解决的问题 | 状态 |
|--------|-----------|------|
| **RS Engine** (top-level) | 给定选股规则 → 模拟持仓 → Sharpe/MDD/Calmar | 含 regime filter + inv_vol weighting |
| **Factor Study** | 给定因子 → IC + 事件研究双轨 → 验证预测力 | 8 因子已注册 |
| **Pipeline V3** | Focused factor validation 分层 pipeline | spec/runner/report/types 四层 |
| **Event Study** | 标准化事件研究框架 | universe gate + RVOL/PMARP 共用 |
| **Timing** | 择时信号回测（含 BTC dual-engine） | **结论：单因子机械择时全面无效** |
| **Breadth Study** | 广度信号验证（QQQ/SOXX percentile upcross） | 进行中（buy quality / event validity） |
| **Research** | 专题脚本（PMARP/RVOL/BBWP signal stats）| 一次性研究 |
| `new/` | Per-study workspaces（数据 + 报告） | 隔离每次研究 |

> 框架审计与统计纪律详见 `docs/plans/2026-03-13-factor-backtest-statistical-discipline.md`

### Forge — 策略锻造引擎

`campaign.lock` → runner（claude -p agent loop）→ evaluator（visible windows + hidden holdout）→ promote/discard。

通用合约 `StrategyConfig + run_backtest`，holdout 隔离，workspace guard。当前已锻造 dual_ema / dual_ma / helen 三个 champion strategy。

详见 `docs/plans/2026-03-26-forge-implementation-plan.md`。

### Portfolio Desk — 持仓管理（CIO-A 副轨）

| 子模块 | 内容 |
|--------|------|
| `holdings/` | 持仓 CRUD + 历史快照 + live quote provider |
| `exposure/` | 集中度 / 行业 / Beta 暴露分析 + 告警规则 |
| `benchmark/` | SPY/QQQ 相对绩效 + 归因 |

**Portfolio Intelligence (PI)** 是 CIO-A 第一阶段落地：每日云端 cron 推送（夏令时 22:00 SGT / 冬令时 23:00 SGT），集成 MarketData live quote + holdings + option ledger，PDF + 高清图片走 Telegram。

详见 `docs/plans/2026-04-02-portfolio-intelligence-design.md`。

### Data Desk — 数据引擎

**采集 client** 都在 `src/data/`：FMP / yfinance / FRED / MarketData / Adanos。每个 client 自带限流（FMP 2s / yfinance 1s / Adanos 2s）。

**数据存储**: 详见下方 Storage section。

**指标引擎**: `src/indicators/`（PMARP / RVOL / 社交注意力），可插拔扩展。

**BTC 择时**: `src/timing/dual_engine.py` + state_store。详见 `docs/plans/2026-03-26-dual-engine-btc-timing-system.md`。

**Universe 架构**:
- 默认 base: `market.db:extended_membership` active rows ∩ `security_master.eligible`（FMP `$10B+` Extended；唯一主池）。
- explicit overlays: `company.db:holdings/watchlist` + benchmarks。昂贵数据只接受显式 targets。
- `data/pool/extended_universe.json`: screener 当前名单的可重建 cache；membership DB 才是 current-base SSOT。
- Broad: `market.db` 中 `$1B+` 历史价格/市值底座，服务广扫、因子研究和历史候选，不是默认讨论池。
- Core: `data/pool/universe.json` 处于软退役兼容期；迁移完成后冻结归档，仅供旧研究复现。
- 退市 overlay: `data/pool/delisted_large_caps.json`，服务 approximate historical candidates / true survivorship。

**基本面双轨与更新状态**:
- current: `income_quarterly` / `balance_sheet_quarterly` / `cash_flow_quarterly` / `metrics_quarterly`。
- PIT: `fundamental_vintage`（UTC observed_at、change-only append）；上线前历史强制标记 approximate。
- 采集状态: `coverage_status` 六态 + retry/TTL；backfill 使用 run header、dataset 粒度 jobs、flock、熔断和 resume。
- 事件增量、reconciliation 与 backfill 共用 `fundamental_collector`，避免两套写入语义。

**数据验证三层**:

| 层 | 组件 | 检查项 |
|---|---|---|
| L1 | `data_health.py` | 11 项（池完整性/覆盖率/新鲜度/DB 完整性） |
| L2 | `data_guardian.py` | 快照/恢复（tar.gz, max 10） |
| L3 | `data_validator.py` | 完整性 + 一致性报告 |

---

## Storage

每个数据库有且仅有一个写入方，同步 = 单向拷贝，永不冲突（P3 所有权模型）。

| 数据库/文件 | 所有权 | 主要内容 | 同步 |
|-------------|--------|---------|------|
| `market.db` | 云端独占写入 | daily_price, income/BS/CF quarterly, ratios, metrics_quarterly, security_master, extended_membership, company_profile, fundamental_vintage, coverage_status, fundamental_backfill_runs/jobs, iv_daily, options_snapshots, forward_estimates/metadata, fmp_estimates, fmp_earnings, fmp_etf_holdings_snapshot, fmp_basket_valuation（Phase 2 才写）, fmp_forward_runs, historical_market_cap, broad_scan_hits, concepts(*), company_concept_tags | pull 到本地 |
| `reports/concept_registry/reviewed_current.csv` (+ manifest) | 云端独占写入 | concept registry canonical 快照（与 `company_concept_tags` symbol 集锁步；A3 weekly-sync 维护） | pull 到本地（仅 pull） |
| `company.db` | 本地独占写入 | companies, oprms_ratings, analyses, kill_conditions, holdings, watchlist, transactions, portfolio_cash, option_positions, option_transactions | push 到云端 |
| `universe.json` | 双端（退役过渡） | 冻结 Core 定义；不再承载默认 base | 双向 merge，Stop G 后归档 |
| `data/macro/` | 准实时缓存 | FRED snapshot（4h/12h TTL） | 不同步 |
| `data/companies/{SYM}/` | 本地 | Per-ticker JSON 存档（oprms / memos / analyses / scratchpad） | 不同步 |
| `data/.backups/` | 本地 | Data Guardian 快照（tar.gz, max 10） | 不同步 |

**同步**: `./sync_to_cloud.sh [--pull|--push|--sync|--status]`，含健康检查门卫 + 文件大小 50% 熔断。

**自动化**: macOS launchd 每天 09:00 auto-pull；`auto_deep_analyze.sh` Phase 5 完成后 auto-push company.db。

> ⚠️ company.db 上的 `iv_daily` / `options_snapshots` 是历史遗留（早期写本地），新数据由 market.db 接管。

---

## External APIs

| API | Plan | 用途 |
|-----|------|------|
| FMP | Starter $22/mo | 基本面 + 价格 + 分析师 grades + 内部交易 + earnings calendar + news |
| yfinance | Free | Forward estimates（6 datasets）+ 扩展池 batch 价格 + screener |
| FRED | Free | 16 宏观序列（收益率曲线 / VIX / CPI / GDP / HY spread / 美元 / 日元等） |
| MarketData.app | Starter $12/mo | 期权链 + IV + 历史 IV + PI live quote。**单 IP 绑定，必须走云端固定 IP** |
| Adanos | Hobby $20/mo | 社交情感（Reddit + X，per-ticker buzz/sentiment + 市场级 trending） |
| Claude | — | 6 lens + debate + memo + scoring（每次深度分析 ~$13-15） |

---

## Cloud & Cron

**部署**: aliyun ECS, `/root/workspace/Finance/`。代码部署走 git pull（不再 rsync）。

**云端 cron（北京时间）**:

| 时间 | 频率 | 任务 |
|------|------|------|
| 06:25 | 日频 | git auto-pull（代码部署） |
| 06:30 | Tue-Sat | 量价 + DV + IV + social 一次性更新（`run_market_data_pipeline.sh`） |
| 07:30 | Wed | 广扫池历史市值采集（`broad_universe_cron_wrapper.sh daily_hmcap`，broad $1B+ final universe / $500M+ seed） |
| 08:00 | Tue-Sat | 晨报生成与推送，HTML 附件投递（渲染/发送失败回退 PDF）（`run_market_report_pipeline.sh`） |
| 08:30 | Sat | 股票池刷新（`run_update_data.sh --pool`） |
| 09:00 | Sat | 广扫池 + 扩展池 + concept registry 周频刷新（`broad_universe_cron_wrapper.sh weekly_refresh`：broad 前 5 步；extended 第 6 步对共享 `market_db_writer` 有界等锁后提交 membership/SM/profile/coverage；**concept_weekly_sync 第 7 步**不被 step 6 失败连坐，但写库前独立非阻塞取同锁，失败只 WARN；registry 做确定性增量落库 / LLM review 队列 / CSV⇔DB lockstep 自检 / Telegram 摘要；extended 有 MIN_COUNT_FLOOR=800 保护 cache） |
| 10:00 | Sat | 基本面 + metrics 计算（`run_update_data.sh --fundamental`，加 `market_db_writer` 资源锁） |
| 10:45 | Sat | 前瞻预期更新（`run_forward_data.sh`：先 yfinance 旧线 `--forward-estimates --scope=all` ~15-22 min，再 FMP forward 新线 `update_fmp_forward.py --mode weekly`；2026-07-18 natural run：1,071 targets，FMP 79.6 min、总计 101.2 min，符合 95-105 min 实测 SLO；日志 `cron_forward_est.log`）。原 10:15 与 fundamental 并发写 market.db（2026-07-11 实测 fundamental 跑到 10:26）→ 移 10:45 留 19 min 缓冲；不并发保证来自共享 writer lock |
| 22:00/23:00 SGT | Mon-Fri | Portfolio Intelligence 推送（夏令时切换） |

**本地 launchd**: `com.finance.sync-pull` 每天 09:00 auto-pull 云端数据。

**约束**:
- PI 依赖 MarketData live quote → 单 IP 绑定云端，本地调试必须显式 `--allow-local`
- 所有 cron 走 `cron_wrapper.sh` 标准包装（统一日志 + 错误处理 + Telegram 失败告警）
- `finance_fundamental` 与 `finance_forward` 共用 `FINANCE_CRON_RESOURCE_KEY=market_db_writer` 资源锁——不并发写 market.db 的保证是锁，时钟只是缓冲；forward 另设 `FINANCE_CRON_LOCK_BUSY_RC=75`：PIT 任务锁忙即告警 + 非零退出，绝不静默跳过（漏掉的周快照补不回来）

> **forward_estimates 表 stale 策略**：周频目标 = current base + explicit overlays；退池标的**不做** stale cleanup——保留 history 作研究材料。覆盖率 verifier 将 base 与 overlay 分桶，并支持 `--min-date` 防止旧 row 误判通过。

> **FMP forward 数据线（Phase 1，2026-07 上线）**：周六顺序 = yfinance 对拍线 → 5 ETF holdings 快照 → FMP estimates/earnings → 只读 verifier。周频 universe = `current base ∪ holdings/watchlist/benchmarks ∪ 5 篮子 included symbol ∪ MAGS`；bootstrap 前带日志回退旧 Core∪Extended。writer 在逐股请求前冻结 exact denominator 到 `fmp_forward_runs`，verifier 只读 manifest。`fmp_basket_valuation` schema 已建、Phase 2 才写入。

> **价格双腿（Extended 主池迁移）**：FMP 日频额度仅用于 holdings/watchlist/benchmarks，current base 其余部分由 yfinance batch 更新；Core 退役过渡期，尚未迁入 watchlist 的 manual/analysis 票仍由 yfinance 腿兜底。06:30 pipeline 中 broad price 与该 batch 存在幂等重叠，Stop C 时结合真实耗时决定是否去重，不在代码合并阶段提前改时序。

> **晨报「0b 成交集中度」context 小节**：市场级 Top50 成交额占比 + 名单换手率的平滑值与 1 年分位 + regime 标签，报告时现算（`market.db` 只读），定位为纯 context 展示、不进策略层；研究依据见 `docs/research/2026-07-24-volume-concentration-signal-stat-study.md`。

---

## Data Flow Example: `analyze_ticker("NVDA")`

```
User 对话 / auto_deep_analyze.sh
  ↓
commands.analyze_ticker("NVDA")
  ↓
Phase 0: collect_data("NVDA")
  ├─ macro_fetcher → FRED 16 序列 → MacroSnapshot（cached）
  ├─ regime.classify() → CRISIS / RISK_OFF / ON / NEUTRAL
  ├─ macro_briefing.detect_signals() → 5 跨资产信号
  ├─ yfinance → forward_estimates + metadata
  └─ FMP → earnings calendar + insider trades + news
        ↓ DataPackage + data_context.md → research_dir
Phase 1: 写 agent prompt 到文件
  ├─ profiler_prompt.md (Company Profiler)
  ├─ lens_*.md (5 lens)
  ├─ gemini_prompt.md (Contrarian counter-thesis)
  ├─ synthesis_prompt.md
  ├─ alpha_prompt.md (Red team + cycle + bet)
  └─ alpha_debate_prompt.md (Phase 4 debate)
        ↓
Shell orchestrator 跑 ~15 claude -p agent
        ↓
compile_deep_report() → HTML + company.db
```

---

## Extension Points

| 想做的事 | 怎么做 |
|---------|--------|
| 加新 investment lens | `knowledge/philosophies/new_lens.py` 实现 `InvestmentLens` protocol |
| 加新技术指标 | `src/indicators/new.py` + `engine.py:INDICATORS` 注册 |
| 加新 FMP 端点 | `terminal/tools/fmp_tools.py` 加 tool + `pipeline.py:collect_data()` 接入 |
| 加新 yfinance dataset | 扩 `src/data/yfinance_client.py` mapper + `market_store.py` schema |
| 加新 FRED 序列 | `terminal/tools/fred_tools.py` 加 tool + 扩 `MacroSnapshot` |
| 加新跨资产信号 | `terminal/macro_briefing.py:SIGNAL_DETECTORS` 加函数 |
| 加新暴露告警 | `portfolio/exposure/alerts.py` 定义规则 |
| 加新投资主题 | `terminal/themes.py:create_theme(slug, name, thesis)` |
| 加新因子（研究） | `backtest/factor_study/factors.py` 实现 Factor protocol |
| 加新策略到 Forge | `forge/strategies/new_champion.py` + `forge/manifests/new.lock.json` |

---

## Known Traps

完整列表见 `docs/issues/`（编号制）+ `docs/postmortems/`（事后分析）+ `MEMORY.md` 反模式 section。

高频地雷：
- macOS 用 `python3` 不是 `python`
- FRED CPIAUCSL 是 raw index，需手动算 YoY%
- FRED HY spread 是 percentage points，需 ×100 显示 bp
- API 调用必须串行（client 已实现）
- VPN 劫持 GitHub DNS → 走 SSH 443 端口
- crontab 管道操作禁用 `sed | crontab -` 模式（见 issue 018）
- worktree 里空壳 market.db 会遮蔽主仓库共享数据（见 issue 019）

---

## 文档导航

| 路径 | 内容 |
|------|------|
| `docs/design/north-star.md` | 战略方向（四层金字塔 + CIO-A/B 拆分） |
| `docs/design/` | 子系统设计（company_db / options / portfolio / theme / trend tracker） |
| `docs/plans/` | 历史执行计划（按日期） |
| `docs/issues/` | 踩坑记录（编号制） |
| `docs/postmortems/` | 事后分析 |
| `docs/references/` | 外部参考（terminal-api / options 数据源 / ticker-to-thesis） |
| `docs/research/` | 研究报告（PMARP / RVOL / Breadth / 因子等） |
| `docs/CHANGELOG.md` | 项目里程碑历史 |
| `docs/audit/` | 文档审计（月度漂移检查） |

---

Built with Claude Code by Anthropic.
