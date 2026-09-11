"""Read-only verifier for the weekly index PE history.

Every test builds a small but *internally consistent* database, confirms the
verifier passes it, then breaks exactly one invariant and confirms the right
check goes red. That shape matters: a verifier that cannot pass a good
database is useless, and one that cannot fail a tampered one is worse.

Covered: manifest as denominator SSOT (including a narrowed expected range),
`mode=ro` discipline, quality_tier / P/E-null consistency, the quarter-sum and
weight-coverage assertions the store does not enforce, consensus vintage
surviving only inside members_json, heterogeneous reconciliation against raw
`historical_market_cap` / `income_quarterly` rows, forced-refresh provenance,
the R1 guard against holding-weighted columns, and SOXX's documented leading
gap being expected rather than a failure.
"""
import json
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts.verify_index_pe_history import (
    connect_readonly,
    parse_args,
    verify_database,
)
from src.data.market_store import MarketStore
from terminal.index_pe_weekly import (
    WEEKLY_METHODOLOGY_VERSION,
    expected_week_ends,
    weekly_calendar_hash,
    weekly_result_hash,
)


REPO_CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"

AS_OF = "2026-01-16"
YEARS = 5
EXPECTED_FROM = "2021-01-16"


@pytest.fixture
def config_dir(tmp_path):
    target = tmp_path / "baskets"
    shutil.copytree(REPO_CONFIG_DIR, target)
    return target


def _weekdays(start, end):
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    days = []
    while current <= final:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _accepted(fiscal_date, days=20):
    return (date.fromisoformat(fiscal_date)
            + timedelta(days=days)).isoformat() + " 16:00:00"


# The four quarters after the fixture's valuation dates, summing to a
# member's hindsight income -- the producer records the window it used, so
# the fixture does too.
HINDSIGHT_FISCAL = ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]


def _member(symbol, weight, market_cap, market_cap_date, ttm=25.0,
            hindsight=30.0):
    return {
        "symbol": symbol,
        "weight_pct": weight,
        "market_cap": market_cap,
        "market_cap_date": market_cap_date,
        "ttm_net_income_usd": ttm,
        "ttm_exclusion_reason": None,
        "hindsight_ntm_net_income_usd": hindsight,
        "hindsight_actual_quarters": 4,
        "hindsight_estimate_quarters": 0,
        "hindsight_exclusion_reason": None,
        "hindsight_window": [HINDSIGHT_FISCAL[0], HINDSIGHT_FISCAL[-1]],
    }


def _weekly_row(basket, valuation_date, members, quality_tier="actual_only",
                run_id="run-1", composition_effective="2021-01-18",
                composition_available="2021-01-25"):
    covered_ttm = [m for m in members
                   if m["market_cap"] is not None
                   and m["ttm_net_income_usd"] is not None]
    covered_hs = [m for m in members
                  if m["market_cap"] is not None
                  and m["hindsight_ntm_net_income_usd"] is not None]
    observed = sum(m["market_cap"] for m in members
                   if m["market_cap"] is not None)
    total_weight = sum(m["weight_pct"] for m in members)
    ttm_mcap = sum(m["market_cap"] for m in covered_ttm)
    ttm_income = sum(m["ttm_net_income_usd"] for m in covered_ttm)
    hs_mcap = sum(m["market_cap"] for m in covered_hs)
    hs_income = sum(m["hindsight_ntm_net_income_usd"] for m in covered_hs)
    return {
        "basket": basket,
        "valuation_date": valuation_date,
        "run_id": run_id,
        "ttm_pe_gaap": ttm_mcap / ttm_income,
        "hindsight_ntm_pe_gaap": hs_mcap / hs_income,
        "ttm_total_mcap": ttm_mcap,
        "ttm_net_income": ttm_income,
        "hindsight_total_mcap": hs_mcap,
        "hindsight_ntm_net_income": hs_income,
        "n_members": len(members),
        "n_covered_ttm": len(covered_ttm),
        "n_covered_hindsight": len(covered_hs),
        "mcap_coverage_ttm": ttm_mcap / observed,
        "mcap_coverage_hindsight": hs_mcap / observed,
        "hindsight_actual_quarters": 4,
        "hindsight_estimate_quarters": 0,
        "composition_effective_date": composition_effective,
        "composition_available_date": composition_available,
        "quality_tier": quality_tier,
        "members_json": {
            "holding_date": "2021-01-15",
            "weight_basis": "fixed_rebalance_weight_proxy",
            "consensus_snapshot_date": "2026-01-09",
            "is_ex_post": 1,
            "weight_coverage_ttm": sum(
                m["weight_pct"] for m in covered_ttm) / total_weight,
            "weight_coverage_hindsight": sum(
                m["weight_pct"] for m in covered_hs) / total_weight,
            "members": members,
        },
        "warnings_json": [],
        "methodology_version": WEEKLY_METHODOLOGY_VERSION,
    }


def _manifest_events(basket, run_id="run-1", expected_from=EXPECTED_FROM,
                     expected_to=AS_OF, extra=(), trading=(), rows=(),
                     composition_floor="2021-01-25"):
    """What the producer freezes: the week set, the calendar and the result.

    Mirrors `backfill_index_pe_history` rather than inventing a shape, so the
    verifier is tested against manifests of the kind it will actually meet.
    """
    window_calendar = [day for day in trading if expected_from <= day <= expected_to]
    base = {
        "run_id": run_id, "basket": basket, "frequency": "weekly",
        "expected_from_date": expected_from, "expected_to_date": expected_to,
        "methodology_version": WEEKLY_METHODOLOGY_VERSION,
        "target_count": 2, "target_universe_json": ["AAA", "BBB"],
    }
    events = [{**base, "event_seq": 0, "event_kind": "run_started",
               "payload_json": {
                   "started_at": "2026-01-16T00:00:00Z",
                   "composition_floor": composition_floor,
                   "expected_weeks": expected_week_ends(
                       window_calendar, expected_from, expected_to,
                       composition_floor),
                   "trading_days": len(window_calendar),
                   "calendar_hash": weekly_calendar_hash(window_calendar),
               }}]
    for offset, (kind, payload) in enumerate(extra, start=1):
        events.append({**base, "event_seq": offset, "event_kind": kind,
                       "payload_json": payload})
    events.append({**base, "event_seq": len(events),
                   "event_kind": "run_completed",
                   "payload_json": {"weekly_rows": len(rows),
                                    "result_hash": weekly_result_hash(rows)}})
    return events


def _build(tmp_path, *, basket="SPY", calendar=("2026-01-05", "2026-01-16"),
           row_dates=None, manifest_extra=(), expected_from=EXPECTED_FROM,
           market_caps=(("AAA", 900.0), ("BBB", 100.0)), ciks=None,
           market_cap_dates=None, composition_floor="2021-01-25",
           composition_effective="2021-01-18",
           composition_available="2021-01-25"):
    """A consistent database: calendar, raw sources, weekly rows, manifest."""
    store = MarketStore(tmp_path / "market.db")
    conn = store._get_conn()
    trading = _weekdays(*calendar)
    fiscal = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30"]
    # Four reported quarters before the valuation dates (TTM) and four after
    # (hindsight), so both reconciliations have something to rebuild from.
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_price "
            "(symbol, date, close) VALUES (?,?,?)",
            [(basket, day, 500.0) for day in trading])
        for symbol, market_cap in market_caps:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price (symbol, date, close) "
                "VALUES (?,?,?)", [(symbol, day, 10.0) for day in trading])
            conn.executemany(
                "INSERT OR REPLACE INTO historical_market_cap "
                "(symbol, date, market_cap) VALUES (?,?,?)",
                [(symbol, day, market_cap) for day in trading])
            conn.executemany(
                "INSERT OR REPLACE INTO income_quarterly "
                "(symbol, date, period, accepted_date, reported_currency, "
                "net_income) VALUES (?,?,?,?,?,?)",
                [(symbol, day, "Q1", _accepted(day), "USD", 6.25)
                 for day in fiscal]
                + [(symbol, day, "Q1", _accepted(day), "USD", 7.5)
                   for day in HINDSIGHT_FISCAL])
        identities = dict(ciks or {
            symbol: f"000000000{index + 1}"
            for index, (symbol, _) in enumerate(market_caps)})
        conn.executemany(
            "INSERT OR REPLACE INTO fmp_fund_disclosure_holdings "
            "(basket_symbol, holding_date, source_kind, raw_row_index, "
            "rebalance_close_date, composition_effective_date, "
            "composition_available_date, raw_symbol, symbol, cik, weight_pct, "
            "included, snapshot_warnings_json, fetched_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(basket, "2021-01-15", "disclosure", index, "2021-01-15",
              composition_effective, composition_available, symbol, symbol,
              identities.get(symbol), weight, 1, "[]",
              "2021-01-25T00:00:00Z", "2021-01-25T00:00:00Z")
             for index, ((symbol, _), weight)
             in enumerate(zip(market_caps, (60.0, 40.0)))])
    dates = list(row_dates if row_dates is not None
                 else ("2026-01-09", "2026-01-16"))
    members = [_member(symbol, weight, market_cap, None)
               for (symbol, market_cap), weight
               in zip(market_caps, (60.0, 40.0))]
    rows = []
    for valuation_date in dates:
        row_members = [
            dict(member, market_cap_date=(market_cap_dates or {}).get(
                valuation_date, valuation_date))
            for member in members]
        rows.append(_weekly_row(
            basket, valuation_date, row_members,
            composition_effective=composition_effective,
            composition_available=composition_available))
    if rows:
        store.upsert_basket_weekly_pe_batch(rows)
    store.append_basket_pe_run_events(_manifest_events(
        basket, expected_from=expected_from, extra=manifest_extra,
        trading=trading, rows=rows, composition_floor=composition_floor))
    store.close()
    return tmp_path / "market.db"


