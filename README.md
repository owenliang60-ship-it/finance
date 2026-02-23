# Finance — 未来资本 AI Trading Desk

**AI-powered institutional-grade investment infrastructure for personal portfolio management.**

Manage a multi-million dollar US equity portfolio with professional research, risk monitoring, and trading discipline — powered by Claude and the 6 Desk Model.

---

## Quickstart (< 5 minutes)

### 1. Install Dependencies

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install packages
pip install -r requirements.txt
```

### 2. Configure Environment

Create `.env` file in the workspace root:

```bash
# Financial Modeling Prep API (required)
FMP_API_KEY=your_api_key_here

# Optional: Obsidian MCP integration (configured in Claude Code settings)
```

Get FMP API key at [financialmodelingprep.com](https://financialmodelingprep.com) (Starter plan: $22/mo).

### 3. Run Your First Analysis

In Claude Code, with this workspace open:

```python
from terminal.commands import analyze_ticker

# Quick data snapshot (5 sec, free)
result = analyze_ticker("AAPL", depth="quick")

# Standard analysis with 6 investment lenses (1 min, ~$2)
result = analyze_ticker("AAPL", depth="standard")

# Full analysis with debate + memo + OPRMS rating (5 min, ~$13-15)
result = analyze_ticker("AAPL", depth="full")
```

Claude will guide you through the analysis prompts and generate an investment memo with OPRMS rating.

### 4. View Results

Analysis results are stored in:
- **Company DB**: `data/companies/{SYMBOL}/` — memos, OPRMS ratings, kill conditions
- **Reports**: `reports/` — historical investment theses

---

## Core Capabilities

| Command | Purpose | Time | Cost |
|---------|---------|------|------|
| `analyze_ticker(symbol, depth)` | Research a ticker with 3 depth levels | 5s - 5min | $0 - $15 |
| `portfolio_status()` | Holdings summary + exposure alerts | Instant | $0 |
| `position_advisor(symbol)` | OPRMS-based position sizing | Instant | $0 |
| `company_lookup(symbol)` | Retrieve all knowledge on a ticker | Instant | $0 |
| `run_monitor()` | Portfolio health sweep | 1min | $0 |
| `theme_status(slug)` | Investment theme membership | Instant | $0 |

---

## The 6 Desk Model

This workspace is organized like an institutional trading desk:

| Desk | Directory | Function | Status |
|------|-----------|----------|--------|
| **Data** | `src/`, `data/`, `scripts/` | Market data collection, validation, storage | Live |
| **Research** | `reports/`, `knowledge/philosophies/` | Investment analysis, 6-lens framework | Live |
| **Risk** | `risk/` | IPS, exposure monitoring, kill conditions | Live |
| **Trading** | `trading/` | Trade logs, options strategies, post-mortems | Skeleton |
| **Portfolio** | `portfolio/` | Holdings, watchlists, performance attribution | Skeleton |
| **Knowledge** | `knowledge/` | OPRMS rating system, investment frameworks | Live |

See [ARCHITECTURE.md](./ARCHITECTURE.md) for system design details.

---

## Data Desk

### Stock Pool
- **Universe**: US large-cap equities (market cap > $100B)
- **Exchanges**: NYSE + NASDAQ
- **Sectors**: Technology, Financials, Healthcare, Consumer Cyclical, Communication (Entertainment only)
- **Current Pool**: 77 stocks with 5-year daily price history
- **Configuration**: `config/settings.py`

### Data Sources
- **FMP API**: Company fundamentals, financials, price data
- **SQLite**: `data/valuation.db` (company info + financials)
- **CSV**: `data/price/{SYMBOL}.csv` (5-year daily OHLCV)
- **JSON**: `data/fundamental/{SYMBOL}/*.json` (statements, ratios, profile)

### Technical Indicators
- **PMARP**: Price momentum percentile (>98% = strong trend)
- **RVOL**: Relative volume (>4σ = anomaly)
- **Extensible**: Add custom indicators in `src/indicators/`

### Cloud Deployment
- **Server**: Aliyun (`ssh aliyun`)
- **Path**: `/root/workspace/Finance/`
- **Cron Jobs**: Daily price updates (06:30 BJT), weekly fundamentals refresh (Saturday 10:00)
- **Sync Script**: `./sync_to_cloud.sh [--code|--data|--all]`

---

## OPRMS Rating System

**Two-dimensional position sizing framework:**

### Y-Axis: Asset DNA (Quality)

| Rating | Name | Max Position | Characteristics |
|--------|------|--------------|-----------------|
| **S** | Holy Grail | 20-25% | Civilization-altering assets |
| **A** | General | 15% | Sector leaders, proven compounders |
| **B** | Dark Horse | 7% | High-beta narrative plays |
| **C** | Follower | 2% | Low conviction, momentum only |

### X-Axis: Timing Coefficient (Entry Quality)

| Rating | Name | Coefficient | Characteristics |
|--------|------|-------------|-----------------|
| **S** | Once-in-Lifetime | 1.0 - 1.5 | Historical bottoms, breakouts |
| **A** | Trend Confirmed | 0.8 - 1.0 | Right-side entries |
| **B** | Normal Range | 0.4 - 0.6 | Support bounces, consolidation |
| **C** | Dead Time | 0.1 - 0.3 | Left-side speculation |

**Position Formula**: `Position = Total Capital × DNA Max% × Timing Coeff`

**Evidence Gate**: Positions with <3 primary sources get scaled proportionally (e.g., 1 source = 33% of calculated size).

Models: `knowledge/oprms/models.py`
IPS: `risk/ips.md`

---

## Investment Philosophy

### 6-Lens Analysis Framework

Every standard/full analysis evaluates a ticker through 6 perspectives:

1. **Business Quality** — Competitive moats, unit economics, customer retention
2. **Management Excellence** — Capital allocation, insider ownership, strategic clarity
3. **Financial Strength** — Balance sheet, cash generation, ROIC trends
4. **Growth Trajectory** — TAM expansion, margin trajectory, network effects
5. **Valuation** — Price vs. intrinsic value, peer multiples, discount rate
6. **Technical Setup** — Momentum, volume patterns, support/resistance

Lenses are defined in `knowledge/philosophies/` and injected into the analysis pipeline.

---

## Next Steps

1. **Run first analysis**: `analyze_ticker("NVDA", depth="full")`
2. **Input holdings**: Add positions to `portfolio/holdings/positions.json`
3. **Set up monitoring**: Configure alerts in `portfolio/exposure/rules.py`
4. **Explore themes**: Create investment themes in `data/themes/`
5. **Read architecture**: See [ARCHITECTURE.md](./ARCHITECTURE.md) for extension points

---

## Project Status

- **Phase 1**: Workspace merge + Desk skeleton — DONE (2026-02-06)
- **Phase 2 P0**: Terminal orchestration layer (7 files, 1462 lines) — DONE (2026-02-07)
- **Commit**: `bda41f7` (pushed to GitHub)
- **Next**: Production validation + FMP endpoint expansion + holdings input

---

## Documentation

- **ARCHITECTURE.md** — System design, data flow, extension points
- **CLAUDE.md** — AI operating instructions (workspace-specific)
- **risk/ips.md** — Investment Policy Statement (position limits, return hurdles)
- **knowledge/oprms/README.md** — Rating system specification

---

## Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/finance/issues)
- **Logs**: `logs/` directory (local), `/root/workspace/Finance/logs/` (cloud)
- **Data Validation**: `python -c "from src.data.data_validator import print_data_report; print_data_report()"`

---

Built with Claude Code by Anthropic.
