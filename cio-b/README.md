# CIO-B Workspace — Geralt (CIO & Performance Coach)

Standalone workspace housing CIO-B (Pattern C deployment; aligned to the 40-line `风险采购员的自我修养.md` and live Finance signal pipes on 2026-11-27).

## What this is

`Finance/cio-b/` is a dedicated CC workspace where the model operates as **Geralt** — Boss's CIO and **Trading Performance Coach**. `CLAUDE.md` is loaded at session start, pairing **emotional/cognitive reframing (Performance Coach)** with **four live Finance signal pipes**:
1. **Constitution**: Obsidian `Cards/风险采购员的自我修养.md` (read in full at session start).
2. **Live Book**: Portfolio Google Sheet (`PORTFOLIO_SHEET_ID`, strictly read-only via xlsx export) + `433 Pool Architecture`.
3. **Stock Pool**: Weekly Prosperity × 14W Momentum shortlist (`src/data/prosperity_morning.py`, read-only after `--pull`).
4. **Timing & Crowding**: Live TradingView 3-indicator panel (`/technical-analysis`: `Supertrend(14,2) + 奶牛趋势通道`, `Vol+RVOL`, `Extremity Z+S`) + US Theme Crowding V2.2 (`scripts/theme_crowding_dashboard.py`).

## How to start

```bash
# Direct
cd "/Users/owen/CC workspace/Finance/cio-b" && claude

# Via alias (installed in ~/.zshrc)
cio
```

## What lives here

| Path | Role |
|------|------|
| `CLAUDE.md` | **Source of truth** for how Geralt coaches, triages, reads live signals, and sizes bets |
| `AGENTS.md` | Generated Codex/agent startup bundle (`scripts/codex/render_startup_bundle.py`, untracked) |
| `cards/` | Gitignored local mirror of 1-page Thesis cards (tier + `load-bearing variable` + `kill condition`; master in Obsidian `Cards/Stocks/`) |
| `draft/` | Frozen history: early drafts + `CLAUDE.v1.md` (retired). Not loaded |
| `research/` | Psychology research backstory used to construct the persona |

## What this workspace is not

- Not a coding workspace. For code work on Finance, use a git worktree under `Finance/`.
- Not a data-writing tool. `market.db`, `prosperity.db`, `company.db`, and the Portfolio Google Sheet are strictly read-only here.