def _verify(db_path, config_dir, baskets=("SPY",), sample=8):
    conn = connect_readonly(db_path)
    try:
        return verify_database(conn, list(baskets), AS_OF, YEARS, sample,
                              config_dir)
    finally:
        conn.close()


def _failed(report):
    return {check["name"]: check["detail"]
            for check in report["checks"] if not check["passed"]}


def _mutate(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(sql, params)
    conn.close()


# ---------------------------------------------------------------------------
# the good case, and read-only discipline
# ---------------------------------------------------------------------------

def test_verifier_passes_a_consistent_database(tmp_path, config_dir):
    db_path = _build(tmp_path)
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)
    assert report["baskets"]["SPY"]["rows"] == 2
    assert report["baskets"]["SPY"]["published_ttm"] == 2
    assert report["baskets"]["SPY"]["manifest_window"] == [EXPECTED_FROM, AS_OF]
    detail = {check["name"]: check["detail"] for check in report["checks"]}
    assert detail["raw_source_spot_check"]["reconciled_market_caps"] == 4
    assert detail["raw_source_spot_check"]["reconciled_ttm_incomes"] == 4


def test_verifier_connection_refuses_writes(tmp_path, config_dir):
    db_path = _build(tmp_path)
    conn = connect_readonly(db_path)
    try:
        with pytest.raises(sqlite3.Error):
            conn.execute(
                "UPDATE basket_weekly_pe_history SET ttm_pe_gaap = 1.0")
    finally:
        conn.close()


def test_verification_does_not_modify_the_database(tmp_path, config_dir):
    db_path = _build(tmp_path)
    before = db_path.read_bytes()
    _verify(db_path, config_dir)
    assert db_path.read_bytes() == before


def test_exit_code_is_nonzero_when_a_check_fails(tmp_path, config_dir, capsys):
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    from scripts.verify_index_pe_history import main
    code = main(["--baskets", "SPY", "--as-of", AS_OF, "--years", str(YEARS),
                 "--mode", "ro", "--db", str(db_path),
                 "--config-dir", str(config_dir)])
    assert code == 1
    assert "no_completed_run_manifest" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# manifest as the denominator SSOT
# ---------------------------------------------------------------------------

def test_missing_manifest_fails_the_denominator_check(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    failures = _failed(_verify(db_path, config_dir))
    assert "manifest_denominator" in failures
    assert any("no_completed_run_manifest" in str(item)
               for item in failures["manifest_denominator"])


def test_a_run_that_never_completed_is_not_accepted(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs "
                     "WHERE event_kind = 'run_completed'")
    assert "manifest_denominator" in _failed(_verify(db_path, config_dir))


def test_manifest_range_narrower_than_requested_is_rejected(tmp_path, config_dir):
    """issue048: a late expected_from would certify a suffix as a full history."""
    db_path = _build(tmp_path, expected_from="2025-12-01")
    failures = _failed(_verify(db_path, config_dir))
    assert any("manifest_range_narrower_than_requested" in str(item)
               for item in failures["manifest_denominator"])


def test_manifest_event_sequence_must_be_append_only(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_pe_backfill_runs SET event_seq = 7 "
                     "WHERE event_kind = 'run_completed'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("event_seq_not_append_only" in str(item)
               for item in failures["manifest_denominator"])


def test_forced_refresh_event_needs_hashes_and_a_covered_trigger(
        tmp_path, config_dir):
    good = _build(tmp_path / "good", manifest_extra=[(
        "forced_refresh", {"symbol": "AAA", "from_date": "2026-01-05",
                           "to_date": "2026-01-16",
                           "trigger_dates": ["2026-01-08"],
                           "pre_row_hash": "a" * 64, "post_row_hash": "b" * 64,
                           "pre_row_count": 3, "post_row_count": 3,
                           "skipped": False})])
    assert _verify(good, config_dir)["passed"]

    bad = _build(tmp_path / "bad", manifest_extra=[(
        "forced_refresh", {"symbol": "AAA", "from_date": "2026-01-05",
                           "to_date": "2026-01-16",
                           "trigger_dates": ["2025-01-08"],
                           "pre_row_hash": "", "post_row_hash": "b" * 64,
                           "skipped": False})])
    failures = _failed(_verify(bad, config_dir))
    assert any("pre_row_hash_not_a_sha256" in str(item)
               for item in failures["forced_refresh_provenance"])
    assert any("trigger_outside_window" in str(item)
               for item in failures["forced_refresh_provenance"])


# ---------------------------------------------------------------------------
# expected weekly denominator and the documented SOXX gap
# ---------------------------------------------------------------------------

def test_a_missing_week_inside_the_expected_range_fails(tmp_path, config_dir):
    db_path = _build(tmp_path, row_dates=["2026-01-16"])
    failures = _failed(_verify(db_path, config_dir))
    assert any("expected_week_has_no_row" in str(item)
               for item in failures["expected_weekly_denominator"])


def test_two_rows_in_one_week_fail(tmp_path, config_dir):
    db_path = _build(tmp_path,
                     row_dates=["2026-01-09", "2026-01-15", "2026-01-16"])
    failures = _failed(_verify(db_path, config_dir))
    assert any("more_than_one_row_in_one_week" in str(item)
               for item in failures["expected_weekly_denominator"])


def test_soxx_leading_gap_is_expressed_by_the_frozen_floor(
        tmp_path, config_dir):
    """SOXX has no verifiable disclosure before 2021-09-20 (basket config).

    That gap is declared once, in the run's frozen composition floor, so the
    weeks before it are never in the expected set to begin with. There is no
    second exemption at comparison time: everything the frozen set contains is
    owed, and the manifest checks reject a frozen set that reaches below the
    documented bound.
    """
    db_path = _build(
        tmp_path, basket="SOXX", calendar=("2021-08-02", "2021-10-15"),
        composition_floor="2021-09-20", composition_effective="2021-09-17",
        composition_available="2021-09-20",
        row_dates=["2021-09-24", "2021-10-01", "2021-10-08", "2021-10-15"])
    report = _verify(db_path, config_dir, baskets=("SOXX",))
    assert report["passed"], _failed(report)
    assert report["baskets"]["SOXX"]["expected_weeks"] == 4
    assert report["baskets"]["SOXX"]["composition_floor"] == "2021-09-20"


def test_a_week_after_the_soxx_boundary_is_still_owed(tmp_path, config_dir):
    """The gap excuses the pre-history, never a week the run claimed."""
    db_path = _build(
        tmp_path, basket="SOXX", calendar=("2021-08-02", "2021-10-15"),
        composition_floor="2021-09-20", composition_effective="2021-09-17",
        composition_available="2021-09-20",
        row_dates=["2021-09-24", "2021-10-08", "2021-10-15"])
    failures = _failed(_verify(db_path, config_dir, baskets=("SOXX",)))
    assert any("2021-10-01:expected_week_has_no_row" in str(item)
               for item in failures["expected_weekly_denominator"]), failures


# ---------------------------------------------------------------------------
# store gaps the verifier has to cover
# ---------------------------------------------------------------------------

def test_unpublishable_row_carrying_a_hindsight_pe_is_caught(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET quality_tier = 'unpublishable' "
                     "WHERE valuation_date = '2026-01-16'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("unpublishable_with_a_hindsight_pe" in str(item)
               for item in failures["quality_tier_and_gate_consistency"])


def test_publishable_tier_without_a_hindsight_pe_is_caught(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET hindsight_ntm_pe_gaap = NULL "
                     "WHERE valuation_date = '2026-01-16'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("publishable_tier_without_a_hindsight_pe" in str(item)
               for item in failures["quality_tier_and_gate_consistency"])


def test_row_level_quarter_sum_is_enforced_by_the_schema_itself(
        tmp_path, config_dir):
    """The one part of this contract the store does enforce, via a CHECK."""
    db_path = _build(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _mutate(db_path, "UPDATE basket_weekly_pe_history "
                         "SET hindsight_actual_quarters = 3 "
                         "WHERE valuation_date = '2026-01-16'")


def test_member_quarters_that_do_not_sum_to_four_are_caught(
        tmp_path, config_dir):
    """No CHECK reaches inside members_json, so the verifier has to.

    A covered member contributing three quarters of earnings against a full
    year of market cap understates the P/E, and the row-level counters would
    still add to four.
    """
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'").fetchone()
    payload = json.loads(row[0])
    payload["members"][0]["hindsight_actual_quarters"] = 3
    with conn:
        conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = '2026-01-16'",
                     [json.dumps(payload)])
    conn.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("member_quarters_not_four" in str(item)
               for item in failures["materialised_evidence"])


def test_a_null_ttm_pe_without_a_recorded_reason_is_caught(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET ttm_pe_gaap = NULL "
                     "WHERE valuation_date = '2026-01-16'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("ttm_null_without_a_recorded_reason" in str(item)
               for item in failures["quality_tier_and_gate_consistency"])


def test_publishing_below_the_mcap_gate_is_caught(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET mcap_coverage_ttm = 0.5 "
                     "WHERE valuation_date = '2026-01-16'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("ttm_published_below_mcap_gate" in str(item)
               for item in failures["quality_tier_and_gate_consistency"])


def test_weight_coverage_must_be_present_and_reproducible(tmp_path, config_dir):
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'").fetchone()
    payload = json.loads(row[0])
    payload.pop("weight_coverage_hindsight")
    payload["weight_coverage_ttm"] = 0.42
    with conn:
        conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = '2026-01-16'",
                     [json.dumps(payload)])
    conn.close()
    failures = _failed(_verify(db_path, config_dir))
    detail = failures["materialised_evidence"]
    assert any("weight_coverage_hindsight_missing" in str(item)
               for item in detail)
    assert any("weight_coverage_ttm_not_reproducible" in str(item)
               for item in detail)


