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
    }


def _weekly_row(basket, valuation_date, members, quality_tier="actual_only"):
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
        "composition_effective_date": "2021-01-18",
        "composition_available_date": "2021-01-25",
        "quality_tier": quality_tier,
        "members_json": {
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
           market_cap_dates=None, composition_floor="2021-01-25"):
    """A consistent database: calendar, raw sources, weekly rows, manifest."""
    store = MarketStore(tmp_path / "market.db")
    conn = store._get_conn()
    trading = _weekdays(*calendar)
    fiscal = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30"]
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
                 for day in fiscal])
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
              "2021-01-18", "2021-01-25", symbol, symbol,
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
        rows.append(_weekly_row(basket, valuation_date, row_members))
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


def test_soxx_leading_gap_before_the_configured_boundary_is_expected(
        tmp_path, config_dir):
    """SOXX has no verifiable disclosure before 2021-09-20 (basket config)."""
    db_path = _build(
        tmp_path, basket="SOXX", calendar=("2021-08-02", "2021-10-15"),
        row_dates=["2021-10-08", "2021-10-15"])
    report = _verify(db_path, config_dir, baskets=("SOXX",))
    denominator = {check["name"]: check["detail"]
                   for check in report["checks"]}["expected_weekly_denominator"]
    assert not [item for item in denominator
                if "expected_week_has_no_row" in str(item)
                and str(item).split(":")[1] < "2021-09-20"]
    missing_after = [item for item in denominator
                     if "expected_week_has_no_row" in str(item)]
    # Weeks after the boundary are still owed, so the gap allowance is narrow.
    assert missing_after
    assert all(str(item).split(":")[1] >= "2021-09-20"
               for item in missing_after)


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


HINDSIGHT_FISCAL = ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]


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

def _orphan_row(db_path, basket, valuation_date):
    """A row of the shape a failed run leaves behind, written directly."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO basket_weekly_pe_history "
            "(basket, valuation_date, ttm_pe_gaap, hindsight_ntm_pe_gaap, "
            "ttm_total_mcap, ttm_net_income, hindsight_total_mcap, "
            "hindsight_ntm_net_income, n_members, n_covered_ttm, "
            "n_covered_hindsight, mcap_coverage_ttm, mcap_coverage_hindsight, "
            "hindsight_actual_quarters, hindsight_estimate_quarters, "
            "composition_effective_date, composition_available_date, "
            "quality_tier, members_json, warnings_json, methodology_version, "
            "created_at, last_updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [basket, valuation_date, 20.0, 18.0, 1000.0, 50.0, 1000.0, 55.0,
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
