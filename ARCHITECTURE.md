# Architecture — Finance Workspace

**未来资本 AI Trading Desk | Updated: 2026-02-16**

**Code Stats**: ~170+ Python files, 838 tests passing

---

## System Overview

```
╔══════════════════════════════════════════════════════════════════════════╗
║                   未来资本 AI 交易台 (17,192 lines)                      ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║  ┌──────────────── HUMAN (Boss) ─────────────────────┐                 ║
║  │  对话 → commands.py → Claude 分析 → 投资决策        │                 ║
║  └───────────────────────┬───────────────────────────┘                 ║
║                          │                                             ║
║  ════════════ TERMINAL 层 (编排中枢, 4944 lines) ═══════════           ║
║                          │                                             ║
║  ┌───────────────────────▼──────────────────────────┐                  ║
║  │  commands.py (490L) 顶层入口                      │                  ║
║  │  ├─ analyze_ticker(sym, depth)                   │                  ║
║  │  ├─ portfolio_status()                           │                  ║
║  │  ├─ position_advisor(sym, shares, price)         │                  ║
║  │  ├─ company_lookup(sym)                          │                  ║
║  │  ├─ run_monitor()                                │                  ║
║  │  └─ theme_status()                               │                  ║
║  └───────────────────────┬──────────────────────────┘                  ║
║                          │                                             ║
║  ┌───────────────────────▼──────────────────────────┐                  ║
║  │  pipeline.py (661L) 分析流水线                     │                  ║
║  │                                                   │                  ║
║  │  Stage 0: collect_data()                          │                  ║
║  │    ├─ FRED macro fetch → MacroSnapshot            │                  ║
║  │    ├─ Signal detection (5 cross-asset detectors)  │                  ║
║  │    ├─ FMP enrichment (estimates/earnings/         │                  ║
║  │    │   insider/news)                              │                  ║
║  │    └─ → DataPackage                               │                  ║
║  │                                                   │                  ║
║  │  Stage 1: macro_briefing prompt                   │                  ║
║  │  Stage 2: 6× lens prompts (→ Claude responds)    │                  ║
║  │  Stage 3: debate prompt (→ Claude responds)       │                  ║
║  │  Stage 4: memo skeleton                           │                  ║
║  │  Stage 5: score → OPRMS rating                    │                  ║
║  │  Stage 6: position sizing                         │                  ║
║  └──────┬───────────┬──────────┬─────────────────────┘                 ║
║         │           │          │                                        ║
║  ┌──────▼─────┐ ┌──▼───────┐ ┌▼─────────────────────┐                 ║
║  │macro_fetch │ │macro_brf │ │ tools/                │                 ║
║  │ (448L)     │ │ (346L)   │ │ registry (188L)       │                 ║
║  │ FRED 16系列│ │5 detctrs │ │ protocol (89L)        │                 ║
║  │ 4h/12h TTL │ │rules-only│ │ fred_tools (567L)     │                 ║
║  │ ↓          │ │no LLM    │ │ fmp_tools (536L)      │                 ║
║  │macro_snap  │ └──────────┘ │ 16 FRED + 14 FMP     │                 ║
║  │ (162L)     │              └───────────────────────┘                 ║
║  │ ↓          │                                                        ║
║  │regime.py   │ ┌────────────────────────────────────┐                 ║
║  │ (97L)      │ │ company_db.py (282L)               │                 ║
║  │ CRISIS/OFF │ │ data/companies/{SYM}/              │                 ║
║  │ ON/NEUTRAL │ │ ├─ analyses/ memos/ debates/       │                 ║
║  └────────────┘ │ ├─ strategies/ trades/             │                 ║
║                 │ └─ scratchpad/                      │                 ║
║  ┌───────────┐  └────────────────────────────────────┘                 ║
║  │monitor    │  ┌────────────────────────────────────┐                 ║
║  │ (152L)    │  │ themes.py (313L)                   │                 ║
║  │exposure   │  │ CRUD + membership + relevance      │                 ║
║  │kill/drift │  └────────────────────────────────────┘                 ║
║  └───────────┘  ┌────────────────────────────────────┐                 ║
║                 │ scratchpad.py (241L)               │                 ║
║                 │ analysis event log                  │                 ║
║                 └────────────────────────────────────┘                 ║
║                                                                        ║
║  ════════════ KNOWLEDGE 层 (投资智慧, ~2000 lines) ════════════        ║
║                                                                        ║
║  ┌─────────────┐ ┌─────────────┐ ┌───────────────┐                    ║
║  │ oprms/      │ │ debate/     │ │ memo/         │                    ║
║  │ models(137) │ │ protocol(247│ │ template(245) │                    ║
║  │ ratings(183)│ │ analyst(145)│ │ evidence(145) │                    ║
║  │ changelog   │ │ director    │ │ scorer(182)   │                    ║
║  │ integration │ │   (176)     │ └───────────────┘                    ║
║  │ (SSOT)      │ └─────────────┘                                      ║
║  └─────────────┘ ┌─────────────────────────────────┐                  ║
║                  │ philosophies/ (6 strategies)     │                  ║
║                  │ deep_value | event_driven        │                  ║
║                  │ fundamental_ls | quality_comp    │                  ║
║                  │ imaginative_growth | macro_tact  │                  ║
║                  └─────────────────────────────────┘                  ║
║                                                                        ║
║  ════════════ PORTFOLIO 层 (持仓管理, ~2000 lines) ════════════        ║
║                                                                        ║
║  ┌─────────────┐ ┌─────────────┐ ┌───────────────┐                    ║
║  │ holdings/   │ │ exposure/   │ │ benchmark/    │                    ║
║  │ manager(366)│ │ analyzer(274│ │ engine(263)   │                    ║
║  │ schema(198) │ │ alerts(234) │ │ attrib(215)   │                    ║
║  │ history(94) │ │ report(199) │ │ review(362)   │                    ║
║  └─────────────┘ └─────────────┘ └───────────────┘                    ║
║                                                                        ║
║  ════════════ DATA 层 (数据引擎, ~2400 lines) ═════════════           ║
║                                                                        ║
║  ┌──────────────────── src/ ────────────────────────┐                  ║
║  │  data/                   indicators/             │                  ║
║  │  ├─ fmp_client (250)     ├─ engine (252)         │                  ║
║  │  ├─ price_fetcher (221)  ├─ pmarp (187)          │                  ║
║  │  ├─ fundamental (413)    └─ rvol (193)           │                  ║
║  │  ├─ data_query (277)                             │                  ║
║  │  ├─ data_validator(322)  analysis/               │                  ║
║  │  ├─ dollar_volume (255)  └─ correlation (164)    │                  ║
║  │  └─ pool_manager (245)                           │                  ║
║  └──────────────────────────────────────────────────┘                  ║
║                                                                        ║
║  ════════════ STORAGE 层 ══════════════════════════════════            ║
║                                                                        ║
║  data/                                                                 ║
║  ├─ company.db            SQLite (统一公司DB: OPRMS+分析+kill)           ║
║  ├─ price/*.csv           池内股 5年日频 + SPY/QQQ                       ║
║  ├─ fundamental/*.json    利润表/资产负债表/现金流/比率/档案                 ║
║  ├─ macro/                macro_snapshot.json (FRED cache)             ║
║  ├─ companies/{SYM}/      per-ticker 分析存档                           ║
║  ├─ ratings/              OPRMS 评级历史                                ║
║  ├─ themes/               投资主题                                      ║
║  └─ pool/                 股票池配置                                     ║
║                                                                        ║
║  ════════════ EXTERNAL APIs ═══════════════════════════════            ║
║                                                                        ║
║  ┌────────────┐  ┌────────────┐  ┌─────────────────┐                  ║
║  │ FMP API    │  │ FRED API   │  │ Claude (LLM)    │                  ║
║  │ Starter $22│  │ Free       │  │ IS the analyst  │                  ║
║  │13 endpoints│  │ 16 series  │  │ 6 lenses+debate │                  ║
║  │300 call/min│  │120 req/min │  │ +memo+scoring   │                  ║
║  └────────────┘  └────────────┘  └─────────────────┘                  ║
║                                                                        ║
║  ════════════ BACKTEST 层 (回测研究, ~4,500 lines) ═══════════        ║
║                                                                        ║
║  ┌─────────── 策略回测引擎 ──────────┐ ┌─── 因子研究框架 ────────┐     ║
║  │ engine.py (219L) 核心循环         │ │ protocol.py (65L) ABC   │     ║
║  │ portfolio.py (197L) NAV跟踪       │ │ factors.py (310L) 8因子  │     ║
║  │ metrics.py (231L) Sharpe/MDD/α/β  │ │ signals.py (112L) 4信号  │     ║
║  │ rebalancer.py (144L) Top-N换仓    │ │ forward_returns.py (99L)│     ║
║  │ sweep.py (148L) 参数扫描          │ │ ic_analysis.py (217L)   │     ║
║  │ optimizer.py (380L) Walk-Forward  │ │ event_study.py (126L)   │     ║
║  │ report.py (290L) HTML/CSV         │ │ sweep.py (169L) 参数网格│     ║
║  │ config.py (152L) 含FactorStudy    │ │ runner.py (195L) 编排   │     ║
║  │                                   │ │ report.py (389L) HTML   │     ║
║  │ adapters/                         │ └────────────────────────┘     ║
║  │  us_stocks.py (185L)              │                                ║
║  │  crypto.py (200L)                 │ 双轨分析:                      ║
║  │  crypto_rs.py (201L)              │  Track 1: IC (连续预测力)       ║
║  └───────────────────────────────────┘  Track 2: 事件研究 (信号检验)   ║
║                                                                        ║
║  ════════════ INFRA ═══════════════════════════════════════            ║
║                                                                        ║
║  ┌────────────┐  ┌────────────┐  ┌─────────────────┐                  ║
║  │ Cloud      │  │ Tests      │  │ Obsidian        │                  ║
║  │ aliyun cron│  │ 20 files   │  │ Cards/ 分析摘要   │                  ║
║  │ price+scan │  │ 838 pass   │  │ Journal/ 日志     │                  ║
║  │ daily 06:30│  │ 4,445 lines│  │                 │                  ║
║  └────────────┘  └────────────┘  └─────────────────┘                  ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════════╝
```