def test_consensus_vintage_must_survive_inside_members_json(
        tmp_path, config_dir):
    """is_ex_post and the vintage are dropped by the store's column filter."""
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'").fetchone()
    payload = json.loads(row[0])
    payload.pop("consensus_snapshot_date")
    payload["is_ex_post"] = 0
    with conn:
        conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = '2026-01-16'",
                     [json.dumps(payload)])
    conn.close()
    detail = _failed(_verify(db_path, config_dir))["materialised_evidence"]
    assert any("consensus_vintage_missing" in str(item) for item in detail)
    assert any("hindsight_not_flagged_ex_post" in str(item) for item in detail)


def test_a_tampered_total_is_caught_against_its_own_members(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET ttm_total_mcap = ttm_total_mcap * 2 "
                     "WHERE valuation_date = '2026-01-16'")
    detail = _failed(_verify(db_path, config_dir))["materialised_evidence"]
    assert any("ttm_total_mcap_not_reproducible" in str(item)
               for item in detail)


def test_a_tampered_pe_is_caught_by_plain_arithmetic(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET ttm_pe_gaap = 12.5 "
                     "WHERE valuation_date = '2026-01-16'")
    detail = _failed(_verify(db_path, config_dir))["aggregate_reconciliation"]
    assert any("ttm_pe_gaap_not_reproducible" in str(item) for item in detail)


def test_a_member_market_cap_that_raw_sources_deny_is_caught(
        tmp_path, config_dir):
    """A self-consistent row is still checked against the raw source rows."""
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json, ttm_total_mcap, ttm_net_income "
        "FROM basket_weekly_pe_history WHERE valuation_date = '2026-01-16'"
    ).fetchone()
    payload = json.loads(row[0])
    payload["members"][0]["market_cap"] = 1800.0
    total = sum(member["market_cap"] for member in payload["members"])
    observed = total
    income = sum(member["ttm_net_income_usd"] for member in payload["members"])
    with conn:
        conn.execute(
            "UPDATE basket_weekly_pe_history SET members_json = ?, "
            "ttm_total_mcap = ?, ttm_pe_gaap = ?, mcap_coverage_ttm = 1.0, "
            "hindsight_total_mcap = ?, hindsight_ntm_pe_gaap = ?, "
            "mcap_coverage_hindsight = 1.0 "
            "WHERE valuation_date = '2026-01-16'",
            [json.dumps(payload), total, total / income, total,
             total / sum(member["hindsight_ntm_net_income_usd"]
                         for member in payload["members"])])
    conn.close()
    report = _verify(db_path, config_dir)
    detail = _failed(report)["raw_source_spot_check"]
    assert any("market_cap_disagrees_with_raw_source" in str(item)
               for item in detail["errors"])


def test_a_member_income_that_raw_sources_deny_is_caught(tmp_path, config_dir):
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'").fetchone()
    payload = json.loads(row[0])
    payload["members"][0]["ttm_net_income_usd"] = 250.0
    income = sum(member["ttm_net_income_usd"] for member in payload["members"])
    total = sum(member["market_cap"] for member in payload["members"])
    with conn:
        conn.execute(
            "UPDATE basket_weekly_pe_history SET members_json = ?, "
            "ttm_net_income = ?, ttm_pe_gaap = ? "
            "WHERE valuation_date = '2026-01-16'",
            [json.dumps(payload), income, total / income])
    conn.close()
    detail = _failed(_verify(db_path, config_dir))["raw_source_spot_check"]
    assert any("ttm_income_disagrees_with_raw_source" in str(item)
               for item in detail["errors"])


def test_a_market_cap_dated_after_the_valuation_date_is_caught(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-09'").fetchone()
    payload = json.loads(row[0])
    payload["members"][0]["market_cap_date"] = "2026-01-16"
    with conn:
        conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = '2026-01-09'",
                     [json.dumps(payload)])
    conn.close()
    detail = _failed(_verify(db_path, config_dir))["materialised_evidence"]
    assert any("mcap_date_crossing" in str(item) for item in detail)


def test_a_quarantined_market_cap_behind_a_published_row_is_caught(
        tmp_path, config_dir):
    """The status is rederived from raw tables, never read back from the row."""
    db_path = _build(tmp_path)
    # A 10:1 market-cap cliff against a flat price is issue035's shape. The
    # cliff is placed *before* the dates the rows use, so the published value
    # still matches the raw row and only a fresh sanity scan can object.
    _mutate(db_path, "UPDATE historical_market_cap SET market_cap = 90.0 "
                     "WHERE symbol = 'AAA' AND date <= '2026-01-13'")
    detail = _failed(_verify(db_path, config_dir))["raw_source_spot_check"]
    assert any("published_on_invalid_mcap_market_cap" in str(item)
               for item in detail["errors"]), detail["errors"]


# ---------------------------------------------------------------------------
# R1 guard + methodology version
# ---------------------------------------------------------------------------

def test_a_holding_weighted_column_fails_the_caliber_guard(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "ALTER TABLE basket_weekly_pe_history "
                     "ADD COLUMN rebalance_weighted_ttm_pe_gaap_proxy REAL")
    failures = _failed(_verify(db_path, config_dir))
    assert failures["no_holding_weighted_columns"]["forbidden_present"] == [
        "rebalance_weighted_ttm_pe_gaap_proxy"]


def test_an_unexpected_methodology_version_is_caught(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET methodology_version = '2.0'")
    assert "methodology_version" in _failed(_verify(db_path, config_dir))


def test_cli_defaults_to_read_only_mode_and_five_years(tmp_path):
    args = parse_args(["--baskets", "SPY,QQQ,SOXX", "--as-of", AS_OF,
                       "--db", str(tmp_path / "x.db")])
    assert args.mode == "ro"
    assert args.years == 5
    assert args.baskets == ["SPY", "QQQ", "SOXX"]


# ---------------------------------------------------------------------------
# Fix round 1 / Important 3: rows written by a run that never certified itself
# ---------------------------------------------------------------------------

def test_a_run_that_wrote_rows_but_never_certified_them_fails(
        tmp_path, config_dir):
    """The weekly batch commits before run_completed is appended.

    A run killed in that gap leaves rows behind with no manifest saying which
    universe or which window produced them. Without this check the previous
    run's frozen manifest would certify them.
    """
    db_path = _build(tmp_path)
    store = MarketStore(db_path)
    try:
        store.append_basket_pe_run_events([{
            "run_id": "run-2", "basket": "SPY", "event_seq": 0,
            "event_kind": "run_started", "frequency": "weekly",
            "expected_from_date": EXPECTED_FROM, "expected_to_date": AS_OF,
            "methodology_version": WEEKLY_METHODOLOGY_VERSION,
            "target_count": 2, "target_universe_json": ["AAA", "BBB"],
            "payload_json": {"started_at": "2026-01-17T00:00:00Z"},
        }])
    finally:
        store.close()
    failures = _failed(_verify(db_path, config_dir))
    assert "manifest_denominator" in failures
    assert any("unfinished_run" in str(item) and "run-2" in str(item)
               for item in failures["manifest_denominator"]), \
        failures["manifest_denominator"]


def test_a_run_that_failed_and_said_so_does_not_block_verification(
        tmp_path, config_dir):
    """A recorded failure is accounted for; only silence is not."""
    db_path = _build(tmp_path)
    store = MarketStore(db_path)
    try:
        store.append_basket_pe_run_events([
            {"run_id": "run-0", "basket": "SPY", "event_seq": 0,
             "event_kind": "run_started", "frequency": "weekly",
             "expected_from_date": EXPECTED_FROM, "expected_to_date": AS_OF,
             "methodology_version": WEEKLY_METHODOLOGY_VERSION,
             "target_count": 2, "target_universe_json": ["AAA", "BBB"],
             "payload_json": {}},
            {"run_id": "run-0", "basket": "SPY", "event_seq": 1,
             "event_kind": "run_failed", "frequency": "weekly",
             "expected_from_date": EXPECTED_FROM, "expected_to_date": AS_OF,
             "methodology_version": WEEKLY_METHODOLOGY_VERSION,
             "target_count": 2, "target_universe_json": ["AAA", "BBB"],
             "payload_json": {"error": "ValueError: boom"}},
        ])
    finally:
        store.close()
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)
    assert report["baskets"]["SPY"]["manifest_run_id"] == "run-1"


# ---------------------------------------------------------------------------
# Final review / I1: two tickers of one company that the config never covered
# ---------------------------------------------------------------------------

def test_two_covered_members_sharing_a_cik_fail_closed(tmp_path, config_dir):
    """The historical dual-class gap, caught structurally rather than by list.

    share_class_groups.json covers today's members. Over five years SPY held
    pairs that are gone now (DISCA/DISCK until 2022-04), and FMP files the full
    company's net income under both classes, so an uncovered pair divides one
    company's market cap by twice its earnings. The TTM aggregate has no
    duplicate-company backstop at all, so the verifier keys on the company
    identity the disclosure itself carries.
    """
    db_path = _build(tmp_path, ciks={"AAA": "0001437107", "BBB": "0001437107"})
    failures = _failed(_verify(db_path, config_dir))
    assert "company_identity_uniqueness" in failures, failures
    errors = failures["company_identity_uniqueness"]["errors"]
    assert any("duplicate_company_cik" in str(item) and "0001437107" in str(item)
               for item in errors), errors


