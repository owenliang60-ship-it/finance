"""Tests for the read-only fundamental quality auditor.

Real temp-file ``MarketStore`` plus a monkeypatched ``current_base_universe``:
no network, no writer lock, no shared data/ side effects. The auditor is
read-only, so several tests also assert the SQLite change counter is untouched.
"""
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from src.data.market_store import MarketStore

AS_OF = "2026-08-24"
SUCCESS_FRESH = "2026-08-20T00:00:00Z"   # 4 days before AS_OF (< default cooldown 7)
SUCCESS_FRESH_BUT_OVER_REFRESH = "2026-07-15T00:00:00Z"  # 40 days (> default 30)
SUCCESS_OLD = "2026-06-10T00:00:00Z"     # 75 days (>= cooldown, no recent check)

REQUIRED_TABLES = ("income_quarterly", "balance_sheet_quarterly", "cash_flow_quarterly")


# ---------------------------------------------------------------------------
# Seeding helpers (direct SQL / store writers — full control, no network)
# ---------------------------------------------------------------------------

def _seed_coverage(store, symbol, dataset, *, status="ok", next_retry_at=None,
                   last_attempt_at=SUCCESS_FRESH, last_success_at=SUCCESS_FRESH):
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO coverage_status "
        "(symbol, dataset, status, detail, updated_at, last_attempt_at, "
        "last_success_at, consecutive_failures, next_retry_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, 0, ?)",
        (symbol, dataset, status, SUCCESS_FRESH, last_attempt_at,
         last_success_at, next_retry_at),
    )
    conn.commit()


def _seed_earnings(store, symbol, *, announce_date, fiscal_date, last_updated):
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO fmp_earnings "
        "(symbol, announce_date, fiscal_date, last_updated) VALUES (?, ?, ?, ?)",
        (symbol, announce_date, fiscal_date, last_updated),
    )
    conn.commit()


def _chain(latest, fy, q, n=5):
    """n descending quarters with sequential fiscal labels and ~91d spacing."""
    current = date.fromisoformat(latest)
    rows = []
    for index in range(n):
        rows.append({
            "date": current.isoformat(),
            "fiscal_year": str(fy),
            "period": f"Q{q}",
            "eps_diluted": 1.0 + 0.1 * (n - 1 - index),
            "revenue": 100.0 + index,
            "net_income": 20.0 + index,
            "gross_profit": 40.0 + index,
            "operating_income": 25.0 + index,
            "ebitda": 30.0 + index,
        })
        current -= timedelta(days=91)
        q -= 1
        if q == 0:
            q = 4
            fy -= 1
    return rows


def _seed_ready(store, symbol, *, latest="2026-06-30", fy=2026, q=2, n=5,
                metrics_date=None, per_table=None, status="ok",
                next_retry_at=None, last_success_at=SUCCESS_FRESH,
                last_attempt_at=SUCCESS_FRESH, metrics=True):
    rows = _chain(latest, fy, q, n)
    store.upsert_income(symbol, rows)
    store.upsert_balance_sheet(symbol, rows)
    store.upsert_cash_flow(symbol, rows)
    if metrics:
        store.upsert_metrics(symbol, [{
            "date": metrics_date or latest,
            "revenue_cagr_4q": 0.10,
            "net_income_cagr_4q": 0.10,
        }])
    for key, table in zip(("income", "balance", "cashflow"), REQUIRED_TABLES):
        override = (per_table or {}).get(key, {})
        _seed_coverage(
            store, symbol, table,
            status=override.get("status", status),
            next_retry_at=override.get("next_retry_at", next_retry_at),
            last_success_at=override.get("last_success_at", last_success_at),
            last_attempt_at=override.get("last_attempt_at", last_attempt_at),
        )