**Core Principle**: Claude IS the analyst. The system generates structured prompts with data context, Claude responds with insights, and results are stored for reuse.

---

## Data Flow: `analyze_ticker("NVDA", depth="full")`

```
User 对话
  │
  ▼
commands.analyze_ticker("NVDA", "full")
  │
  ├── Stage 0: collect_data("NVDA") ───────────────────────────────┐
  │     ├─ macro_fetcher → FRED 16 series → MacroSnapshot (cached) │
  │     ├─ regime.classify() → CRISIS / RISK_OFF / ON / NEUTRAL    │
  │     ├─ macro_briefing.detect_signals() → 5 cross-asset signals │
  │     ├─ fmp_tools.get_analyst_estimates("NVDA")                 │
  │     ├─ fmp_tools.get_earnings_calendar("NVDA")                 │
  │     ├─ fmp_tools.get_insider_trades("NVDA")                    │
  │     └─ fmp_tools.get_stock_news("NVDA")                        │
  │     → DataPackage { macro, signals, estimates, earnings, ... } │
  │                                                                 │
  ├── Stage 1: generate_briefing_prompt(signals, snapshot)          │
  │     → Claude generates macro narrative                          │
  │                                                                 │
  ├── Stage 2: prepare_lens_prompts(DataPackage)                    │
  │     → 6 lens prompts (each investment philosophy)               │
  │     → Claude answers each sequentially                          │
  │                                                                 │
  ├── Stage 3: debate prompt (Bull vs Bear)                         │
  │     → Claude simulates 5-round adversarial debate               │
  │                                                                 │
  ├── Stage 4: prepare_memo_skeleton()                              │
  │     → Investment memo (9 buckets)                               │
  │                                                                 │
  ├── Stage 5: score → OPRMS rating (DNA + Timing)                  │
  │                                                                 │
  └── Stage 6: calculate_position()                                 │
        → Total × DNA_cap × Timing × regime_mult × evidence_gate   │
        → Final position recommendation                             │
```