def test_distinct_ciks_pass_and_are_counted(tmp_path, config_dir):
    db_path = _build(tmp_path)
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)
    detail = {check["name"]: check["detail"] for check in report["checks"]}
    assert detail["company_identity_uniqueness"]["members_with_cik"] > 0
    assert detail["company_identity_uniqueness"]["members_without_cik"] == 0


def test_missing_company_identity_fails_rather_than_skipping(tmp_path, config_dir):
    """A check that cannot see company identity has to say so, not pass."""
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE fmp_fund_disclosure_holdings SET cik = NULL")
    failures = _failed(_verify(db_path, config_dir))
    errors = failures["company_identity_uniqueness"]["errors"]
    assert any("company_identity_unavailable" in str(item) for item in errors)


# ---------------------------------------------------------------------------
# Final review / Minor 2: staleness comes from the basket config
# ---------------------------------------------------------------------------

def test_market_cap_staleness_tolerance_comes_from_the_basket_config(
        tmp_path, config_dir):
    """The producer reads market_cap_staleness_days per basket; so must this.

    A 7-day-old observation is fine under SPY's configured 7 and stale under a
    basket configured for 3. A hardcoded 7 in the verifier would bless data the
    producer's own gate would have rejected.
    """
    payload = json.loads((config_dir / "index_pe_baskets.json").read_text())
    payload["SPY"]["market_cap_staleness_days"] = 3
    (config_dir / "index_pe_baskets.json").write_text(json.dumps(payload))
    db_path = _build(tmp_path,
                     market_cap_dates={"2026-01-16": "2026-01-09"})
    failures = _failed(_verify(db_path, config_dir))
    assert any("mcap_date_stale" in str(item)
               for item in failures["materialised_evidence"]), failures


def test_seven_day_observation_is_fine_under_the_configured_seven(
        tmp_path, config_dir):
    db_path = _build(tmp_path,
                     market_cap_dates={"2026-01-16": "2026-01-09"})
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)


# ---------------------------------------------------------------------------
# Boss review P1-1 (verifier side) and P1-4 (CIK fail-open)
# ---------------------------------------------------------------------------

def test_a_row_using_a_composition_before_its_disclosure_is_caught(
        tmp_path, config_dir):
    """The producer's gate and the verifier's must not share the same blind spot."""
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history "
                     "SET composition_available_date = '2026-02-15' "
                     "WHERE valuation_date = '2026-01-16'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("composition_not_yet_disclosed" in str(item)
               for item in failures["materialised_evidence"]), failures


def test_one_unresolvable_cik_fails_the_identity_check(tmp_path, config_dir):
    """Boss review P1-4: counting the gap is not closing it.

    Two members and one resolvable CIK is exactly the case the check exists
    for -- the unresolved one is where an uncovered dual-class pair hides.
    """
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE fmp_fund_disclosure_holdings SET cik = NULL "
                     "WHERE raw_symbol = 'BBB'")
    failures = _failed(_verify(db_path, config_dir))
    errors = failures["company_identity_uniqueness"]["errors"]
    assert any("company_identity_unresolved" in str(item) and "BBB" in str(item)
               for item in errors), errors


# ---------------------------------------------------------------------------
# Boss review P1-2: the manifest is the immutable denominator
# ---------------------------------------------------------------------------

def test_deleting_a_week_and_its_calendar_still_fails(tmp_path, config_dir):
    """Boss repro: delete a week's row *and* that week's basket prices.

    Recomputing the expected weeks from the live calendar lets an attacker (or
    a bad sync) erase the evidence of the erasure. The denominator has to come
    from what the run froze, not from tables that changed since.
    """
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_weekly_pe_history "
                     "WHERE valuation_date = '2026-01-09'")
    _mutate(db_path, "DELETE FROM daily_price WHERE symbol = 'SPY' "
                     "AND date BETWEEN '2026-01-05' AND '2026-01-09'")
    failures = _failed(_verify(db_path, config_dir))
    assert failures, "erasing the calendar must not erase the expectation"
    assert ("expected_weekly_denominator" in failures
            or "manifest_result_integrity" in failures), failures


def test_result_hash_catches_a_deleted_row(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_weekly_pe_history "
                     "WHERE valuation_date = '2026-01-09'")
    failures = _failed(_verify(db_path, config_dir))
    assert "manifest_result_integrity" in failures, failures


def test_result_hash_catches_a_tampered_published_value(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET ttm_pe_gaap = 9.99 "
                     "WHERE valuation_date = '2026-01-16'")
    failures = _failed(_verify(db_path, config_dir))
    assert "manifest_result_integrity" in failures, failures


def test_calendar_drift_since_the_run_is_reported(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM daily_price WHERE symbol = 'SPY' "
                     "AND date = '2026-01-07'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("calendar_changed_since_the_run" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_run_that_failed_after_writing_rows_blocks_certification(
        tmp_path, config_dir):
    """No falling back to the last good manifest to bless newer writes."""
    db_path = _build(tmp_path)
    store = MarketStore(db_path)
    try:
        base = {
            "run_id": "run-2", "basket": "SPY", "frequency": "weekly",
            "expected_from_date": EXPECTED_FROM, "expected_to_date": AS_OF,
            "methodology_version": WEEKLY_METHODOLOGY_VERSION,
            "target_count": 2, "target_universe_json": ["AAA", "BBB"],
        }
        store.append_basket_pe_run_events([
            {**base, "event_seq": 0, "event_kind": "run_started",
             "payload_json": {}},
            {**base, "event_seq": 1, "event_kind": "run_failed",
             "payload_json": {"error": "RuntimeError: boom",
                              "rows_written": True}},
        ])
    finally:
        store.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("wrote_rows_then_failed" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_run_that_failed_before_writing_does_not_block(tmp_path, config_dir):
    db_path = _build(tmp_path)
    store = MarketStore(db_path)
    try:
        base = {
            "run_id": "run-2", "basket": "SPY", "frequency": "weekly",
            "expected_from_date": EXPECTED_FROM, "expected_to_date": AS_OF,
            "methodology_version": WEEKLY_METHODOLOGY_VERSION,
            "target_count": 2, "target_universe_json": ["AAA", "BBB"],
        }
        store.append_basket_pe_run_events([
            {**base, "event_seq": 0, "event_kind": "run_started",
             "payload_json": {}},
            {**base, "event_seq": 1, "event_kind": "run_failed",
             "payload_json": {"error": "ValueError: universe is empty",
                              "rows_written": False}},
        ])
    finally:
        store.close()
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)


# ---------------------------------------------------------------------------
# Boss review P1-3: the hindsight number needs its own source reconciliation
# ---------------------------------------------------------------------------

def _future_quarters(db_path, symbol, values, currency="USD"):
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO income_quarterly (symbol, date, period, "
            "accepted_date, reported_currency, net_income) VALUES (?,?,?,?,?,?)",
            [(symbol, fiscal, "Q1", _accepted(fiscal), currency, value)
             for fiscal, value in values])
    conn.close()


def _with_hindsight_sources(db_path, per_quarter=7.5):
    """The four quarters that follow the valuation dates, 30.0 a year."""
    for symbol in ("AAA", "BBB"):
        _future_quarters(db_path, symbol,
                         [(fiscal, per_quarter) for fiscal in HINDSIGHT_FISCAL])


def _set_member_window(db_path, valuation_date="2026-01-16"):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = ?", [valuation_date]).fetchone()
    payload = json.loads(row[0])
    for member in payload["members"]:
        member["hindsight_window"] = [HINDSIGHT_FISCAL[0], HINDSIGHT_FISCAL[-1]]
    with conn:
        conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = ?",
                     [json.dumps(payload), valuation_date])
    conn.close()


