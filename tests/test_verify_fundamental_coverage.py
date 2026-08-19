"""Tests for scripts/verify_fundamental_coverage.py (T18, R9 — Stop F gate).

Four independently-thresholded metrics, each with an explicit denominator
(MF-4): three-table coverage, 8Q continuity, profile coverage, and forward
(D2/D1) coverage. Every missing symbol must carry a coverage_status
six-state attribution (metrics 1-3) or a fetch_failed/insufficient_quarters
classification (metric 4, R3-m3/R4-P2-3) — an unattributed gap fails the
verifier itself, independent of whether the aggregate percentage clears its
threshold.
"""
import json

import pytest

from scripts.verify_fundamental_coverage import (
    STATEMENT_TABLES,
    PROFILE_TABLE,
    parse_args,
    verify,
    _print_report,
)
from src.data.market_store import MarketStore


def _today_fn():
    from datetime import date
    return date(2026, 8, 19)


def _sm_row(symbol, eligible=1, reason="ok"):
    return {
        "symbol": symbol, "cik": None, "company_name": symbol + " Inc.",
        "exchange": "NASDAQ", "is_etf": 0, "is_fund": 0, "is_adr": 0,
        "share_class_of": None, "eligible": eligible, "reason": reason,
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _seed_universe(store, symbols, as_of="2026-01-02"):
    store.upsert_security_master([_sm_row(s) for s in symbols])
    store.record_membership_snapshot(symbols, as_of=as_of)


def _seed_statements(store, symbol, fiscal_dates):
    """Give a symbol the same fiscal-date coverage in all three current tables
    (mirrors tests/test_backfill_runner.py's fixture helper)."""
    rows = [{"date": d, "symbol": symbol, "period": "Q", "revenue": 1.0}
            for d in fiscal_dates]
    store.upsert_income(symbol, rows)
    store.upsert_balance_sheet(symbol, rows)
    store.upsert_cash_flow(symbol, rows)


def _seed_profile(store, symbol, updated_at="2026-08-01T00:00:00Z"):
    with store.transaction() as conn:
        store.write_company_profile_in_conn(conn, symbol, {"name": symbol}, updated_at)


def _mark_coverage(store, symbol, datasets, status):
    store.upsert_coverage_status([
        {"symbol": symbol, "dataset": d, "status": status,
         "updated_at": "2026-08-01T00:00:00Z"}
        for d in datasets
    ])


FUTURE_Q = ["2026-09-30", "2026-12-31", "2027-03-31", "2027-06-30"]

# 8 consecutive quarter ends, gaps 90-92 days, most recent well inside the
# FUNDAMENTAL_QUARTER_GAP_MAX_DAYS window relative to TODAY (2026-08-19).
QUARTER_ENDS_8 = [
    "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
    "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
]


def _seed_forward_covered(store, symbol, snapshot="2026-08-15"):
    rows = [{
        "snapshot_date": snapshot, "fiscal_date": fd, "period_type": "Q",
        "snapshot_kind": "weekly", "eps_avg": 1.5,
    } for fd in FUTURE_Q]
    store.upsert_fmp_estimates(symbol, rows)


@pytest.fixture
def store(tmp_path):
    s = MarketStore(tmp_path / "market.db")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Group A: 95%/94% threshold boundary (three-table coverage)
# ---------------------------------------------------------------------------

def test_three_table_coverage_95pct_passes(store):
    symbols = [f"S{i:03d}" for i in range(100)]
    _seed_universe(store, symbols)
    covered, missing = symbols[:95], symbols[95:]
    for s in covered:
        _seed_statements(store, s, ["2026-06-30"])
    for s in missing:
        _mark_coverage(store, s, STATEMENT_TABLES, "fetch_failed")

    rc, report = verify(store, today_fn=_today_fn)
    m = report["metrics"]["three_table"]
    assert m["covered"] == 95
    assert m["denominator"] == 100
    assert m["pct"] == 95.0
    assert m["ok"] is True
    assert len(m["missing"]) == 5


def test_three_table_coverage_94pct_fails(store):
    symbols = [f"S{i:03d}" for i in range(100)]
    _seed_universe(store, symbols)
    covered, missing = symbols[:94], symbols[94:]
    for s in covered:
        _seed_statements(store, s, ["2026-06-30"])
    for s in missing:
        _mark_coverage(store, s, STATEMENT_TABLES, "fetch_failed")

    rc, report = verify(store, today_fn=_today_fn)
    m = report["metrics"]["three_table"]
    assert m["pct"] == 94.0
    assert m["ok"] is False
    assert rc == 1
    assert report["ok"] is False


# ---------------------------------------------------------------------------
# Group B: not_applicable excluded from the forward D1 denominator (R3-m3)
# ---------------------------------------------------------------------------

def test_forward_not_applicable_excluded_from_denominator(store):
    symbols = ["AAA", "BBB", "NEVER"]
    _seed_universe(store, symbols)
    _seed_forward_covered(store, "AAA")
    _seed_forward_covered(store, "BBB")
    # NEVER: zero fmp_estimates rows ever, no forward manifest at all ->
    # not_applicable, must NOT inflate the denominator nor count as covered.

    rc, report = verify(store, today_fn=_today_fn)
    fwd = report["metrics"]["forward"]
    assert fwd["not_applicable"] == ["NEVER"]
    assert fwd["d1"] == 2
    assert fwd["d2"] == 2
    assert fwd["pct"] == 100.0
    assert fwd["ok"] is True


def test_forward_fetch_failed_forces_d1_inclusion_not_not_applicable(store):
    """R3-m3 + R4-P2-3 frozen order: fetch_failed is classified BEFORE
    not_applicable, so a symbol with zero historical rows whose fetch simply
    failed this run must land in the failure bucket (and D1), never in
    not_applicable — a collection outage must not masquerade as "no analyst
    coverage"."""
    symbols = ["AAA", "FAILED"]
    _seed_universe(store, symbols)
    _seed_forward_covered(store, "AAA")
    summary = json.dumps({
        "run_state": {"quarter_empty": [], "earnings_failed": []},
        "attempts": [{"quarter_failed": ["FAILED"]}],
    })
    store.upsert_fmp_forward_run({
        "snapshot_date": "2026-08-15", "run_kind": "weekly", "status": "complete",
        "target_universe": symbols, "started_at": "2026-08-15T02:00:00Z",
        "completed_at": "2026-08-15T03:00:00Z", "summary_json": summary,
    })

    rc, report = verify(store, today_fn=_today_fn)
    fwd = report["metrics"]["forward"]
    assert "FAILED" not in fwd["not_applicable"]
    assert "FAILED" in fwd["fetch_failed_bucket"]
    assert fwd["d1"] == 2          # FAILED counted in the denominator...
    assert fwd["d2"] == 1          # ...but not in the numerator.
    missing = {m["symbol"]: m["reason"] for m in fwd["missing"]}
    assert missing["FAILED"] == "fetch_failed"


# ---------------------------------------------------------------------------
# Group C: unattributed missing symbol fails the verifier itself, even when
# the metric's own percentage clears its threshold (no silent gaps)
# ---------------------------------------------------------------------------

def test_verifier_fails_on_unattributed_missing_even_if_pct_clears_threshold(store):
    symbols = [f"S{i:03d}" for i in range(100)]
    _seed_universe(store, symbols)
    for s in symbols[:99]:
        _seed_statements(store, s, QUARTER_ENDS_8)
    ghost = symbols[99]
    # ghost has zero rows in all three tables AND zero coverage_status rows
    # anywhere — a pure, unexplained gap.

    rc, report = verify(store, today_fn=_today_fn)
    m = report["metrics"]["three_table"]
    assert m["pct"] == 99.0
    assert m["ok"] is True             # the metric threshold itself is met...
    assert rc == 1                     # ...but the verifier still FAILs...
    assert report["ok"] is False
    assert any(g["symbol"] == ghost and g["metric"] == "three_table"
              for g in report["unattributed_gaps"])


def test_attributed_missing_symbol_does_not_trigger_unattributed_fail(store):
    """Positive control: a missing symbol WITH a coverage_status row (even
    just one of the three relevant tables) is a known, explained gap and must
    not trip the unattributed-gap fail-closed path."""
    symbols = [f"S{i:03d}" for i in range(100)]
    _seed_universe(store, symbols)
    for s in symbols:
        _seed_profile(store, s)
        _seed_forward_covered(store, s)
    for s in symbols[:99]:
        _seed_statements(store, s, QUARTER_ENDS_8)
    ghost = symbols[99]
    _mark_coverage(store, ghost, STATEMENT_TABLES, "not_applicable")

    rc, report = verify(store, today_fn=_today_fn)
    assert report["unattributed_gaps"] == []
    assert rc == 0
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# Supplementary coverage: 8Q continuity, profile, D2 quarter gate, empty
# universe, CLI wiring — not required by the brief's three RED groups but
# exercise the remaining wiring so a future refactor can't silently break it.
# ---------------------------------------------------------------------------

def test_8q_continuity_pass_and_insufficient_depth_fail(store):
    _seed_universe(store, ["FULL", "SHORT"])
    _seed_statements(store, "FULL", QUARTER_ENDS_8)
    _seed_statements(store, "SHORT", QUARTER_ENDS_8[1:])  # only 7 quarters
    _mark_coverage(store, "SHORT", STATEMENT_TABLES, "ok")

    rc, report = verify(store, today_fn=_today_fn)
    m = report["metrics"]["continuity_8q"]
    assert "FULL" not in [x["symbol"] for x in m["missing"]]
    assert "SHORT" in [x["symbol"] for x in m["missing"]]
    assert m["gap_max_days"] == 120
    assert m["quarters_required"] == 8


def test_profile_coverage_threshold_and_attribution(store):
    _seed_universe(store, ["HASPROFILE", "NOPROFILE"])
    _seed_profile(store, "HASPROFILE")
    _mark_coverage(store, "NOPROFILE", [PROFILE_TABLE], "provider_empty")

    rc, report = verify(store, today_fn=_today_fn)
    m = report["metrics"]["profile"]
    assert m["covered"] == 1
    assert m["denominator"] == 2
    assert m["pct"] == 50.0
    missing = {x["symbol"]: x["attribution"] for x in m["missing"]}
    assert missing["NOPROFILE"][PROFILE_TABLE] == "provider_empty"


def test_forward_d2_requires_four_nonnull_future_quarters(store):
    _seed_universe(store, ["THIN"])
    rows = [{
        "snapshot_date": "2026-08-15", "fiscal_date": fd, "period_type": "Q",
        "snapshot_kind": "weekly",
        "eps_avg": None if fd == FUTURE_Q[0] else 1.0,
    } for fd in FUTURE_Q]
    store.upsert_fmp_estimates("THIN", rows)

    rc, report = verify(store, today_fn=_today_fn)
    fwd = report["metrics"]["forward"]
    assert fwd["d1"] == 1          # has a row -> not not_applicable
    assert fwd["d2"] == 0          # only 3 of 4 future quarters non-null
    missing = {m["symbol"]: m["reason"] for m in fwd["missing"]}
    assert missing["THIN"] == "insufficient_quarters"


def test_forward_backfill_snapshot_does_not_satisfy_weekly_d2(store):
    _seed_universe(store, ["BACKFILLONLY"])
    rows = [{
        "snapshot_date": "2026-08-15", "fiscal_date": fd, "period_type": "Q",
        "snapshot_kind": "backfill", "eps_avg": 1.0,
    } for fd in FUTURE_Q]
    store.upsert_fmp_estimates("BACKFILLONLY", rows)

    rc, report = verify(store, today_fn=_today_fn)
    fwd = report["metrics"]["forward"]
    # a backfill-only row still counts as "ever had a row" (not not_applicable)
    assert fwd["not_applicable"] == []
    assert fwd["d2"] == 0


def test_empty_universe_fails_loud_not_silent(store):
    rc, report = verify(store, today_fn=_today_fn)
    assert rc == 1
    assert report["ok"] is False
    assert report["failures"]


def test_parse_args_json_flag():
    assert parse_args([]).as_json is False
    assert parse_args(["--json"]).as_json is True


def test_print_report_smoke(store, capsys):
    _seed_universe(store, ["ONLY"])
    _seed_statements(store, "ONLY", ["2026-06-30"])
    _mark_coverage(store, "ONLY", STATEMENT_TABLES, "ok")
    rc, report = verify(store, today_fn=_today_fn)
    _print_report(report)
    out = capsys.readouterr().out
    assert "three_table" in out
    assert "forward" in out