---

## Layer Details

### Layer 1: Terminal (Orchestration) — 4,944 lines

**Location**: `terminal/` (16 files)

The orchestration layer. Every user-facing function lives here.

| File | Lines | Purpose |
|------|-------|---------|
| `commands.py` | 490 | Top-level entry points Claude calls |
| `pipeline.py` | 661 | Multi-stage analysis workflow (data→prompt→score→OPRMS) |
| `macro_fetcher.py` | 448 | FRED 16-series fetch, 4h/12h cache, derived values |
| `macro_briefing.py` | 346 | 5 cross-asset signal detectors (carry unwind, credit stress, liquidity drain, reflation, risk rally) |
| `macro_snapshot.py` | 162 | MacroSnapshot dataclass (33+ fields incl trends) |
| `themes.py` | 313 | Investment theme CRUD + membership + relevance |
| `company_db.py` | 282 | Per-ticker file storage at `data/companies/{SYM}/` |
| `scratchpad.py` | 241 | Analysis event log for debugging |
| `monitor.py` | 152 | Portfolio sweep (exposure, kill, drift, staleness) |
| `regime.py` | 97 | Decision tree: VIX/curve/GDP/HY → CRISIS/RISK_OFF/ON/NEUTRAL |
| `__init__.py` | 9 | Public exports |
| **tools/registry.py** | 188 | Tool discovery + execution engine |
| **tools/fred_tools.py** | 567 | 16 FRED tool definitions |
| **tools/fmp_tools.py** | 536 | 14 FMP tool definitions |
| **tools/protocol.py** | 89 | Tool protocol (ToolResult dataclass) |
| **tools/__init__.py** | 120 | Tool exports |

#### Key Commands (`commands.py`)

| Command | Purpose | Depth |
|---------|---------|-------|
| `analyze_ticker(sym, depth)` | Full analysis pipeline | quick/standard/full |
| `portfolio_status()` | Holdings + exposure alerts + company DB coverage | — |
| `position_advisor(sym, shares, price)` | OPRMS-based position sizing | — |
| `company_lookup(sym)` | Everything in company DB for a ticker | — |
| `run_monitor()` | Full portfolio health sweep | — |
| `theme_status(slug)` | Investment theme membership | — |

#### Macro Pipeline