def test_hindsight_income_is_reconciled_against_raw_sources(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _with_hindsight_sources(db_path)
    for valuation_date in ("2026-01-09", "2026-01-16"):
        _set_member_window(db_path, valuation_date)
    report = _verify(db_path, config_dir)
    detail = {check["name"]: check["detail"]
              for check in report["checks"]}["raw_source_spot_check"]
    assert detail["reconciled_hindsight_incomes"] > 0, detail
    assert report["passed"], _failed(report)


def test_a_tampered_hindsight_income_is_caught(tmp_path, config_dir):
    """Boss repro: one member's hindsight income 30 -> 3000, totals kept
    self-consistent. Every existing check passed; only a rebuild from
    income_quarterly can object."""
    db_path = _build(tmp_path)
    _with_hindsight_sources(db_path)
    for valuation_date in ("2026-01-09", "2026-01-16"):
        _set_member_window(db_path, valuation_date)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT members_json FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'").fetchone()
    payload = json.loads(row[0])
    payload["members"][0]["hindsight_ntm_net_income_usd"] = 3000.0
    income = sum(member["hindsight_ntm_net_income_usd"]
                 for member in payload["members"])
    total = sum(member["market_cap"] for member in payload["members"])
    with conn:
        conn.execute(
            "UPDATE basket_weekly_pe_history SET members_json = ?, "
            "hindsight_ntm_net_income = ?, hindsight_ntm_pe_gaap = ? "
            "WHERE valuation_date = '2026-01-16'",
            [json.dumps(payload), income, total / income])
    conn.close()
    detail = _failed(_verify(db_path, config_dir)).get("raw_source_spot_check")
    assert detail is not None, "the tamper went unnoticed"
    assert any("hindsight_income_disagrees_with_raw_source" in str(item)
               for item in detail["errors"]), detail["errors"]


# ---------------------------------------------------------------------------
# Frozen denominator, both directions: missing rows AND surplus rows
# ---------------------------------------------------------------------------

def _orphan_row(db_path, basket, valuation_date, run_id="run-0"):
    """A row of the shape a failed run leaves behind, written directly."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO basket_weekly_pe_history "
            "(basket, valuation_date, run_id, ttm_pe_gaap, hindsight_ntm_pe_gaap, "
            "ttm_total_mcap, ttm_net_income, hindsight_total_mcap, "
            "hindsight_ntm_net_income, n_members, n_covered_ttm, "
            "n_covered_hindsight, mcap_coverage_ttm, mcap_coverage_hindsight, "
            "hindsight_actual_quarters, hindsight_estimate_quarters, "
            "composition_effective_date, composition_available_date, "
            "quality_tier, members_json, warnings_json, methodology_version, "
            "created_at, last_updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [basket, valuation_date, run_id, 20.0, 18.0, 1000.0, 50.0, 1000.0, 55.0,
             2, 2, 2, 1.0, 1.0, 4, 0, "2021-01-18", "2021-01-25",
             "actual_only", json.dumps({"members": []}), "[]",
             WEEKLY_METHODOLOGY_VERSION, "2026-01-16T00:00:00Z",
             "2026-01-16T00:00:00Z"])
    conn.close()


def _failed_run_events(store, basket, run_id, expected_from, expected_to):
    base = {
        "run_id": run_id, "basket": basket, "frequency": "weekly",
        "expected_from_date": expected_from, "expected_to_date": expected_to,
        "methodology_version": WEEKLY_METHODOLOGY_VERSION,
        "target_count": 2, "target_universe_json": ["AAA", "BBB"],
    }
    store.append_basket_pe_run_events([
        {**base, "event_seq": 0, "event_kind": "run_started",
         "payload_json": {}},
        {**base, "event_seq": 1, "event_kind": "run_failed",
         "payload_json": {"error": "RuntimeError: boom", "rows_written": True}},
    ])


def test_rows_a_failed_run_left_outside_the_rerun_window_are_caught(
        tmp_path, config_dir):
    """A surplus row must not escape by sitting outside the expected set.

    The certifying run's window is the five years back from as-of. A failed
    run that wrote 2019 rows leaves them certified by nothing at all -- and
    they are invisible to a denominator that only ever looks inside the
    window it was handed.
    """
    db_path = _build(tmp_path)
    _orphan_row(db_path, "SPY", "2019-06-28")
    store = MarketStore(db_path)
    try:
        _failed_run_events(store, "SPY", "run-2", "2014-06-28", "2019-06-28")
    finally:
        store.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("row_outside_every_certified_window" in str(item)
               and "2019-06-28" in str(item)
               for item in failures["expected_weekly_denominator"]), failures


def test_a_surplus_row_inside_the_window_is_caught(tmp_path, config_dir):
    """Same rule inside the window: a week the run never claimed is surplus."""
    db_path = _build(tmp_path)
    _orphan_row(db_path, "SPY", "2025-11-14")
    failures = _failed(_verify(db_path, config_dir))
    assert any("row_outside_the_expected_range" in str(item)
               for item in failures["expected_weekly_denominator"]), failures


def test_rows_of_an_older_completed_run_are_not_treated_as_surplus(
        tmp_path, config_dir):
    """An earlier run that certified its own window is not an orphan."""
    db_path = _build(tmp_path)
    _orphan_row(db_path, "SPY", "2019-06-28")
    store = MarketStore(db_path)
    try:
        store.append_basket_pe_run_events(_manifest_events(
            "SPY", run_id="run-0", expected_from="2014-06-28",
            expected_to="2019-06-28", trading=["2019-06-28"], rows=[]))
    finally:
        store.close()
    denominator = [item for item in _failed(_verify(db_path, config_dir)).get(
        "expected_weekly_denominator", [])
        if "row_outside_every_certified_window" in str(item)]
    assert not denominator, denominator


# ---------------------------------------------------------------------------
# Fix round 2 / F1: a run cannot both fail and complete
# ---------------------------------------------------------------------------

def test_a_run_completed_appended_to_a_failed_run_is_rejected(
        tmp_path, config_dir):
    """The append-only log stops rewrites, not appends.

    A failed run's out-of-window leftovers were whitewashed by appending a
    run_completed to that same run id -- no rewrite, no rejected write, and
    the payload was never looked at because only the certifying run's payload
    was validated. An event set claiming both outcomes is self-contradictory
    and cannot vouch for anything.
    """
    db_path = _build(tmp_path)
    _orphan_row(db_path, "SPY", "2019-06-28")
    store = MarketStore(db_path)
    base = {
        "run_id": "run-0", "basket": "SPY", "frequency": "weekly",
        "expected_from_date": "2014-06-28", "expected_to_date": "2019-06-28",
        "methodology_version": WEEKLY_METHODOLOGY_VERSION,
        "target_count": 2, "target_universe_json": ["AAA", "BBB"],
    }
    try:
        store.append_basket_pe_run_events([
            {**base, "event_seq": 0, "event_kind": "run_started",
             "payload_json": {}},
            {**base, "event_seq": 1, "event_kind": "run_failed",
             "payload_json": {"error": "RuntimeError: boom",
                              "rows_written": True}},
        ])
        # the attack: append a completion to the run that already failed
        store.append_basket_pe_run_events([
            {**base, "event_seq": 2, "event_kind": "run_completed",
             "payload_json": {"weekly_rows": 1, "result_hash": "deadbeef"}},
        ])
    finally:
        store.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("run_both_failed_and_completed" in str(item)
               for item in failures["manifest_denominator"]), failures
    # and the whitewash must not work: the orphan is still uncertified
    assert any("row_outside_every_certified_window" in str(item)
               for item in failures["expected_weekly_denominator"]), failures


def test_a_contradictory_run_cannot_become_the_certifying_run(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    store = MarketStore(db_path)
    base = {
        "run_id": "run-2", "basket": "SPY", "frequency": "weekly",
        "expected_from_date": EXPECTED_FROM, "expected_to_date": AS_OF,
        "methodology_version": WEEKLY_METHODOLOGY_VERSION,
        "target_count": 2, "target_universe_json": ["AAA", "BBB"],
    }
    try:
        store.append_basket_pe_run_events([
            {**base, "event_seq": 0, "event_kind": "run_started",
             "payload_json": {}},
            {**base, "event_seq": 1, "event_kind": "run_failed",
             "payload_json": {"rows_written": False}},
            {**base, "event_seq": 2, "event_kind": "run_completed",
             "payload_json": {"weekly_rows": 2, "result_hash": "deadbeef"}},
        ])
    finally:
        store.close()
    report = _verify(db_path, config_dir)
    assert report["baskets"]["SPY"]["manifest_run_id"] != "run-2"
    assert not report["passed"]


# ---------------------------------------------------------------------------
# Fix round 2 / F3: frozen values must be checked, not merely displayed
# ---------------------------------------------------------------------------

def test_a_frozen_week_set_that_the_calendar_cannot_produce_is_rejected(
        tmp_path, config_dir):
    """A week nobody traded cannot be an expected week.

    Freezing the denominator moved the attack from the tables to the manifest:
    inject a non-trading day into the frozen set and the calendar hash still
    matches, because the calendar was never touched. Once the hash proves the
    calendar is the run's own, the frozen set has to be reproducible from it.
    """
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rowid, payload_json FROM basket_pe_backfill_runs "
        "WHERE event_kind = 'run_started'").fetchone()
    payload = json.loads(row[1])
    payload["expected_weeks"] = payload["expected_weeks"] + ["2026-01-04"]
    with conn:
        conn.execute("UPDATE basket_pe_backfill_runs SET payload_json = ? "
                     "WHERE rowid = ?", [json.dumps(payload), row[0]])
    conn.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("frozen_weeks_not_reproducible" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_composition_floor_later_than_the_disclosures_is_rejected(
        tmp_path, config_dir):
    """Moving the floor forward shrinks what the run is accountable for."""
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT rowid, payload_json FROM basket_pe_backfill_runs "
        "WHERE event_kind = 'run_started'").fetchone()
    payload = json.loads(row[1])
    payload["composition_floor"] = "2026-01-12"
    payload["expected_weeks"] = ["2026-01-16"]
    with conn:
        conn.execute("UPDATE basket_pe_backfill_runs SET payload_json = ? "
                     "WHERE rowid = ?", [json.dumps(payload), row[0]])
    conn.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("composition_floor_later_than_disclosures" in str(item)
               for item in failures["manifest_denominator"]), failures


# ---------------------------------------------------------------------------
# Fix round 2 / F4: gap semantics, and the composition actually chosen
# ---------------------------------------------------------------------------

def test_a_frozen_week_before_the_documented_history_bound_is_rejected(
        tmp_path, config_dir):
    """The config bound and the availability floor are different statements.

    `history_available_from` is the lower bound of *disclosable* history; the
    frozen floor is where availability actually starts. The frozen set may not
    reach below either, and once a week is in the frozen set it is owed --
    the config bound never excuses a missing row.
    """
    db_path = _build(tmp_path, basket="SOXX",
                     calendar=("2021-08-02", "2021-10-15"),
                     row_dates=["2021-10-08", "2021-10-15"],
                     composition_floor="2021-08-02")
    failures = _failed(_verify(db_path, config_dir, baskets=("SOXX",)))
    assert any("frozen_week_before_history_bound" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_row_using_an_older_composition_than_available_is_caught(
        tmp_path, config_dir):
    """Not the newest eligible composition means a stale membership."""
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    with conn:
        # A newer composition, in force and public before both valuation dates.
        conn.execute(
            "INSERT OR REPLACE INTO fmp_fund_disclosure_holdings "
            "(basket_symbol, holding_date, source_kind, raw_row_index, "
            "rebalance_close_date, composition_effective_date, "
            "composition_available_date, raw_symbol, symbol, cik, weight_pct, "
            "included, snapshot_warnings_json, fetched_at, created_at) "
            "VALUES ('SPY','2025-12-30','disclosure',0,'2025-12-19',"
            "'2025-12-22','2025-12-30','AAA','AAA','0000000001',60.0,1,'[]',"
            "'2025-12-30T00:00:00Z','2025-12-30T00:00:00Z')")
    conn.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("not_the_latest_eligible_composition" in str(item)
               for item in failures["materialised_evidence"]), failures


# ---------------------------------------------------------------------------
# Fix round 2 / F2: certification applies to the rows a run actually wrote
# ---------------------------------------------------------------------------

def test_a_forged_run_cannot_certify_rows_it_did_not_write(
        tmp_path, config_dir):
    """The manifest resisted rewriting but not appending.

    Tamper a row, then append a fresh run_started + run_completed whose
    result_hash is recomputed over the tampered data: every hash matched and
    the forged run became the certifying one. Rows now name the run that wrote
    them, so a run can only certify its own.
    """
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET ttm_pe_gaap = 9.99 "
                     "WHERE valuation_date = '2026-01-16'")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history ORDER BY valuation_date")]
    conn.close()
    store = MarketStore(db_path)
    try:
        store.append_basket_pe_run_events(_manifest_events(
            "SPY", run_id="run-forged",
            trading=_weekdays("2026-01-05", "2026-01-16"), rows=rows))
    finally:
        store.close()
    failures = _failed(_verify(db_path, config_dir))
    assert failures, "the forged run re-certified tampered rows"
    assert any("not_owned_by_the_certifying_run" in str(item)
               for item in failures.get("manifest_result_integrity", [])), \
        failures


def test_rows_naming_a_run_that_never_completed_are_rejected(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET run_id = 'run-ghost'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("rows_claim_an_uncertified_run" in str(item)
               for item in failures["manifest_result_integrity"]), failures


def test_a_run_hash_covers_exactly_the_rows_it_owns(tmp_path, config_dir):
    """An older run's rows are hashed against that run's own declaration."""
    db_path = _build(tmp_path)
    _orphan_row(db_path, "SPY", "2019-06-28")
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET run_id = 'run-old' "
                     "WHERE valuation_date = '2019-06-28'")
    store = MarketStore(db_path)
    try:
        store.append_basket_pe_run_events(_manifest_events(
            "SPY", run_id="run-old", expected_from="2014-06-28",
            expected_to="2019-06-28", trading=["2019-06-28"], rows=[]))
    finally:
        store.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("run_result_hash_mismatch" in str(item)
               for item in failures["manifest_result_integrity"]), failures


# ---------------------------------------------------------------------------
# Fix round 3 / D1-D3: a run gets exactly one terminal event, and it is last
# ---------------------------------------------------------------------------

def _all_rows(db_path, basket="SPY"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history WHERE basket = ? "
        "ORDER BY valuation_date", [basket])]
    conn.close()
    return rows


def _append_events(db_path, events):
    store = MarketStore(db_path)
    try:
        store.append_basket_pe_run_events(events)
    finally:
        store.close()


def _run_event(run_id, event_seq, event_kind, payload, expected_from=None,
               expected_to=None):
    return {
        "run_id": run_id, "basket": "SPY", "event_seq": event_seq,
        "event_kind": event_kind, "frequency": "weekly",
        "expected_from_date": expected_from or EXPECTED_FROM,
        "expected_to_date": expected_to or AS_OF,
        "methodology_version": WEEKLY_METHODOLOGY_VERSION,
        "target_count": 2, "target_universe_json": ["AAA", "BBB"],
        "payload_json": payload,
    }


def test_a_second_run_completed_cannot_recertify_tampered_rows(
        tmp_path, config_dir):
    """D1: re-declaring a run's own completion, with the hash recomputed.

    No rewrite, no new run, no contradiction between failure and success --
    just a second `run_completed` appended to the legitimate run. The
    declaration map took the last one, so the tampered rows were blessed by
    their own owner.
    """
    db_path = _build(tmp_path)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET ttm_pe_gaap = 9.99 "
                     "WHERE valuation_date = '2026-01-16'")
    assert _failed(_verify(db_path, config_dir)), "tamper should already fail"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history ORDER BY valuation_date")]
    conn.close()
    _append_events(db_path, [_run_event(
        "run-1", 2, "run_completed",
        {"weekly_rows": len(rows), "result_hash": weekly_result_hash(rows)})])
    failures = _failed(_verify(db_path, config_dir))
    assert failures, "a second run_completed re-certified tampered rows"
    assert any("multiple_terminal_events" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_second_run_completed_cannot_widen_a_window_to_adopt_orphans(
        tmp_path, config_dir):
    """D2: the same append, used to widen a run's window over a stray row."""
    db_path = _build(tmp_path)
    _orphan_row(db_path, "SPY", "2019-06-28", run_id="run-1")
    assert _failed(_verify(db_path, config_dir))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history ORDER BY valuation_date")]
    conn.close()
    _append_events(db_path, [_run_event(
        "run-1", 2, "run_completed",
        {"weekly_rows": len(rows), "result_hash": weekly_result_hash(rows)},
        expected_from="2014-06-28")])
    failures = _failed(_verify(db_path, config_dir))
    assert failures, "a widened window adopted an orphan row"
    assert any("multiple_terminal_events" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_second_run_started_is_rejected(tmp_path, config_dir):
    """D3: a run announces itself once."""
    db_path = _build(tmp_path)
    _append_events(db_path, [_run_event(
        "run-1", 2, "run_started",
        {"expected_weeks": ["2026-01-16"], "calendar_hash": "x" * 64,
         "composition_floor": "2021-01-25"})])
    failures = _failed(_verify(db_path, config_dir))
    assert any("started_more_than_once" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_events_after_a_terminal_event_are_rejected(tmp_path, config_dir):
    """A run that kept talking after it finished is not a finished run."""
    db_path = _build(tmp_path)
    _append_events(db_path, [_run_event(
        "run-1", 2, "forced_refresh",
        {"symbol": "AAA", "from_date": "2026-01-05", "to_date": "2026-01-09",
         "trigger_dates": ["2026-01-07"], "pre_row_hash": "a" * 64,
         "post_row_hash": "b" * 64, "skipped": False})])
    failures = _failed(_verify(db_path, config_dir))
    assert any("terminal_is_not_the_last_event" in str(item)
               for item in failures["manifest_denominator"]), failures


# ---------------------------------------------------------------------------
# Fix round 3 / C1: the full-rewrite contract, pinned loudly
# ---------------------------------------------------------------------------

def test_incremental_tail_refresh_fails_loudly_full_rewrite_contract_pending_task_5(
        tmp_path, config_dir):
    """A partial-ownership run is REJECTED -- full-rewrite contract, pending
    Task 5 ruling.

    Row-level ownership (F2) means the certifying run must own every row in
    its window, which amounts to requiring a full-window rewrite. Today's
    producer does exactly that, so nothing is broken -- but plan §R5 describes
    a weekly tail refresh that recomputes only the last ~12 months, and such a
    run would take ownership of just those rows and fail this check for the
    whole basket.

    That is a real design decision (supersession chain vs full rewrite), not
    an oversight, and it belongs to Boss at Task 5. This test exists so the
    collision is loud the moment anyone implements an incremental refresh,
    instead of surfacing as an inexplicable basket-wide verification failure.
    """
    db_path = _build(tmp_path)
    # run-2 recomputes only the tail row, as an incremental refresh would.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tail = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'")]
    conn.close()
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET run_id = 'run-2' "
                     "WHERE valuation_date = '2026-01-16'")
    _append_events(db_path, _manifest_events(
        "SPY", run_id="run-2", trading=_weekdays("2026-01-05", "2026-01-16"),
        rows=tail))
    failures = _failed(_verify(db_path, config_dir))
    assert any("not_owned_by_the_certifying_run" in str(item)
               for item in failures["manifest_result_integrity"]), (
        "an incremental tail refresh must fail loudly, not quietly diverge")


# ---------------------------------------------------------------------------
# Fix round 4 / E1: a crashed run cannot be finished after the fact
# ---------------------------------------------------------------------------

def test_a_terminal_event_appended_after_a_later_runs_terminal_is_rejected(
        tmp_path, config_dir):
    """E1: the operationally likely one.

    A cloud run killed between committing its batch and appending its outcome
    is a normal event, and "record that it finished" is cleanup an operator
    would reach for through the sanctioned API. The resulting event set is
    perfectly well formed -- one start, one terminal, terminal last -- so
    cardinality cannot see it. Only the order gives it away: a sequential
    producer cannot close run-0 after run-1 has already closed.
    """
    db_path = _build(tmp_path)
    trading = _weekdays("2026-01-05", "2026-01-16")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    good = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history ORDER BY valuation_date")]
    conn.close()
    # The real chronology: run-0 starts, writes, and is killed before it can
    # append an outcome; run-1 then runs and completes normally.
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    _append_events(db_path, [_run_event(
        "run-0", 0, "run_started", {}, expected_from="2014-06-28",
        expected_to="2019-06-28")])
    _append_events(db_path, _manifest_events(
        "SPY", run_id="run-1", trading=trading, rows=good))
    _orphan_row(db_path, "SPY", "2019-06-28", run_id="run-0")
    before = _failed(_verify(db_path, config_dir))
    assert any("rows_claim_an_uncertified_run" in str(item)
               for item in before["manifest_result_integrity"]), before

    # the cleanup: run-0 is "recorded as finished" after run-1 already closed
    _append_events(db_path, [_run_event(
        "run-0", 1, "run_completed",
        {"weekly_rows": 1, "result_hash": weekly_result_hash(
            [row for row in _all_rows(db_path)
             if row["run_id"] == "run-0"])},
        expected_from="2014-06-28", expected_to="2019-06-28")])
    failures = _failed(_verify(db_path, config_dir))
    assert failures, "a late completion adopted the orphan"
    assert any("terminal_recorded_after_a_later_run" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_failed_run_before_a_good_run_is_not_flagged_by_the_order_rule(
        tmp_path, config_dir):
    """E2: the ordinary sequence must stay clean.

    run-0 fails and says so, then run-1 runs and completes. Terminal events in
    run order, nothing anomalous -- the order rule must not read a normal
    failure-then-retry as tampering.
    """
    db_path = _build(tmp_path)
    _append_events(db_path, [
        _run_event("run-0", 0, "run_started", {}),
        _run_event("run-0", 1, "run_failed",
                   {"error": "ValueError: boom", "rows_written": False}),
    ])
    # run-0's events land after run-1's in this fixture, so re-assert the rule
    # only fires on the *relative* order of terminals between runs.
    report = _verify(db_path, config_dir)
    assert not any("terminal_recorded_after_a_later_run" in str(item)
                   for item in _failed(report).get("manifest_denominator", []))


def test_gaps_in_event_seq_do_not_matter(tmp_path, config_dir):
    """E5: sequence numbers are labels, not evidence."""
    db_path = _build(tmp_path)
    _append_events(db_path, [
        _run_event("run-2", 7, "run_started", {}),
        _run_event("run-2", 99, "run_failed", {"rows_written": False}),
    ])
    report = _verify(db_path, config_dir)
    assert not any("event_seq" in str(item)
                   for item in _failed(report).get("manifest_denominator", []))


def test_no_certifying_run_reports_the_cause_not_every_row(
        tmp_path, config_dir):
    """Operability: one root cause beats thirty derived lines.

    With no certifying run every row is trivially uncertified, and listing
    them buries the reason underneath its own consequences.
    """
    db_path = _build(tmp_path, calendar=("2025-06-02", "2026-01-16"),
                     row_dates=None)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs "
                     "WHERE event_kind = 'run_completed'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("no_completed_run_manifest" in str(item)
               for item in failures["manifest_denominator"])
    derived = failures.get("expected_weekly_denominator", [])
    assert len(derived) <= 1, derived
    assert any("suppressed" in str(item) for item in derived), derived


def test_the_ordinary_retry_shape_verifies_clean(tmp_path, config_dir):
    """E3, done properly: a failed attempt, then a retry that rewrites and owns.

    The attack script's E3 leaves the rows owned by the original run while
    re-appending the manifest under new ids, so its red is the ownership rule
    working on an inconsistent fixture -- not a false positive. This is the
    same sequence with the rows where a real retry would put them, and it must
    verify clean, or the ownership model would be unusable in operation.
    """
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET run_id = 'retry-b'")
    _append_events(db_path, [
        _run_event("retry-a", 0, "run_started", {}),
        _run_event("retry-a", 1, "run_failed",
                   {"error": "RuntimeError: boom", "rows_written": False}),
    ])
    _append_events(db_path, _manifest_events(
        "SPY", run_id="retry-b", trading=_weekdays("2026-01-05", "2026-01-16"),
        rows=_all_rows(db_path)))
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)
    assert report["baskets"]["SPY"]["manifest_run_id"] == "retry-b"


# ---------------------------------------------------------------------------
# Boss review 2 / P1-C: manifest shape, and rowid as the only ordering
# ---------------------------------------------------------------------------

def test_a_run_with_no_run_started_is_rejected(tmp_path, config_dir):
    """A completion nobody started is not a run."""
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs "
                     "WHERE event_kind = 'run_started'")
    failures = _failed(_verify(db_path, config_dir))
    assert any("run_never_started" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_event_seq_cannot_launder_the_physical_order(tmp_path, config_dir):
    """event_seq is a label the writer chooses; rowid is what happened.

    Appending the terminal first and the start afterwards, with event_seq
    numbers that read correctly, produced a well-ordered run under an
    event_seq sort. Only the manifest's own row order shows the truth.
    """
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history ORDER BY valuation_date")]
    conn.close()
    # physically: terminal first, start second -- event_seq says otherwise
    _append_events(db_path, [_run_event(
        "run-1", 1, "run_completed",
        {"weekly_rows": len(rows), "result_hash": weekly_result_hash(rows)})])
    _append_events(db_path, [_run_event(
        "run-1", 0, "run_started",
        {"expected_weeks": ["2026-01-09", "2026-01-16"],
         "calendar_hash": weekly_calendar_hash(
             _weekdays("2026-01-05", "2026-01-16")),
         "composition_floor": "2021-01-25"})])
    failures = _failed(_verify(db_path, config_dir))
    assert any("run_started_is_not_the_first_event" in str(item)
               or "terminal_is_not_the_last_event" in str(item)
               for item in failures["manifest_denominator"]), failures


def test_a_table_without_run_id_fails_verification(tmp_path, config_dir):
    """Defence in depth for the migration: no ownership column, no trust."""
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("ALTER TABLE basket_weekly_pe_history "
                     "RENAME COLUMN run_id TO run_id_removed")
    conn.close()
    conn = connect_readonly(db_path)
    try:
        with pytest.raises(ValueError, match="run_id"):
            verify_database(conn, ["SPY"], AS_OF, YEARS, 8, config_dir)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Boss review 2 / P1-B: a reconciliation that reconciles nothing is not one
# ---------------------------------------------------------------------------

def _strip_hindsight_evidence(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT valuation_date, members_json FROM basket_weekly_pe_history")]
    with conn:
        for row in rows:
            payload = json.loads(row["members_json"])
            for member in payload["members"]:
                member.pop("hindsight_window", None)
                member.pop("hindsight_snapshot_date", None)
            conn.execute(
                "UPDATE basket_weekly_pe_history SET members_json = ? "
                "WHERE valuation_date = ?",
                [json.dumps(payload), row["valuation_date"]])
    conn.close()


def test_deleting_the_hindsight_evidence_does_not_buy_a_pass(
        tmp_path, config_dir):
    """Boss: strip every member's hindsight window and the check goes quiet.

    Declining on ambiguity is right; declining on *everything* and still
    passing means the check can be switched off by removing what it reads.
    A published hindsight P/E has to be accompanied by the evidence that lets
    it be rebuilt.
    """
    db_path = _build(tmp_path)
    _with_hindsight_sources(db_path)
    for valuation_date in ("2026-01-09", "2026-01-16"):
        _set_member_window(db_path, valuation_date)
    assert _verify(db_path, config_dir)["passed"], "baseline must be clean"

    _strip_hindsight_evidence(db_path)
    report = _verify(db_path, config_dir)
    failures = _failed(report)
    assert failures, "stripping the evidence bought a pass"
    detail = failures["raw_source_spot_check"]
    assert any("hindsight_evidence_missing" in str(item)
               for item in detail["errors"]), detail


def test_a_sample_that_reconciles_no_hindsight_row_fails(tmp_path, config_dir):
    """Zero reconciled hindsight incomes across a sample containing them."""
    db_path = _build(tmp_path)
    _with_hindsight_sources(db_path)
    for valuation_date in ("2026-01-09", "2026-01-16"):
        _set_member_window(db_path, valuation_date)
    # A window nothing can be rebuilt from: every member declines.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT valuation_date, members_json FROM basket_weekly_pe_history")]
    with conn:
        for row in rows:
            payload = json.loads(row["members_json"])
            for member in payload["members"]:
                member["hindsight_window"] = ["2099-03-31", "2099-12-31"]
            conn.execute(
                "UPDATE basket_weekly_pe_history SET members_json = ? "
                "WHERE valuation_date = ?",
                [json.dumps(payload), row["valuation_date"]])
    conn.close()
    detail = _failed(_verify(db_path, config_dir))["raw_source_spot_check"]
    assert detail["reconciled_hindsight_incomes"] == 0
    assert any("no_hindsight_income_reconciled" in str(item)
               for item in detail["errors"]), detail


# ---------------------------------------------------------------------------
# Boss review 2 / P1-D: the member set must come from the source, not the row
# ---------------------------------------------------------------------------

def test_a_deleted_member_cannot_hide_by_leaving_both_sides(
        tmp_path, config_dir):
    """Boss: delete a 40%-weight member and recompute everything around it.

    Coverage was computed from the members the row happened to contain, so a
    member removed from members_json vanished from numerator and denominator
    alike and the row stayed self-consistent. The expected membership has to
    come from the disclosure snapshot, exactly as the expected week set comes
    from the manifest -- a denominator built out of the data it is judging is
    not a denominator.
    """
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM basket_weekly_pe_history ORDER BY valuation_date")]
    with conn:
        for row in rows:
            payload = json.loads(row["members_json"])
            kept = [m for m in payload["members"] if m["symbol"] != "BBB"]
            payload["members"] = kept
            payload["weight_coverage_ttm"] = 1.0
            payload["weight_coverage_hindsight"] = 1.0
            mcap = sum(m["market_cap"] for m in kept)
            ttm = sum(m["ttm_net_income_usd"] for m in kept)
            hs = sum(m["hindsight_ntm_net_income_usd"] for m in kept)
            conn.execute(
                "UPDATE basket_weekly_pe_history SET members_json = ?, "
                "n_members = ?, n_covered_ttm = ?, n_covered_hindsight = ?, "
                "ttm_total_mcap = ?, ttm_net_income = ?, ttm_pe_gaap = ?, "
                "hindsight_total_mcap = ?, hindsight_ntm_net_income = ?, "
                "hindsight_ntm_pe_gaap = ?, mcap_coverage_ttm = 1.0, "
                "mcap_coverage_hindsight = 1.0 WHERE valuation_date = ?",
                [json.dumps(payload), len(kept), len(kept), len(kept),
                 mcap, ttm, mcap / ttm, mcap, hs, mcap / hs,
                 row["valuation_date"]])
    conn.close()
    # re-declare the manifest so the hash cannot be what catches it
    rows = _all_rows(db_path)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    _append_events(db_path, _manifest_events(
        "SPY", run_id="run-1", trading=_weekdays("2026-01-05", "2026-01-16"),
        rows=rows))
    failures = _failed(_verify(db_path, config_dir))
    assert failures, "a deleted member vanished from both sides unnoticed"
    assert any("member_missing_from_members_json" in str(item)
               and "BBB" in str(item)
               for item in failures["materialised_evidence"]), failures


def test_weight_coverage_is_measured_against_the_disclosed_weights(
        tmp_path, config_dir):
    """The eligible-weight denominator comes from the snapshot, not the row."""
    db_path = _build(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute(
        "SELECT * FROM basket_weekly_pe_history "
        "WHERE valuation_date = '2026-01-16'").fetchone())
    payload = json.loads(row["members_json"])
    # BBB stays in the row but its disclosed 40% is understated as 4%,
    # flattering the coverage that gates publication.
    for member in payload["members"]:
        if member["symbol"] == "BBB":
            member["weight_pct"] = 4.0
    with conn:
        conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = '2026-01-16'",
                     [json.dumps(payload)])
    conn.close()
    failures = _failed(_verify(db_path, config_dir))
    assert any("member_weight_disagrees_with_the_disclosure" in str(item)
               for item in failures["materialised_evidence"]), failures


def _add_snapshot(db_path, *, holding_date, source_kind, available,
                  weights=(60.0, 40.0)):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO fmp_fund_disclosure_holdings "
            "(basket_symbol, holding_date, source_kind, raw_row_index, "
            "rebalance_close_date, composition_effective_date, "
            "composition_available_date, raw_symbol, symbol, cik, weight_pct, "
            "included, snapshot_warnings_json, fetched_at, created_at) "
            "SELECT basket_symbol, ?, ?, raw_row_index, rebalance_close_date, "
            "composition_effective_date, ?, raw_symbol, symbol, cik, "
            "CASE raw_row_index WHEN 0 THEN ? ELSE ? END, included, "
            "snapshot_warnings_json, fetched_at, created_at "
            "FROM fmp_fund_disclosure_holdings "
            "WHERE holding_date = '2021-01-15' AND source_kind = 'disclosure'",
            [holding_date, source_kind, available, *weights])


