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

> **Concept Registry** 详见 `docs/plans/2026-04-28-company-concept-registry-phase1.md`
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

**池子分层**:
- 核心池: `data/pool/universe.json`（FMP screener，市值阈值见 `config/settings.py`）
- 扩展池: `data/extended_universe/`（FMP screener $10B+，~533 只，yfinance batch 价格）
- 池外广扫: `data/scans/broad_universe.json`（yfinance screener $5B+ RVOL 扫描）
- 退市 overlay: `data/pool/delisted_large_caps.json`（true survivorship 用，~21 只，独立 backfill）

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
| `market.db` | 云端独占写入 | daily_price, income/BS/CF quarterly, ratios, metrics_quarterly, iv_daily, options_snapshots, forward_estimates/metadata, social_sentiment, market_sentiment, social_trending(*), historical_market_cap, broad_scan_hits, concepts(*) | pull 到本地 |
| `company.db` | 本地独占写入 | companies, oprms_ratings, analyses, kill_conditions, holdings, transactions, portfolio_cash, option_positions, option_transactions | push 到云端 |
| `universe.json` | 双端 | 股票池定义 | 双向 merge（并集） |
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
| 07:30 | Wed | 扩展池历史市值采集（`broad_universe_cron_wrapper.sh daily_hmcap`） |
| 08:00 | Tue-Sat | 晨报生成与推送（`run_market_report_pipeline.sh`） |
| 08:30 | Sat | 股票池刷新（`run_update_data.sh --pool`） |
| 09:00 | Sat | 扩展池周频刷新（`broad_universe_cron_wrapper.sh weekly_refresh`） |
| 10:00 | Sat | 基本面 + metrics 计算（`run_update_data.sh --fundamental`） |
| 10:15 | Sat | 前瞻预期更新（核心 + 扩展池 ~563 unique，`run_update_data.sh --forward-estimates --scope=all`，~9.4 min，日志 `cron_forward_est.log`） |
| 22:00/23:00 SGT | Mon-Fri | Portfolio Intelligence 推送（夏令时切换） |

**本地 launchd**: `com.finance.sync-pull` 每天 09:00 auto-pull 云端数据。

**约束**:
- PI 依赖 MarketData live quote → 单 IP 绑定云端，本地调试必须显式 `--allow-local`
- 所有 cron 走 `cron_wrapper.sh` 标准包装（统一日志 + 错误处理 + Telegram 失败告警）

> **forward_estimates 表 stale 策略**：跟随核心 + 扩展池 weekly 刷新；退池标的**不做** stale cleanup——保留 history 作研究材料。覆盖率验证用 `scripts/verify_forward_coverage.py --min-date <本次 cron 日期>`，避免旧 row 误判通过。

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