```
FRED API (16 series)
  ├─ DGS2/5/10/30          Yield curve
  ├─ T10Y2Y, T10Y3M        Curve spreads
  ├─ FEDFUNDS               Fed funds rate
  ├─ CPIAUCSL               CPI index (YoY% computed manually)
  ├─ GDP                    Real GDP growth
  ├─ UNRATE                 Unemployment
  ├─ VIXCLS                 VIX
  ├─ BAMLH0A0HYM2           HY spread (×100 for bp display)
  ├─ DTWEXBGS               Dollar index
  ├─ DEXJPUS                USD/JPY
  ├─ IRSTCI01JPM156N        Japan 10Y
  └─ WALCL                  Fed balance sheet
          │
          ▼
  MacroSnapshot (33+ fields)
          │
     ┌────┴────┐
     ▼         ▼
  regime    signal detectors (5)
  classify  carry_trade_unwind
  ────────  credit_stress
  VIX>45    liquidity_drain
  →CRISIS   reflation
  ...       risk_rally
```

**Cache**: `data/macro/macro_snapshot.json`, TTL 4h (trading) / 12h (non-trading)

**Regime Decision Tree**:
- VIX > 45 → CRISIS
- VIX > 35 + curve inversion → CRISIS
- VIX > 25 + curve inversion → RISK_OFF
- GDP < 0 → RISK_OFF
- HY spread > 5% → RISK_OFF
- VIX < 18 + curve > 0.5 + GDP > 2 → RISK_ON
- else → NEUTRAL

**Position Sizing Multiplier**: RISK_OFF ×0.7, CRISIS ×0.4

#### Tool Registry (`tools/`)

Protocol-based tool system. Each tool is a function with metadata:

```python
@tool(name="get_treasury_yields", category="fred")
def get_treasury_yields() -> ToolResult:
    """Fetch current yield curve from FRED."""
    ...
```

| Category | Count | Examples |
|----------|-------|---------|
| FRED | 16 | treasury yields, VIX, CPI, GDP, unemployment, HY spread |
| FMP | 14 | analyst estimates, earnings calendar, insider trades, stock news, profile, financials |

---

### Layer 2: Knowledge Desk — ~2,000 lines

**Location**: `knowledge/` (15 files, 4 subsystems)

Investment frameworks, rating systems, and analysis methodologies. Contains domain knowledge, not market data.

#### OPRMS (Single Source of Truth)

**`knowledge/oprms/models.py`** — imported by portfolio/ and risk/ modules.

```
DNA Rating (Asset Quality)         Timing Rating (Entry Quality)
─────────────────────────          ──────────────────────────────
S 圣杯  → max 20-25%               S 千载难逢 → coeff 1.0-1.5
A 猛将  → max 15%                  A 趋势确立 → coeff 0.8-1.0
B 黑马  → max 7%                   B 正常波动 → coeff 0.4-0.6
C 跟班  → max 2%                   C 垃圾时间 → coeff 0.1-0.3

Position = Total × DNA_cap × Timing_coeff × regime_mult
Evidence gate: <3 sources → proportional scaling
```

| File | Lines | Purpose |
|------|-------|---------|
| `models.py` | 137 | DNARating, TimingRating, OPRMSRating dataclasses |
| `ratings.py` | 183 | `calculate_position_size()` |
| `changelog.py` | 135 | OPRMS history tracking |
| `integration.py` | 143 | Hooks for portfolio desk |

#### 6-Lens Analysis Framework (`philosophies/`)

| Lens | File | Focus |
|------|------|-------|
| Deep Value | `deep_value.py` | Margin of safety, asset-backed |
| Event Driven | `event_driven.py` | Catalysts, special situations |
| Fundamental L/S | `fundamental_ls.py` | Earnings quality, peer comparison |
| Quality Compounder | `quality_compounder.py` | Moats, ROIC, compounding |
| Imaginative Growth | `imaginative_growth.py` | TAM expansion, vision |
| Macro Tactical | `macro_tactical.py` | Regime, sector rotation |

Each lens implements `InvestmentLens` protocol from `base.py` (65 lines).

#### Debate Protocol (`debate/`)

| File | Lines | Purpose |
|------|-------|---------|
| `protocol.py` | 247 | 5-round Bull/Bear adversarial debate structure |
| `analyst_rules.py` | 145 | Rules each analyst role must follow |
| `director_guide.py` | 176 | Meta-prompts for identifying tensions |

#### Memo System (`memo/`)

| File | Lines | Purpose |
|------|-------|---------|
| `template.py` | 245 | 9-bucket memo skeleton |
| `evidence.py` | 145 | Evidence classification and quality gates |
| `scorer.py` | 182 | Completeness + writing quality scoring (target > 7.0/10) |

---

### Layer 3: Portfolio Desk — ~2,000 lines

**Location**: `portfolio/` (10 files, 3 subsystems)

