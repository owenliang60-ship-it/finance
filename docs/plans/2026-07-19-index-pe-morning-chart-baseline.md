# Index PE Morning Chart — Post-Merge Test Baseline (2026-07-30)

**Status**: This is the authoritative comparison baseline for Task 9 of the
`2026-07-19-index-pe-morning-chart` plan. It supersedes and replaces:
- the original 2026-07-19 pre-merge baseline (if any existed for this branch), and
- the SOXX historical-TTM-PE audit's "14 failed" baseline number.

Any future full-suite diff for this branch must diff against the numbers below,
not against either superseded baseline.

## Context

Recorded immediately after Task 0's two merges into `codex/index-pe-morning-chart`:
1. `git merge main` (eb75969) — brings in 14 commits from `main`, incl. the
   volconc morning-report 0b context section.
2. `git merge --no-ff codex/soxx-historical-ttm-pe` (f103497) — brings in the
   18-commit audited SOXX historical TTM PE evidence layer.

HEAD at time of this baseline: `f103497` (merge of `codex/soxx-historical-ttm-pe`
into `codex/index-pe-morning-chart`).

## Commands

Target suite (merge-surface regression check):

```bash
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest \
  tests/test_fmp_forward_client.py \
  tests/test_fmp_forward_ingestion.py \
  tests/test_market_store_fmp_forward.py \
  tests/test_historical_basket_valuation.py \
  tests/test_historical_market_cap_sanity.py \
  tests/test_morning_report.py \
  tests/test_morning_html_report.py -q
```

Result: **226 passed, 1 skipped** in 17.09s. Zero failures. The volconc
frozen-fixture parity test
(`tests/test_morning_report.py::TestVolumeConcentrationFrozenFixtureParity::test_frozen_fixture_matches_hardcoded_reference_six_fields`)
passed with no fixture or reference-value changes.

Full suite:

```bash
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/ -q
```

Result: **7 failed, 2520 passed, 4 skipped** in 273.27s (0:04:33).

## Pre-existing failures (verbatim list)

```
FAILED tests/test_breadth_buy_quality.py::test_load_event_dates_s1_active_snapshot
FAILED tests/test_breadth_buy_quality.py::test_load_event_dates_s1_with_delisted_snapshot
FAILED tests/test_breadth_buy_quality.py::test_load_event_dates_s2_active_snapshot
FAILED tests/test_breadth_buy_quality.py::test_load_event_dates_s2_with_delisted_snapshot
FAILED tests/test_breadth_buy_quality.py::test_events_csv_columns_and_rows
FAILED tests/test_telegram_routing.py::TestPortfolioRouting::test_run_intelligence_empty_holdings_uses_private_delivery
FAILED tests/test_telegram_routing.py::TestPortfolioRouting::test_run_intelligence_normal_uses_private_delivery
```

### Root cause (confirmed, not a merge regression)

Both clusters are caused by files that exist in the primary checkout
(`/Users/owen/CC workspace/Finance`) but are absent from this git worktree —
not by any code change from either merge:

- **`test_breadth_buy_quality.py` (5 tests)**: fail with
  `FileNotFoundError: .../data/breadth_study_1b/daily_breadth.csv`. That path
  is `.gitignore`'d (`/data/* data/breadth_study_1b/daily_breadth.csv`), i.e.
  local-only generated data that is present in the primary checkout's
  filesystem but was never committed, so a fresh worktree never has it.
- **`test_telegram_routing.py::TestPortfolioRouting` (2 tests)**: fail with
  `SheetBookError: PORTFOLIO_SHEET_ID not configured`. The primary checkout
  has a local `.env` with `PORTFOLIO_SHEET_ID` set; this worktree has no
  `.env` at all. (Note: on the primary checkout, with `.env` loaded, these
  same two tests still fail, but on a *different* line — a pre-existing
  `TypeError: '>' not supported between instances of 'MagicMock' and 'int'`
  at `scripts/portfolio_intelligence.py:1178` — so the tests are not clean
  on main either; the worktree just fails earlier for an unrelated reason.)

**Verification performed**: ran this exact 7-test list at the pre-merge
commit `1029910` in this same worktree (via `git switch --detach 1029910`,
then switched back to `codex/index-pe-morning-chart` afterward, working tree
was clean throughout). All 7 tests failed identically, with the same error
signatures, before either merge landed. This confirms the failures are a
worktree-environment artifact (missing `.env`, missing gitignored local data
directory) that predates Task 0 and is orthogonal to the merged code.

## Baseline number for Task 9

**7 pre-existing failures**, all environment-caused per above, **0 failures
attributable to the Task 0 merges**. Task 9's full-suite comparison should
expect these same 7 failures (or their resolution, if a later task happens to
add the missing `.env`/data files) and treat any *additional* failure as a
real regression to investigate.