@pytest.mark.parametrize("source_kind", ["live", "disclosure"])
def test_future_snapshot_does_not_change_past_membership(
        tmp_path, config_dir, source_kind):
    db_path = _build(tmp_path)
    assert _verify(db_path, config_dir)["passed"]
    _add_snapshot(db_path, holding_date="2026-02-01",
                  source_kind=source_kind, available="2026-02-01",
                  weights=(20.0, 80.0))
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)


def test_disclosure_supersedes_live_at_the_same_effective_date(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _add_snapshot(db_path, holding_date="2026-01-01", source_kind="live",
                  available="2026-01-02", weights=(20.0, 80.0))
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)


def test_live_snapshot_is_used_until_disclosure_becomes_available(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _add_snapshot(db_path, holding_date="2021-01-15", source_kind="live",
                  available="2021-01-25")
    _mutate(db_path, "UPDATE fmp_fund_disclosure_holdings "
                     "SET composition_available_date = '2026-02-01', "
                     "weight_pct = weight_pct / 2 WHERE source_kind = 'disclosure'")
    for row in _all_rows(db_path):
        payload = json.loads(row["members_json"])
        payload["weight_basis"] = "live_snapshot_backcast_proxy"
        _mutate(db_path, "UPDATE basket_weekly_pe_history SET members_json = ? "
                         "WHERE valuation_date = ?",
                [json.dumps(payload), row["valuation_date"]])
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)