def _seed_neutral_earnings(store, symbol, income_latest):
    """A fresh, valid cached feed that proves no upcoming gap (state 'none')."""
    announce = date.fromisoformat(income_latest) + timedelta(days=20)
    fiscal = date.fromisoformat(income_latest) + timedelta(days=32)
    if fiscal > date.fromisoformat(AS_OF):
        fiscal = date.fromisoformat(AS_OF) - timedelta(days=5)
    _seed_earnings(
        store, symbol,
        announce_date=announce.isoformat(),
        fiscal_date=fiscal.isoformat(),
        last_updated="2026-08-22T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


@pytest.fixture
def run_audit(tmp_store, monkeypatch):
    import src.data.fundamental_quality as fq

    state = {"symbols": []}
    monkeypatch.setattr(fq, "current_base_universe", lambda store: list(state["symbols"]))

    def _run(symbols, as_of=AS_OF, **kwargs):
        state["symbols"] = list(symbols)
        return fq.audit_fundamentals(tmp_store, as_of=as_of, **kwargs)

    return _run


def _record(report, symbol):
    return report["symbols"][symbol]


# ---------------------------------------------------------------------------
# as_of normalization / input validation
# ---------------------------------------------------------------------------

def test_pure_date_is_normalized_to_end_of_day_utc(run_audit):
    report = run_audit(["EMPTYDATA"], as_of="2026-08-24")
    assert report["as_of"] == "2026-08-24T23:59:59Z"
    assert report["schema_version"] == 1
    assert report["universe"] == {"count": 1, "symbols": ["EMPTYDATA"]}
    assert report["coverage"]["fundamental_ready"] == {"covered": 0, "total": 1, "ratio": 0.0}
    assert report["status"] == "FAIL"
    assert json.loads(json.dumps(report)) == report


def test_iso_datetime_is_normalized_to_utc(run_audit):
    report = run_audit(["EMPTYDATA"], as_of="2026-08-24T10:00:00+08:00")
    assert report["as_of"] == "2026-08-24T02:00:00Z"


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-13-01", "2026-08-24T10:00:00"])
def test_malformed_as_of_rejected(run_audit, bad):
    with pytest.raises(ValueError):
        run_audit([], as_of=bad)


@pytest.mark.parametrize("knob", ["refresh_days", "stale_after_days", "cooldown_days"])
@pytest.mark.parametrize("value", [0, -1])
def test_nonpositive_knobs_rejected(run_audit, knob, value):
    with pytest.raises(ValueError):
        run_audit([], **{knob: value})


# ---------------------------------------------------------------------------
# Universe denominator / readiness coverage
# ---------------------------------------------------------------------------

def test_universe_keeps_every_base_security_sorted(run_audit, tmp_store):
    _seed_ready(tmp_store, "BBB")
    _seed_ready(tmp_store, "AAA")
    report = run_audit(["BBB", "AAA", "CCC"])
    assert report["universe"]["count"] == 3
    assert report["universe"]["symbols"] == ["AAA", "BBB", "CCC"]
    assert set(report["symbols"]) == {"AAA", "BBB", "CCC"}
    assert report["coverage"]["fundamental_ready"]["total"] == 3


def test_readiness_uses_exact_denominator(run_audit, tmp_store):
    _seed_ready(tmp_store, "READY")
    # BAD has no statements at all -> not ready, not zero-filled.
    report = run_audit(["READY", "BAD"])
    coverage = report["coverage"]["fundamental_ready"]
    assert coverage == {"covered": 1, "total": 2, "ratio": 0.5}
    assert _record(report, "READY")["fundamental_ready"] is True
    assert _record(report, "BAD")["fundamental_ready"] is False
    assert _record(report, "BAD")["evidence"]["income_date"] is None
    assert "missing_coverage_status" in _record(report, "BAD")["issues"]
    assert "missing_required_statement" in _record(report, "BAD")["issues"]


def test_future_fiscal_date_is_invalid(run_audit, tmp_store):
    _seed_ready(tmp_store, "FUTURE_FISCAL", latest="2026-12-31", fy=2026, q=4,
                last_success_at=SUCCESS_FRESH, last_attempt_at=SUCCESS_FRESH)
    conn = tmp_store._get_conn()
    conn.execute(
        "INSERT INTO income_quarterly (symbol, date, fiscal_year, period) "
        "VALUES ('BAD_DATE', 'not-a-date', '2026', 'Q2')")
    conn.commit()
    for table in REQUIRED_TABLES:
        _seed_coverage(tmp_store, "BAD_DATE", table, status="ok")
    report = run_audit(["FUTURE_FISCAL", "BAD_DATE"])
    future = _record(report, "FUTURE_FISCAL")
    bad_date = _record(report, "BAD_DATE")
    assert "invalid_fiscal_date" in future["issues"]
    assert "fiscal_stale" not in future["issues"]
    assert "invalid_fiscal_date" in bad_date["issues"]


# ---------------------------------------------------------------------------
# Fiscal staleness / verification freshness
# ---------------------------------------------------------------------------

def test_aligned_old_fiscal_date_flags_fiscal_stale(run_audit, tmp_store):
    _seed_ready(tmp_store, "STALE", latest="2026-03-31", fy=2026, q=1,
                last_success_at=SUCCESS_OLD, last_attempt_at=SUCCESS_OLD)
    report = run_audit(["STALE"], refresh_days=400)  # isolate fiscal staleness
    record = _record(report, "STALE")
    assert "fiscal_stale" in record["issues"]
    assert "cross_statement_missing" not in record["issues"]
    assert record["repair_eligible"] is True
    assert report["repair_targets"] == ["STALE"]


def test_recent_fiscal_date_but_receipt_over_refresh_is_overdue(run_audit, tmp_store):
    _seed_ready(tmp_store, "DUE", latest="2026-06-30", fy=2026, q=2,
                last_success_at=SUCCESS_FRESH_BUT_OVER_REFRESH,
                last_attempt_at=SUCCESS_FRESH_BUT_OVER_REFRESH)
    report = run_audit(["DUE"])
    record = _record(report, "DUE")
    assert "verification_overdue" in record["issues"]
    assert "fiscal_stale" not in record["issues"]
    assert report["repair_targets"] == ["DUE"]


def test_future_success_timestamp_is_unknown_not_fresh(run_audit, tmp_store):
    _seed_ready(tmp_store, "FUTURE", last_success_at="2026-12-01T00:00:00Z",
                last_attempt_at="2026-12-01T00:00:00Z")
    report = run_audit(["FUTURE"])
    record = _record(report, "FUTURE")
    assert "verification_unknown" in record["issues"]
    assert "verification_overdue" not in record["issues"]


# ---------------------------------------------------------------------------
# Failed / empty states and retry timers
# ---------------------------------------------------------------------------

def test_active_failed_timer_with_old_rows_blocks_whole_symbol(run_audit, tmp_store):
    _seed_ready(
        tmp_store, "BLOCKED", latest="2026-03-31", fy=2026, q=1,
        last_success_at=SUCCESS_OLD, last_attempt_at=SUCCESS_OLD,
        per_table={"income": {"status": "fetch_failed",
                              "next_retry_at": "2026-09-01T00:00:00Z"}},
    )
    report = run_audit(["BLOCKED"], refresh_days=400)
    record = _record(report, "BLOCKED")
    assert "retry_timer_active" in record["issues"]
    assert "statement_fetch_failed" in record["issues"]
    # Even though balance/cashflow are stale, the whole symbol is blocked.
    assert "fiscal_stale" in record["issues"]
    assert record["repair_eligible"] is False
    assert report["repair_targets"] == []
    assert "BLOCKED" in report["deferred"]


def test_invalid_retry_timer_is_unknown_and_blocks(run_audit, tmp_store):
    _seed_ready(
        tmp_store, "BADTIMER",
        per_table={"income": {"status": "provider_empty",
                              "next_retry_at": "not-a-timestamp"}},
    )
    report = run_audit(["BADTIMER"])
    record = _record(report, "BADTIMER")
    assert "retry_timer_unknown" in record["issues"]
    assert record["repair_eligible"] is False
    assert "BADTIMER" in report["deferred"]


def test_valid_due_failure_is_a_repair_candidate(run_audit, tmp_store):
    _seed_ready(
        tmp_store, "DUEEMPTY",
        per_table={"income": {"status": "provider_empty",
                              "next_retry_at": "2026-08-01T00:00:00Z"}},
    )
    report = run_audit(["DUEEMPTY"])
    record = _record(report, "DUEEMPTY")
    assert "statement_provider_empty" in record["issues"]
    assert record["repair_eligible"] is True
    assert report["repair_targets"] == ["DUEEMPTY"]


@pytest.mark.parametrize("terminal,reason", [
    ("identity_blocked", "terminal_identity_blocked"),
    ("not_applicable", "terminal_not_applicable"),
])
def test_terminal_status_blocks_without_auto_fetch(run_audit, tmp_store, terminal, reason):
    _seed_ready(tmp_store, "BLOCKED", per_table={"income": {"status": terminal}})
    report = run_audit(["BLOCKED"])
    record = _record(report, "BLOCKED")
    assert reason in record["issues"]
    assert record["repair_eligible"] is False
    assert report["repair_targets"] == []
    assert "BLOCKED" in report["deferred"]


def test_missing_statement_recency_defers_source_problem(run_audit, tmp_store):
    # Recently checked `ok` row with no rows -> defer, do not hammer.
    conn = tmp_store._get_conn()
    conn.execute("INSERT OR REPLACE INTO income_quarterly (symbol, date) VALUES ('RECENT', '2026-06-30')")
    conn.commit()
    # Seed only balance/cashflow; delete the income row again to force missing.
    for table in ("balance_sheet_quarterly", "cash_flow_quarterly"):
        _seed_coverage(tmp_store, "RECENT", table, status="ok")
    conn.execute("DELETE FROM income_quarterly WHERE symbol = 'RECENT'")
    conn.commit()
    _seed_coverage(tmp_store, "RECENT", "income_quarterly", status="ok",
                   last_attempt_at=SUCCESS_FRESH, last_success_at=SUCCESS_FRESH)

    _seed_ready(tmp_store, "OLD_CHECK",
                per_table={"income": {"status": "ok"}})
    conn.execute("DELETE FROM income_quarterly WHERE symbol = 'OLD_CHECK'")
    conn.commit()
    _seed_coverage(tmp_store, "OLD_CHECK", "income_quarterly", status="ok",
                   last_attempt_at="2026-06-01T00:00:00Z",
                   last_success_at="2026-06-01T00:00:00Z")

    report = run_audit(["RECENT", "OLD_CHECK"], refresh_days=400)
    recent = _record(report, "RECENT")
    old = _record(report, "OLD_CHECK")
    assert "missing_required_statement" in recent["issues"]
    assert recent["repair_eligible"] is False
    assert "RECENT" in report["deferred"]
    assert old["repair_eligible"] is True
    assert "OLD_CHECK" in report["repair_targets"]


# ---------------------------------------------------------------------------
# Structural (non-actionable) history gaps
# ---------------------------------------------------------------------------

def test_short_and_gappy_history_are_structural_warnings(run_audit, tmp_store):
    _seed_ready(tmp_store, "SHORT", latest="2026-06-30", fy=2026, q=2, n=3)
    _seed_neutral_earnings(tmp_store, "SHORT", "2026-06-30")

    gappy = _chain("2026-06-30", 2026, 2, n=1) + _chain("2025-12-31", 2025, 4, n=4)
    tmp_store.upsert_income("GAPPY", gappy)
    tmp_store.upsert_balance_sheet("GAPPY", gappy)
    tmp_store.upsert_cash_flow("GAPPY", gappy)
    tmp_store.upsert_metrics("GAPPY", [{"date": "2026-06-30"}])
    for table in REQUIRED_TABLES:
        _seed_coverage(tmp_store, "GAPPY", table, status="ok")
    _seed_neutral_earnings(tmp_store, "GAPPY", "2026-06-30")

    report = run_audit(["SHORT", "GAPPY"])
    short = _record(report, "SHORT")
    gappy_rec = _record(report, "GAPPY")
    assert "missing_quarters" in short["issues"]
    assert "quarter_gap" in gappy_rec["issues"]
    assert short["repair_eligible"] is False
    assert gappy_rec["repair_eligible"] is False
    assert report["repair_targets"] == []
    assert report["status"] == "WARN"


# ---------------------------------------------------------------------------
# Metrics mismatch (immediate)
# ---------------------------------------------------------------------------

def test_metrics_mismatch_is_immediate_even_with_recent_success(run_audit, tmp_store):
    _seed_ready(tmp_store, "MISMATCH", metrics_date="2026-03-31")
    report = run_audit(["MISMATCH"])
    record = _record(report, "MISMATCH")
    assert "metrics_not_current" in record["issues"]
    assert record["fundamental_ready"] is False
    assert record["repair_eligible"] is True
    assert "metrics_not_current" not in record["deferred_reasons"]
    assert report["repair_targets"] == ["MISMATCH"]


# ---------------------------------------------------------------------------
# Alignment / ambiguity via the canonical matcher
# ---------------------------------------------------------------------------

def test_latest_bs_cf_missing_reports_cross_statement_missing(run_audit, tmp_store):
    tmp_store.upsert_income("ALIGN", _chain("2026-06-30", 2026, 2, n=5))
    tmp_store.upsert_balance_sheet("ALIGN", _chain("2026-03-31", 2026, 1, n=4))
    tmp_store.upsert_cash_flow("ALIGN", _chain("2026-06-30", 2026, 2, n=5))
    tmp_store.upsert_metrics("ALIGN", [{"date": "2026-06-30"}])
    for table in REQUIRED_TABLES:
        _seed_coverage(tmp_store, "ALIGN", table, status="ok")
    report = run_audit(["ALIGN"], refresh_days=400)
    record = _record(report, "ALIGN")
    assert "cross_statement_missing" in record["issues"]


def test_ambiguous_balance_quarter_is_reported_not_corrected(run_audit, tmp_store):
    income = _chain("2026-06-30", 2026, 2, n=5)
    tmp_store.upsert_income("AMBIG", income)
    tmp_store.upsert_cash_flow("AMBIG", _chain("2026-06-30", 2026, 2, n=5))
    tmp_store.upsert_metrics("AMBIG", [{"date": "2026-06-30"}])
    # Two balance rows share the latest income's fiscal identity. The current
    # writer rejects such an incoming batch, so seed directly: the audit must
    # report the ambiguity, never correct the rows.
    conn = tmp_store._get_conn()
    conn.execute(
        "INSERT INTO balance_sheet_quarterly (symbol, date, fiscal_year, period) "
        "VALUES ('AMBIG', '2026-06-30', '2026', 'Q2')")
    conn.execute(
        "INSERT INTO balance_sheet_quarterly (symbol, date, fiscal_year, period) "
        "VALUES ('AMBIG', '2026-06-29', '2026', 'Q2')")
    conn.commit()
    for table in REQUIRED_TABLES:
        _seed_coverage(tmp_store, "AMBIG", table, status="ok")
    report = run_audit(["AMBIG"], refresh_days=400)
    record = _record(report, "AMBIG")
    assert "ambiguous_fiscal_quarter" in record["issues"]
    # Rows are reported, never rewritten.
    assert len(tmp_store.get_balance_sheet("AMBIG")) == 2


# ---------------------------------------------------------------------------
# Cached event feed
# ---------------------------------------------------------------------------

def test_kr_style_near_fiscal_date_is_not_an_earnings_hint(run_audit, tmp_store):
    _seed_ready(tmp_store, "KR", latest="2026-08-15", fy=2026, q=3,
                last_success_at=SUCCESS_FRESH, last_attempt_at=SUCCESS_FRESH)
    _seed_earnings(tmp_store, "KR", announce_date="2026-08-09",
                   fiscal_date="2026-08-23", last_updated="2026-08-22T00:00:00Z")
    report = run_audit(["KR"])
    record = _record(report, "KR")
    assert "earnings_hint" not in record["issues"]
    assert "event_evidence_unknown" not in record["issues"]
    assert record["evidence"]["event_evidence"] == "none"


def test_fresh_valid_event_is_an_earnings_hint(run_audit, tmp_store):
    _seed_ready(tmp_store, "HINT", latest="2026-06-01", fy=2026, q=2,
                last_success_at=SUCCESS_OLD, last_attempt_at=SUCCESS_OLD)
    _seed_earnings(tmp_store, "HINT", announce_date="2026-08-05",
                   fiscal_date="2026-08-01", last_updated="2026-08-22T00:00:00Z")
    report = run_audit(["HINT"], refresh_days=400)
    record = _record(report, "HINT")
    assert "earnings_hint" in record["issues"]
    assert record["evidence"]["event_hint"]["fiscal_date"] == "2026-08-01"
    assert record["repair_eligible"] is True
    assert "HINT" in report["repair_targets"]


def test_stale_event_feed_is_unknown_warning_not_repair(run_audit, tmp_store):
    _seed_ready(tmp_store, "NOFEED", latest="2026-06-30", fy=2026, q=2,
                last_success_at=SUCCESS_FRESH, last_attempt_at=SUCCESS_FRESH)
    _seed_earnings(tmp_store, "NOFEED", announce_date="2026-06-20",
                   fiscal_date="2026-06-15", last_updated="2026-06-20T00:00:00Z")
    report = run_audit(["NOFEED"])
    record = _record(report, "NOFEED")
    assert "event_evidence_unknown" in record["issues"]
    assert "earnings_hint" not in record["issues"]
    assert record["repair_eligible"] is False
    assert report["status"] == "WARN"


def test_missing_earnings_table_is_explicit_unknown(run_audit, tmp_store):
    _seed_ready(tmp_store, "NOTABLE")
    tmp_store._get_conn().execute("DROP TABLE fmp_earnings")
    tmp_store._get_conn().commit()
    report = run_audit(["NOTABLE"])
    record = _record(report, "NOTABLE")
    assert record["evidence"]["event_evidence"] == "missing_table"
    assert "event_evidence_unknown" in record["issues"]


# ---------------------------------------------------------------------------
# Cooldown deferral / proactive verification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["stale", "missing", "healthy"])
@pytest.mark.parametrize("as_of,due", [
    ("2026-09-25T23:59:59Z", False),
    ("2026-09-26T00:00:00Z", True),
    ("2026-09-26T06:28:07Z", True),
    ("2026-09-26T07:01:38Z", True),
    ("2026-09-26T08:00:00+08:00", True),
])
def test_weekly_cooldown_due_by_utc_date(run_audit, tmp_store, kind, as_of, due):
    # Last week's collector finished later than this week's selection starts.
    stamp = "2026-09-19T06:59:52Z"
    latest = "2026-03-31" if kind == "stale" else "2026-06-30"
    _seed_ready(tmp_store, "WEEKLY", latest=latest,
                q=1 if kind == "stale" else 2,
                last_success_at=stamp, last_attempt_at=stamp)
    if kind == "missing":
        tmp_store._get_conn().execute("DELETE FROM cash_flow_quarterly WHERE symbol='WEEKLY'")
        tmp_store._get_conn().commit()
    report = run_audit(["WEEKLY"], as_of=as_of)
    target_key = "verification_targets" if kind == "healthy" else "repair_targets"
    assert ("WEEKLY" in report[target_key]) is due
    if kind != "healthy":
        assert ("WEEKLY" in report["deferred"]) is not due


def test_calendar_cooldown_does_not_advance_explicit_retry_timer(run_audit, tmp_store):
    stamp = "2026-09-19T06:59:52Z"
    _seed_ready(tmp_store, "RETRY", last_success_at=stamp, last_attempt_at=stamp,
                per_table={"income": {"status": "fetch_failed",
                                       "next_retry_at": "2026-09-26T07:00:00Z"}})
    before = run_audit(["RETRY"], as_of="2026-09-26T06:28:07Z")
    assert before["repair_targets"] == []
    assert "retry_timer_active" in before["symbols"]["RETRY"]["issues"]
    after = run_audit(["RETRY"], as_of="2026-09-26T07:00:00Z")
    assert after["repair_targets"] == ["RETRY"]


def test_recent_source_check_defers_persistent_fiscal_stale(run_audit, tmp_store):
    _seed_ready(tmp_store, "COOLDOWN", latest="2026-03-31", fy=2026, q=1,
                last_success_at=SUCCESS_FRESH, last_attempt_at=SUCCESS_FRESH)
    report = run_audit(["COOLDOWN"], refresh_days=400)
    record = _record(report, "COOLDOWN")
    assert "fiscal_stale" in record["issues"]
    assert record["repair_eligible"] is False
    assert "fiscal_stale" in record["deferred_reasons"]
    assert report["repair_targets"] == []
    assert "COOLDOWN" in report["deferred"]
    assert report["status"] == "WARN"


def test_healthy_old_verification_is_a_proactive_target(run_audit, tmp_store):
    _seed_ready(tmp_store, "PROACTIVE", latest="2026-06-30", fy=2026, q=2,
                last_success_at="2026-08-15T00:00:00Z",
                last_attempt_at="2026-08-15T00:00:00Z")
    _seed_neutral_earnings(tmp_store, "PROACTIVE", "2026-06-30")
    report = run_audit(["PROACTIVE"])
    assert report["status"] == "CLEAN"
    assert report["repair_targets"] == []
    assert report["issues"] == {}
    assert report["verification_targets"] == ["PROACTIVE"]


def test_recent_successes_are_not_proactive_targets(run_audit, tmp_store):
    _seed_ready(tmp_store, "FRESH", latest="2026-06-30", fy=2026, q=2,
                last_success_at=SUCCESS_FRESH, last_attempt_at=SUCCESS_FRESH)
    _seed_neutral_earnings(tmp_store, "FRESH", "2026-06-30")
    report = run_audit(["FRESH"])
    assert report["status"] == "CLEAN"
    assert report["verification_targets"] == []


def test_verification_targets_sort_oldest_success_first(run_audit, tmp_store):
    _seed_ready(tmp_store, "NEWER", last_success_at="2026-08-15T00:00:00Z",
                last_attempt_at="2026-08-15T00:00:00Z")
    _seed_ready(tmp_store, "OLDER", last_success_at="2026-08-01T00:00:00Z",
                last_attempt_at="2026-08-01T00:00:00Z")
    _seed_neutral_earnings(tmp_store, "NEWER", "2026-06-30")
    _seed_neutral_earnings(tmp_store, "OLDER", "2026-06-30")
    report = run_audit(["NEWER", "OLDER"])
    assert report["verification_targets"] == ["OLDER", "NEWER"]


# ---------------------------------------------------------------------------
# Repair priority and read-only guarantee
# ---------------------------------------------------------------------------

def test_repair_targets_sort_missing_metrics_then_failed_then_other(run_audit, tmp_store):
    _seed_ready(tmp_store, "FAILED", per_table={
        "income": {"status": "provider_empty", "next_retry_at": "2026-08-01T00:00:00Z"}})
    _seed_ready(tmp_store, "MISSING", per_table={"income": {"status": "ok"}})
    tmp_store._get_conn().execute("DELETE FROM income_quarterly WHERE symbol = 'MISSING'")
    tmp_store._get_conn().commit()
    _seed_coverage(tmp_store, "MISSING", "income_quarterly", status="ok",
                   last_attempt_at="2026-06-01T00:00:00Z",
                   last_success_at="2026-06-01T00:00:00Z")
    _seed_ready(tmp_store, "STALE", latest="2026-03-31", fy=2026, q=1,
                last_success_at=SUCCESS_OLD, last_attempt_at=SUCCESS_OLD)
    report = run_audit(["FAILED", "MISSING", "STALE"], refresh_days=400)
    assert report["repair_targets"][0] == "MISSING"
    assert report["repair_targets"][1] == "FAILED"
    assert "STALE" in report["repair_targets"]


def test_audit_performs_no_writes(run_audit, tmp_store):
    _seed_ready(tmp_store, "HEALTHY", latest="2026-03-31", fy=2026, q=1,
                last_success_at=SUCCESS_OLD, last_attempt_at=SUCCESS_OLD)
    _seed_ready(tmp_store, "DUE", per_table={
        "income": {"status": "fetch_failed", "next_retry_at": "2026-09-01T00:00:00Z"}})
    conn = tmp_store._get_conn()
    conn.commit()
    before = conn.total_changes
    run_audit(["HEALTHY", "DUE"])
    assert conn.total_changes == before
    # Coverage annotations were not persisted stale by the audit.
    statuses = tmp_store.get_coverage("income_quarterly")
    assert statuses["HEALTHY"] == "ok"
    assert statuses["DUE"] == "fetch_failed"


@pytest.mark.parametrize('attempt', ['2099-01-01T00:00:00Z', 'not-a-timestamp'])
def test_invalid_attempt_cannot_be_hidden_by_valid_success(run_audit, tmp_store, attempt):
    _seed_ready(tmp_store, 'BADATTEMPT', last_attempt_at=attempt)
    _seed_neutral_earnings(tmp_store, 'BADATTEMPT', '2026-06-30')
    report = run_audit(['BADATTEMPT'])
    assert 'verification_unknown' in report['symbols']['BADATTEMPT']['issues']
    assert report['status'] != 'CLEAN'


def test_stale_coverage_annotation_never_reports_clean(run_audit, tmp_store):
    _seed_ready(tmp_store, 'MARKED', status='stale')
    _seed_neutral_earnings(tmp_store, 'MARKED', '2026-06-30')
    report = run_audit(['MARKED'])
    assert 'statement_marked_stale' in report['symbols']['MARKED']['issues']
    assert report['status'] != 'CLEAN'


def test_empty_universe_fails_loud_instead_of_clean_zero(run_audit):
    with pytest.raises(RuntimeError, match='empty'):
        run_audit([])


def test_proactive_refresh_does_not_bypass_missing_source_cooldown(run_audit, tmp_store):
    _seed_ready(tmp_store, 'MISSING', last_success_at='2026-08-15T00:00:00Z')
    tmp_store._get_conn().execute("DELETE FROM income_quarterly WHERE symbol='MISSING'")
    tmp_store._get_conn().commit()
    report = run_audit(['MISSING'])
    assert 'MISSING' in report['deferred']
    assert 'MISSING' not in report['verification_targets']


def test_audit_uses_one_read_snapshot_during_concurrent_writer(run_audit, tmp_store, monkeypatch):
    import sqlite3
    _seed_ready(tmp_store, 'SNAP')
    _seed_neutral_earnings(tmp_store, 'SNAP', '2026-06-30')
    original=tmp_store.get_income
    def during_read(symbol, limit=20):
        rows=original(symbol,limit=limit)
        peer=sqlite3.connect(str(tmp_store.db_path))
        peer.execute("UPDATE metrics_quarterly SET date='2026-03-31' WHERE symbol='SNAP'")
        peer.commit();peer.close()
        return rows
    monkeypatch.setattr(tmp_store,'get_income',during_read)
    report=run_audit(['SNAP'])
    assert report['symbols']['SNAP']['evidence']['metrics_date']=='2026-06-30'
    assert not tmp_store._get_conn().in_transaction
    assert tmp_store.get_metrics('SNAP',limit=1)[0]['date']=='2026-03-31'
