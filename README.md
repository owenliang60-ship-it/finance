# Finance — 未来资本 AI Trading Desk

**AI-powered institutional-grade investment infrastructure for personal portfolio management.**

Manage a multi-million dollar US equity portfolio with professional research, risk monitoring, and trading discipline — powered by Claude and the Desk Model.

**Code Stats**: ~167 Python files | 1,285 tests passing | 36,800+ lines

---

## Architecture Overview

```
╔══════════════════════════════════════════════════════════════════╗
║            未来资本 AI Trading Desk — System Map                  ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Boss (Human)                                                    ║
║    │                                                             ║
║    ▼                                                             ║
║  ┌─────────── Terminal (编排中枢) ──────────────┐                ║
║  │  5-lens analysis │ debate │ OPRMS │ alpha    │                ║
║  │  macro pipeline  │ signal detection          │                ║
║  └────┬─────────┬──────────┬───────────┬────────┘                ║
║       │         │          │           │                         ║
║  ┌────▼───┐ ┌──▼────┐ ┌──▼─────┐ ┌──▼──────┐                   ║
║  │Knowledge│ │  Data │ │Backtest│ │ Options │                    ║
║  │OPRMS    │ │FMP+   │ │RS rank │ │IV track │                    ║
║  │6 lens   │ │FRED+  │ │factor  │ │BS solver│                    ║
║  │debate   │ │MktData│ │study   │ │24 plays │                    ║
║  │alpha    │ │       │ │        │ │         │                    ║
║  └─────────┘ └───┬───┘ └────────┘ └─────────┘                   ║
║                  │                                               ║
║  ┌───────────────▼──────────────────────────────┐                ║
║  │              Storage Layer                    │                ║
║  │  market.db (cloud-owned) │ company.db (local) │                ║
║  │  price+fundamental+IV    │ OPRMS+analysis     │                ║
║  └──────────────────────────────────────────────┘                ║
║                                                                  ║
║  ┌──────────── Cloud (Aliyun) ──────────────────┐                ║
║  │  Daily cron: price 06:30 │ IV 06:50           │                ║
║  │  Weekly: pool 08:00 │ fundamental+metrics 10:00│               ║
║  │  Auto git pull 06:25 │ launchd pull 09:00     │                ║
║  └───────────────────────────────────────────────┘                ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## The Desk Model

| Desk | Directory | Function | Status |
|------|-----------|----------|--------|
| **Data** | `src/`, `data/`, `scripts/` | Market data collection, storage, validation | LIVE |
| **Terminal** | `terminal/` | Orchestration, analysis pipelines, macro engine | LIVE |
| **Knowledge** | `knowledge/` | OPRMS rating, 6-lens framework, debate, alpha | LIVE |
| **Backtest** | `backtest/` | RS ranking engine, factor study framework | LIVE |
| **Options** | `terminal/options/` | IV tracking, chain analysis, BS solver, 24 playbooks | LIVE |
| **Portfolio** | `portfolio/` | Holdings, exposure analysis, attribution | Code ready |
| **Risk** | `risk/` | IPS, exposure monitoring, kill conditions | Skeleton |
| **Trading** | `trading/` | Trade logs, strategy library | Skeleton |

---

## Data Infrastructure

### Dual-Database Architecture (P3 Ownership Model)

Each database has a single owner — sync is always one-way copy, never conflict.

| Database | Owner | Contents | Sync |
|----------|-------|----------|------|
| **market.db** (31 MB) | Cloud | daily_price, income/BS/CF quarterly, ratios, metrics_quarterly, iv_daily, options_snapshots | Cloud → Local (pull) |
| **company.db** (3.4 MB) | Local | companies, oprms_ratings, analyses, kill_conditions, situation_summary | Local → Cloud (push) |
| **universe.json** | Both | Stock pool definitions | Bidirectional merge (union) |

### Data Sources

| Source | Data | Plan |
|--------|------|------|
| **FMP API** | Fundamentals, price, estimates, analyst grades, insider, news | Starter ($22/mo) |
| **FRED API** | 16 macro series (yields, CPI, VIX, HY spread, etc.) | Free |
| **MarketData.app** | Options chains, IV history | Starter ($12/mo) |

### Stock Pool
- **Universe**: 145 US large-cap equities (market cap > $100B)
- **Exchanges**: NYSE + NASDAQ
- **Excluded**: Consumer Defensive, Energy, Utilities, Basic Materials, Real Estate
- **Refresh**: Weekly (Saturday 08:00 cloud cron)

### Cloud Deployment

| Item | Value |
|------|-------|
| Server | Aliyun ECS (Beijing) |
| SSH | `ssh aliyun` |
| Path | `/root/workspace/Finance/` |
| Sync | `./sync_to_cloud.sh [--pull\|--push\|--sync\|--status]` |
| Auto-pull | macOS launchd daily 09:00 |
| Auto-push | After deep analysis batch (`auto_deep_analyze.sh` Phase 5) |

**Cron Schedule (Beijing Time)**:

| Time | Task | Frequency |
|------|------|-----------|
| 06:25 | Git auto-pull (code deploy) | Daily |
| 06:30 | Price data update | Tue-Sat |
| 06:45 | Dollar volume scan | Tue-Sat |
| 06:50 | IV data update | Tue-Sat |
| 08:00 | Stock pool refresh | Saturday |
| 10:00 | Fundamental + metrics computation | Saturday |

---

## Deep Analysis Pipeline

Multi-agent orchestration via `scripts/auto_deep_analyze.sh` (~25 min/ticker):

| Phase | What | Agents | Model |
|-------|------|--------|-------|
| **0** | Data collection + prompt generation | Python setup | — |
| **0b** | Research: earnings, competitive, street, contrarian, profiler | 5 parallel | Sonnet |
| **1** | 5-lens analysis (quality, growth, value, event, macro) | 5 parallel | Opus |
| **2** | Synthesis: debate + memo + OPRMS | 1 | Opus |
| **3** | Alpha: red team + cycle + asymmetric bet | 1 | Opus |
| **4** | Alpha Debate: Soros vs Marks final arbitration | 1 | Opus |
| **4b** | Compile HTML report + save to company.db | 1 | Haiku |
| **5** | Auto-push company.db to cloud | Script | — |

Output: `deep_analyses/{TICKER}/{YYYYMMDD_HHMMSS}/` (HTML report + per-agent markdown)

---

## OPRMS Rating System

Two-dimensional position sizing framework:

| | S | A | B | C |
|---|---|---|---|---|
| **DNA** (Asset Quality) | Holy Grail 20-25% | General 15% | Dark Horse 7% | Follower 2% |
| **Timing** (Entry Quality) | Once-in-Lifetime ×1.0-1.5 | Trend Confirmed ×0.8-1.0 | Normal Range ×0.4-0.6 | Dead Time ×0.1-0.3 |

**Formula**: `Position = Total Capital × DNA Max% × Timing Coeff × Regime Mult`

- Evidence gate: <3 primary sources → proportional scaling
- Regime multiplier: RISK_OFF ×0.7, CRISIS ×0.4
- SSOT: `knowledge/oprms/models.py`

---

## Backtest & Factor Research

### RS Backtest Engine (`backtest/`)
- Relative Strength ranking → Top-N selection → periodic rebalance → NAV tracking
- Metrics: Sharpe, Sortino, Calmar, Alpha, Beta, Max Drawdown
- Parameter sweep + Walk-Forward optimization
- HTML reports with Chart.js visualizations
- Adapters: US stocks + crypto

### Factor Study Framework (`backtest/factor_study/`)
- 8 registered factors (RS_B/C, PMARP, RVOL, DV_Acceleration, RVOL_Sustained, Crypto_RS_B/C)
- Dual-track analysis: IC (prediction power) + Event Study (signal validation)
- 4 signal types: threshold, cross_up, cross_down, sustained
- Benchmark-adjusted excess returns
- CLI: `scripts/run_factor_study.py --market us_stocks --factor RS_Rating_B`

---

## Options Desk

- **IV Tracking**: Daily IV collection + rank/percentile + historical volatility
- **Chain Analysis**: Liquidity scoring, term structure, earnings positioning
- **BS Solver**: Pure-Python Black-Scholes pricing + IV inversion (no external libs)
- **24 Strategy Playbooks**: Covered calls to iron condors
- **Skill**: `/options SYMBOL` for conversational strategy discussion
- **Data**: MarketData.app (10K credits/day)

---

## Quickstart

```bash
# 1. Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Environment (.env)
FMP_API_KEY=your_key_here
MARKETDATA_API_KEY=your_key_here  # optional, for options