def test_same_effective_newer_disclosure_must_be_selected(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    _add_snapshot(db_path, holding_date="2025-12-31",
                  source_kind="disclosure", available="2026-01-02")
    failures = _failed(_verify(db_path, config_dir))
    assert any("snapshot_identity_mismatch" in item
               for item in failures["materialised_evidence"]), failures


def test_membership_cannot_skip_a_missing_source_snapshot(tmp_path, config_dir):
    db_path = _build(tmp_path)
    _mutate(db_path, "DELETE FROM fmp_fund_disclosure_holdings")
    failures = _failed(_verify(db_path, config_dir))
    assert any("no_eligible_membership_snapshot" in item
               for item in failures["materialised_evidence"]), failures


def test_covered_by_weights_are_folded_inside_one_snapshot(tmp_path, config_dir):
    db_path = _build(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO fmp_fund_disclosure_holdings "
            "SELECT basket_symbol, holding_date, source_kind, 2, "
            "rebalance_close_date, composition_effective_date, "
            "composition_available_date, 'AAA.B', NULL, alias_symbol, "
            "alias_mode, alias_reason, name, 20.0, market_value, cik, cusip, "
            "isin, 0, 'dual_class_secondary', 'AAA', row_accepted_at, "
            "snapshot_warnings_json, fetched_at, created_at "
            "FROM fmp_fund_disclosure_holdings WHERE symbol = 'AAA'")
        conn.execute("UPDATE fmp_fund_disclosure_holdings SET weight_pct = 40 "
                     "WHERE symbol = 'AAA'")
    report = _verify(db_path, config_dir)
    assert report["passed"], _failed(report)


def test_hindsight_evidence_is_required_on_unsampled_rows(tmp_path, config_dir):
    db_path = _build(tmp_path)
    row = _all_rows(db_path)[-1]
    payload = json.loads(row["members_json"])
    payload["members"][0].pop("hindsight_window")
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = ?",
            [json.dumps(payload), row["valuation_date"]])
    report = _verify(db_path, config_dir, sample=1)
    assert row["valuation_date"] not in report["baskets"]["SPY"]["sampled"]
    assert any("hindsight_evidence_missing" in item
               for item in _failed(report)["materialised_evidence"])


