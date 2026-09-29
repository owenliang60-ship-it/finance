"""Prosperity engine constants; each cites where the value comes from."""

# D9 baseline vintage is complete from here: phase0 close snapshot max observed_at 2026-09-29T03:11:58Z
STRICT_STATEMENTS_FROM = "2026-09-29"
# Earliest extended_membership.effective_from in market.db
LIVE_MEMBERSHIP_FROM = "2026-08-21"
# First weekly fmp_estimates snapshot in market.db
FIRST_WEEKLY_SNAPSHOT = "2026-07-13"
# First weekly snapshot + 13 weeks: long revision window becomes available
REVISION_LONG_WINDOW_FROM = "2026-10-12"
REVISION_SHORT_WEEKS = 4
REVISION_LONG_WEEKS = 13
# Weekly cadence: a snapshot older than one week is stale (north-star NTM contract)
SNAPSHOT_MAX_STALENESS_DAYS = 7
PRE_ANNOUNCE_MAX_STALENESS_DAYS = 14
PRICE_MAX_STALENESS_DAYS = 7
MCAP_MAX_STALENESS_DAYS = 10
# North-star: estimates match fiscal quarters within ±10 days; EPS ↔ statements within ±20 (Codex SAME_QUARTER_DAYS)
ESTIMATE_MATCH_DAYS = 10
EPS_STATEMENT_MATCH_DAYS = 20
# Enough history for 4-quarter growth and SUE's 8 prior YoY pairs plus bases
STATEMENT_QUARTERS = 16
EPS_QUARTERS = 16
# North-star NTM contract: far quarters with fewer analysts fall back to FY blend
NTM_MIN_ANALYSTS = 3
# Q14: median period ≥150 days marks a semiannual reporter (e.g. BHP)
SEMIANNUAL_MEDIAN_GAP_DAYS = 150
# Same as prosperity_history FUNDAMENTAL gap cap (config/settings.py FUNDAMENTAL_QUARTER_GAP_MAX_DAYS)
STALE_FISCAL_DAYS = 120
# scripts/morning_report.py BETA_BENCHMARK
BETA_BENCHMARK = "SPY"
PIT_RANK = {"approximate": 0, "strict": 1, "live": 2}