| Subsystem | Files | Key Classes | Status |
|-----------|-------|-------------|--------|
| **holdings/** | manager(366), schema(198), history(94) | `HoldingsManager`, `Position` | Code ready, awaiting real data |
| **exposure/** | analyzer(274), alerts(234), report(199) | `ExposureAnalyzer`, `AlertRule` | Code ready |
| **benchmark/** | engine(263), attribution(215), review(362) | `BenchmarkEngine`, `Attribution` | Code ready |

**Holdings Model**:
```
portfolio/holdings/
├─ manager.py     CRUD for positions
├─ schema.py      Position dataclass (symbol, qty, cost, entry_date)
└─ history.py     Historical snapshots
```

**Exposure Alerts**: Single position > DNA max, sector > 40%, total > 95%, correlation cluster risk.

**Benchmark**: SPY/QQQ relative performance, attribution by sector/theme/DNA tier.

---

### Layer 4: Data Desk — ~2,400 lines

**Location**: `src/` (10 files), `scripts/` (8 files), `config/` (1 file)

#### Data Pipeline (`src/data/`)

| File | Lines | Purpose |
|------|-------|---------|
| `fmp_client.py` | 250 | FMP API wrapper, 2s rate limit |
| `fundamental_fetcher.py` | 413 | Financial statements fetch + store |
| `data_validator.py` | 322 | Schema validation + quality checks |
| `data_guardian.py` | 215 | Snapshot backup/restore + retention policy (max 10) |
| `data_health.py` | 321 | Full-chain health check (8 checks: pool/coverage/freshness/consistency) |
| `data_query.py` | 277 | Unified `get_stock_data(symbol)` interface |
| `dollar_volume.py` | 255 | Liquidity ranking system |
| `pool_manager.py` | 363 | Stock pool management + auto-admission + stale data cleanup + safety fuse (30%) |
| `price_fetcher.py` | 221 | OHLCV price data fetch + CSV storage |

#### Technical Indicators (`src/indicators/`)

| File | Lines | Purpose |
|------|-------|---------|
| `engine.py` | 252 | Indicator orchestration (`run_indicators()`) |
| `pmarp.py` | 187 | Price momentum percentile (>98% = strong trend) |
| `rvol.py` | 193 | Relative volume (>4σ = anomaly) |

#### Analysis Engines (`src/analysis/`)

| File | Lines | Purpose |
|------|-------|---------|
| `correlation.py` | 164 | Pairwise return correlation matrix, cached at `data/correlation/matrix.json` |

#### Configuration (`config/`)

```python
# config/settings.py
MARKET_CAP_THRESHOLD = 100_000_000_000  # $100B
BENCHMARK_SYMBOLS = ["SPY", "QQQ"]
# 过滤策略: EXCLUDED_SECTORS + EXCLUDED_INDUSTRIES + PERMANENTLY_EXCLUDED (排除法)
```

#### Operations Scripts (`scripts/`)

| Script | Purpose |
|--------|---------|
| `update_data.py` | Price + fundamental updates (--price / --all / --check) + auto health check |
| `scan_indicators.py` | Run indicators on all stocks |
| `daily_scan.py` | Daily automated scan |
| `collect_dollar_volume.py` | Dollar volume ranking |
| `backfill_dollar_volume.py` | Historical backfill |
| `check_fmp_api.py` | API connectivity test |

---

### Layer 5: Storage

```
data/
├── company.db                    SQLite — unified company DB (OPRMS, analyses, kill conditions)
├── price/*.csv                   池内股 × 5yr daily OHLCV + SPY/QQQ benchmark
├── fundamental/
│   ├── income.json               Income statements
│   ├── balance_sheet.json        Balance sheets
│   ├── cash_flow.json            Cash flow statements
│   ├── ratios.json               Financial ratios
│   └── profiles.json             Company profiles
├── macro/
│   └── macro_snapshot.json       FRED cache (4h/12h TTL)
├── companies/{SYMBOL}/           Per-ticker knowledge store
│   ├── oprms.json                Current OPRMS rating
│   ├── oprms_history.jsonl       Rating changelog (append-only)
│   ├── kill_conditions.json      Invalidation triggers
│   ├── memos.jsonl               Investment memos (timestamped)
│   ├── analyses.jsonl            Full analysis results
│   ├── scratchpad/               Debug logs
│   └── meta.json                 Theme memberships, tags
├── ratings/                      OPRMS rating snapshots
├── themes/{slug}.json            Investment themes
├── pool/                         Stock pool configs
├── correlation/matrix.json       Pairwise correlation cache
└── .backups/                     Data snapshots (tar.gz, max 10)
```

**Data Formats**:
- **JSON**: Single-record data (oprms.json, meta.json)
- **JSONL**: Append-only logs (memos.jsonl, analyses.jsonl)
- **CSV**: Tabular time series (price data)
- **SQLite**: Queryable aggregates (company.db)

**Data Hygiene**: `cleanup_stale_data()` runs automatically after pool refresh — removes stale price CSVs and fundamental JSON entries for exited stocks. Safety fuse aborts if >30% of data would be deleted. Auto-snapshot before any deletion.

**Data Guardian**: `data_guardian.py` provides snapshot/restore for `data/` (price, fundamental, pool, company.db). Max 10 snapshots at `data/.backups/`. Triggered automatically before cleanup and available manually.

**Health Check**: `data_health.py` runs 8 checks (pool integrity, price/fundamental coverage, freshness, consistency, company.db). Embedded in `update_data.py` (post-update + `--check`) and sync scripts (pre-push, post-pull). Returns PASS/WARN/FAIL.

---

### Layer 6: Backtest Desk — ~4,500 lines

**Location**: `backtest/` (20 files, 2 subsystems)

两个独立但互补的框架，解决不同层次的问题：

#### 框架 A: 策略回测引擎 (`backtest/`)

**目的**: "这个策略赚不赚钱？" — 给定选股规则 + 换仓频率 → 模拟持仓 → 计算绩效

```
数据加载 → 逐日循环:
  slice_to_date(t) → RS排名 → Top-N选股 → Rebalancer换仓
  → PortfolioState更新(NAV/持仓/成本) → 下一日
→ compute_metrics() → Sharpe/CAGR/MaxDD/α/β
→ ParameterSweep → 参数网格扫描 → 最优组合
→ WalkForwardOptimizer → 滚动窗口验证
```

| 文件 | 行数 | 职能 |
|------|------|------|
| `engine.py` | 219 | 回测核心循环（防前视: slice→rank→trade→next） |
| `portfolio.py` | 197 | PortfolioState: NAV 跟踪、持仓管理、交易成本 |
| `metrics.py` | 231 | 20+ 指标: Sharpe/Sortino/Calmar/α/β/IR/MaxDD |
| `rebalancer.py` | 144 | Top-N 换仓 + sell_buffer 缓冲 |
| `sweep.py` | 148 | 参数网格扫描 (rs_method × top_n × freq × buffer) |
| `optimizer.py` | 380 | Walk-Forward 优化 (滚动训练/验证窗口) |
| `report.py` | 290 | HTML 报告 (暗色主题 + Chart.js 净值曲线) |
| `config.py` | 152 | BacktestConfig + FactorStudyConfig 配置类 |
| `adapters/us_stocks.py` | 185 | 美股 CSV 加载 + 日期切片 |
| `adapters/crypto.py` | 200 | 币安合约 CSV 加载 (兼容 timestamp/open_time) |
| `adapters/crypto_rs.py` | 201 | 币圈 RS 纯计算 (B: Z-Score, C: Clenow, 短周期 7d/3d/1d) |

**关键参数**:

| 市场 | RS 方法 | Top-N | 换仓频率 | 成本 | 基准 |
|------|---------|-------|----------|------|------|
| 美股 | B/C | 5-20 | W/2W/M | 5bps | SPY |
| 币圈 | B/C | 5-20 | D/3D/W | 4bps | BTCUSDT |

#### 框架 B: 因子有效性研究 (`backtest/factor_study/`)

**目的**: "这个因子有没有预测力？" — 先验证因子，再构建策略

**方法论**: 不做交易模拟，直接统计检验

```
数据加载(一次) → 逐日切片(防前视) → 计算因子分数 → 积累 score_history
                                                        │
                                     ┌──────────────────┴──────────────────┐
                                     ▼                                     ▼
                              Track 1: IC 分析                      Track 2: 事件研究
                              ──────────────                        ──────────────
                              每天 Spearman(分数, 收益)              定义信号规则
                              → IC 时间序列                          → 检测事件日期
                              → Mean IC / IC_IR                     → 收集事件后收益
                              → 分位数单调性                         → t-test (H0: mean=0)
                              → IC 衰减曲线                          → hit rate / p-value
```

| 文件 | 行数 | 职能 |
|------|------|------|
| `protocol.py` | 65 | Factor ABC + FactorMeta (统一接口) |
| `factors.py` | 310 | 8 个因子适配器 (RS_B/C × 美股/币圈, PMARP, RVOL, DV, RVOL_Sustained) |
| `signals.py` | 112 | 4 种信号类型 (threshold / cross_up / cross_down / sustained) |
| `forward_returns.py` | 99 | 前向收益矩阵 (评估用, 合法使用完整数据) |
| `ic_analysis.py` | 217 | Track 1: IC / IC_IR / 分位数收益 / IC 衰减曲线 |
| `event_study.py` | 126 | Track 2: 事件研究 (mean return / hit rate / t-stat / p-value) |
| `sweep.py` | 169 | 每因子默认参数网格 + 自定义扫描 |
| `runner.py` | 195 | 编排器 (数据加载→逐日计算→双轨分析) |
| `report.py` | 389 | 文本 + HTML (IC 衰减曲线 + 分位数柱状图 + 事件统计表) + CSV |

**已注册因子**:

| 因子 | 分数含义 | 范围 | 方向 | 市场 |
|------|---------|------|------|------|
| RS_Rating_B | Z-Score 横截面动量排名 | 0-99 | 高=强 | 美股 |
| RS_Rating_C | Clenow 回归动量排名 | 0-99 | 高=强 | 美股 |
| Crypto_RS_B | Z-Score 短周期 (7d/3d/1d) | 0-99 | 高=强 | 币圈 |
| Crypto_RS_C | Clenow 短周期 (7d/3d/1d) | 0-99 | 高=强 | 币圈 |
| PMARP | 价格动量百分位 | 0-100 | 高=强 | 美股 |
| RVOL | 相对成交量 (σ) | -5~10 | 高=强 | 美股 |
| DV_Acceleration | 美元交易量 5d/20d 加速比 | 0-5 | 高=强 | 美股 |
| RVOL_Sustained | 持续放量天数 | 0-30 | 高=强 | 美股 |

**四种信号类型**:

| 类型 | 规则 | 示例 |
|------|------|------|
| threshold | score > X | RS > 90 |
| cross_up | 前期 ≤ X, 本期 > X | RS 从 85 突破 90 |
| cross_down | 前期 ≥ X, 本期 < X | RS 跌破 20 |
| sustained | 连续 N 期 > X (去重) | RS > 80 持续 5 周 |

**两个框架的关系**:
```
因子研究 (Factor Study)          策略回测 (Backtest Engine)
━━━━━━━━━━━━━━━━━━━              ━━━━━━━━━━━━━━━━━━━━━
回答: 因子有没有预测力？           回答: 这个策略赚不赚钱？
方法: IC + 事件研究               方法: 模拟持仓 + 绩效计算
输出: p-value, IC_IR              输出: Sharpe, CAGR, MaxDD
       ↓                                ↑
  验证因子有效 ──────────────────→ 用有效因子构建策略
```

---

### Layer 7: Skeleton Modules (Built, Awaiting Activation)

| Module | Directory | Status |
|--------|-----------|--------|
| **Risk Desk** | `risk/rules/`, `risk/sizing/` | IPS in markdown, code rules pending |
| **Trading Desk** | `trading/journal/`, `trading/strategies/`, `trading/review/` | Templates exist, no live trades |
| **Reports** | `reports/` | Historical research reports |

---

## External Dependencies

### APIs

| API | Plan | Rate Limit | Endpoints Used | Cost |
|-----|------|-----------|----------------|------|
| FMP | Starter | 300/min | 13 (profile, financials, estimates, earnings, insider, news, ...) | $22/mo |
| FRED | Free | 120/min | 16 macro series | Free |
| Claude | — | — | 6 lenses + debate + memo + scoring per analysis | ~$13-15/full analysis |

### FMP Endpoints (13 active)

1. Stock screener (market cap, sector filter)
2. Company profile
3. Key metrics (P/E, ROE, margins)
4. Income statement (quarterly/annual)
5. Balance sheet
6. Cash flow
7. Financial ratios
8. Historical price (5 years daily)
9. Quote (latest price)
10. **Analyst estimates** (NEW)
11. **Earnings calendar** (NEW)
12. **Insider trades** (NEW)
13. **Stock news** (NEW)

**NOT available on FMP Starter**: Options chain, Greeks, bonds, Level 2 data.

### Python Dependencies

```
numpy>=2.4          # Numerical computing (indicators, correlation)
pandas>=3.0         # Data manipulation (price, financials)
requests>=2.32      # HTTP client (FMP + FRED APIs)
python-dateutil>=2.9 # Date parsing
python-dotenv       # Environment variables
```

---

## Cloud Deployment

| Item | Value |
|------|-------|
| Server | Aliyun ECS (Beijing) |
| SSH | `ssh aliyun` |
| Path | `/root/workspace/Finance/` |
| Sync | `./sync_to_cloud.sh [--code\|--data\|--all]` |

### Cron Jobs (Beijing Time, Tue-Sat)

| Time | Task | Log |
|------|------|-----|
| 06:30 | Price data update | `cron_price.log` |
| 06:45 | Dollar volume scan | `cron_scan.log` |
| Sat 08:00 | Stock pool refresh + stale data cleanup | `cron_pool.log` |
| Sat 10:00 | Fundamentals update | `cron_fundamental.log` |

---

## Code Stats by Layer

| Layer | Files | Lines | % of Total |
|-------|-------|-------|-----------|
| **terminal/** (orchestration) | 16 | 4,944 | 29% |
| **tests/** | 11 | 2,783 | 16% |
| **knowledge/** (investment frameworks) | 15 | ~2,000 | 12% |
| **portfolio/** (holdings + exposure) | 10 | ~2,000 | 12% |
| **src/** (data + indicators + analysis) | 10 | ~2,400 | 14% |
| **scripts/** (operations) | 8 | ~1,100 | 6% |
| **config/** | 1 | 92 | 1% |
| Other (risk/, trading/ skeletons) | ~14 | ~1,800 | 10% |
| **Total** | **~85** | **17,192** | 100% |

---

## Performance Characteristics

| Operation | Time | Cost | Cache |
|-----------|------|------|-------|
| FRED macro fetch | 2-3s | $0 | 4h/12h TTL |
| Signal detection (5 detectors) | <1ms | $0 | — |
| FMP enrichment (4 endpoints) | 6-8s | $0 | 24h TTL |
| Indicator calculation | <1s | $0 | On-demand |
| Prompt generation | <1s | $0 | — |
| Claude 6-lens analysis | 30-60s | ~$2 | — |
| Claude debate (5 rounds) | 2-3min | ~$5-8 | — |
| Claude memo writing | 1-2min | ~$3-5 | — |
| Memo scoring | <1s | $0 | — |
| Company DB storage | <1s | $0 | — |

**Full analysis total**: ~5 min, ~$13-15 per ticker

---

## Extension Points

| Want to... | Do this |
|------------|---------|
| Add new investment lens | Create `knowledge/philosophies/new_lens.py` implementing `InvestmentLens` protocol |
| Add new technical indicator | Create `src/indicators/new.py`, register in `engine.py:INDICATORS` |
| Add new FMP endpoint | Add tool in `terminal/tools/fmp_tools.py`, wire into `pipeline.py:collect_data()` |
| Add new FRED series | Add tool in `terminal/tools/fred_tools.py`, extend `MacroSnapshot` fields |
| Add new signal detector | Add function in `terminal/macro_briefing.py:SIGNAL_DETECTORS` |
| Add new exposure alert | Define rule in `portfolio/exposure/alerts.py` |
| Add new investment theme | Call `terminal/themes.py:create_theme(slug, name, thesis)` |

---

## Build History

| Milestone | Date | Delta |
|-----------|------|-------|
| Phase 1: Valuation → Finance merge + Desk skeleton | 2026-02-06 | — |
| Phase 2 P0: 4 desks built (92 files, 9006 lines) | 2026-02-07 | +9,006 |
| Terminal layer (7 files, 1462 lines) | 2026-02-07 | +1,462 |
| FRED Macro Pipeline (42 files, 103 tests) | 2026-02-09 | +9,837 |
| P0 FMP Enrichment (13 new tests) | 2026-02-09 | +137 |
| P1 Benchmark + Correlation (14 new tests, 130 total) | 2026-02-09 | +397 |
| Macro Briefing Layer (40 new tests, 179 total) | 2026-02-09 | +773 |
| Alpha Layer (L2) + Data Freshness + Deep Pipeline v2 | 2026-02-10 | +4,600 |
| HTML Reports + Agent-ization + Slim Context | 2026-02-11 | +2,800 |
| Unified Company DB (SQLite + Dashboard + Migration) | 2026-02-13 | +2,300 |
| Pool Cleanup (stale data auto-removal) | 2026-02-15 | cleanup |
| Data Guardian (snapshot/fuse/health check) | 2026-02-15 | +1,157 |
| **Current** | 2026-02-15 | **678 tests** |

---

## Known Traps

| Trap | Workaround |
|------|-----------|
| `.bashrc` non-interactive shell | Use `.env` file |
| `.gitignore` `/data/` vs `data/` | Use `/data/` for root only |
| FMP Screener returns ~976, not 3000+ | Doesn't affect Top 200 quality |
| FRED CPIAUCSL is raw index | Compute YoY% manually: `index[0]/index[12]-1` |
| FRED BAMLH0A0HYM2 is percentage points | Display `×100` for basis points |
| API calls must be serial | 2s interval enforced in client |
| Mock patch for runtime imports | Patch at source module, not caller |
| macOS uses `python3` not `python` | Always use `python3` in scripts |
| VPN hijacks DNS for GitHub | SSH via port 443: `ssh.github.com:443` |

---

## Glossary

| Term | Definition |
|------|-----------|
| **OPRMS** | Owen's Position & Risk Management System (DNA × Timing → Position) |
| **DNA Rating** | Asset quality tier (S/A/B/C) determining max position size |
| **Timing Rating** | Entry quality tier (S/A/B/C) determining size coefficient |
| **Kill Condition** | Observable trigger for position invalidation |
| **DataPackage** | Aggregated ticker data (fundamentals, price, indicators, macro, enrichment) |
| **Investment Lens** | Analytical perspective (6 philosophies) |
| **Debate Protocol** | 5-round adversarial Bull vs Bear analysis |
| **Company DB** | Per-ticker knowledge storage (`data/companies/{SYMBOL}/`) |
| **MacroSnapshot** | Point-in-time snapshot of 16 FRED macro indicators |
| **Regime** | Market regime classification (CRISIS/RISK_OFF/RISK_ON/NEUTRAL) |
| **Signal Detector** | Rule-based cross-asset signal (carry unwind, credit stress, etc.) |
| **Desk** | Functional domain (Data, Research, Risk, Trading, Portfolio, Knowledge) |

---

Built with Claude Code by Anthropic.