def test_estimated_member_requires_its_own_snapshot_evidence(
        tmp_path, config_dir):
    db_path = _build(tmp_path)
    row = _all_rows(db_path)[-1]
    payload = json.loads(row["members_json"])
    payload["members"][0].update(
        hindsight_actual_quarters=3, hindsight_estimate_quarters=1)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET members_json = ? "
                     "WHERE valuation_date = ?",
            [json.dumps(payload), row["valuation_date"]])
    failures = _failed(_verify(db_path, config_dir, sample=1))
    assert any("hindsight_snapshot_evidence_missing" in item
               for item in failures["materialised_evidence"])


def test_one_baskets_reconciliation_cannot_cover_another(tmp_path, config_dir):
    db_path = _build(tmp_path, basket="SPY")
    _build(tmp_path, basket="QQQ")
    with sqlite3.connect(db_path) as conn:
        records = list(conn.execute(
            "SELECT valuation_date, members_json FROM basket_weekly_pe_history "
            "WHERE basket = 'QQQ'"))
        for valuation_date, raw in records:
            payload = json.loads(raw)
            for member in payload["members"]:
                member["hindsight_window"] = ["2099-03-31", "2099-12-31"]
            conn.execute("UPDATE basket_weekly_pe_history SET members_json = ? "
                         "WHERE basket = 'QQQ' AND valuation_date = ?",
                         [json.dumps(payload), valuation_date])
    report = _verify(db_path, config_dir, baskets=("SPY", "QQQ"))
    assert report["baskets"]["SPY"]["raw_source_reconciliation"][
        "reconciled_hindsight_incomes"] == 4
    assert report["baskets"]["QQQ"]["raw_source_reconciliation"][
        "reconciled_hindsight_incomes"] == 0
    assert any("QQQ:no_hindsight_income_reconciled" in item
               for item in _failed(report)["raw_source_spot_check"]["errors"])


@pytest.mark.parametrize("broken_shape", ["no_start", "reversed"])
def test_invalid_old_manifest_is_not_laundered_by_a_new_valid_run(
        tmp_path, config_dir, broken_shape):
    db_path = _build(tmp_path)
    rows = _all_rows(db_path)
    events = _manifest_events(
        "SPY", trading=_weekdays("2026-01-05", "2026-01-16"), rows=rows)
    _mutate(db_path, "DELETE FROM basket_pe_backfill_runs")
    old_events = events[1:] if broken_shape == "no_start" else events[::-1]
    _append_events(db_path, old_events)
    _mutate(db_path, "UPDATE basket_weekly_pe_history SET run_id = 'run-2'")
    _append_events(db_path, _manifest_events(
        "SPY", run_id="run-2", rows=_all_rows(db_path),
        trading=_weekdays("2026-01-05", "2026-01-16")))
    report = _verify(db_path, config_dir)
    assert report["baskets"]["SPY"]["manifest_run_id"] == "run-2"
    expected = ("run_never_started" if broken_shape == "no_start"
                else "run_started_is_not_the_first_event")
    assert any(expected in item
               for item in _failed(report)["manifest_denominator"])