# 3. Update data
python scripts/update_data.py --all

# 4. Run deep analysis
./scripts/auto_deep_analyze.sh AAPL

# 5. Cloud sync
./sync_to_cloud.sh --pull   # Get latest market data
./sync_to_cloud.sh --push   # Upload analysis results
./sync_to_cloud.sh --status # Check both sides
```

---

## Documentation

| File | Contents |
|------|----------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | System design, data flow, layer details |
| [CLAUDE.md](./CLAUDE.md) | AI operating instructions |
| `docs/design/` | Design docs (company_db, options, portfolio, theme_engine) |
| `docs/plans/` | Implementation plans (8 files) |
| `docs/issues/` | Issue tracker (4 files) |
| `docs/CHANGELOG.md` | Full build history |

---

## Project Status

| Phase | Date | Status |
|-------|------|--------|
| Workspace merge + Desk skeleton | 2026-02-06 | DONE |
| Terminal orchestration + Macro pipeline | 2026-02-09 | DONE |
| Deep Analysis Pipeline v2 + HTML reports | 2026-02-11 | DONE |
| Unified Company DB + Attention Engine | 2026-02-13 | DONE |
| Data Guardian + Theme Engine P2 | 2026-02-15 | DONE |
| RS Backtest + Factor Study Framework | 2026-02-16 | DONE |
| Alpha Debate + Agent Memory + Company Profiler | 2026-02-20 | DONE |
| Options Module (IV + chains + BS solver + 24 playbooks) | 2026-02-25 | DONE |
| Deep Analysis enhancements (business overview + forward estimates) | 2026-02-28 | DONE |
| **Data Infra Upgrade (P1-P3: market.db primary + DB ownership + cloud sync)** | 2026-03-03 | DONE |
| **Automated Sync (launchd pull + auto-push + metrics on cloud)** | 2026-03-04 | DONE |
| CSV retirement | — | NEXT |
| Portfolio desk activation (real holdings) | — | PLANNED |

---

Built with [Claude Code](https://claude.ai/claude-code) by Anthropic.
