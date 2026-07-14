"""Independent read-only verification contracts."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

import scripts.verify_basket_ttm_pe as verifier
from src.data.market_store import MarketStore
from terminal.historical_basket_valuation import compute_daily_basket_valuation
from terminal.historical_market_cap_sanity import scan_market_cap_candidates


def _quarter_rows(symbol="TEST"):
    return [
        {"symbol": symbol, "date": "2025-03-31", "period": "Q1",
         "accepted_date": "2025-04-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": symbol, "date": "2025-06-30", "period": "Q2",
         "accepted_date": "2025-07-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": symbol, "date": "2025-09-30", "period": "Q3",
         "accepted_date": "2025-10-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": symbol, "date": "2025-12-31", "period": "Q4",
         "accepted_date": "2026-01-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
    ]


def _make_green_db(tmp_path):
    path = tmp_path / "market.db"
    store = MarketStore(path)
    conn = store._get_conn()
    trading_dates = ["2025-12-22", "2026-01-30", "2026-02-02"]
    conn.executemany(
        "INSERT INTO daily_price (symbol,date,close) VALUES (?,?,?)",
        [("SOXX", day, 500.0) for day in trading_dates]
        + [("TEST", "2026-01-30", 10.0), ("TEST", "2026-02-02", 15.0)])
    conn.executemany(
        "INSERT INTO historical_market_cap VALUES (?,?,?)",
        [("TEST", "2026-01-30", 1000.0), ("TEST", "2026-02-02", 1500.0)])
    conn.executemany(
        "INSERT INTO income_quarterly "
        "(symbol,date,period,accepted_date,reported_currency,net_income) "
        "VALUES (?,?,?,?,?,?)",
        [(row["symbol"], row["date"], row["period"], row["accepted_date"],
          row["reported_currency"], row["net_income"])
         for row in _quarter_rows()])
    start = date(2021, 3, 31)
    holding_dates = []
    for index in range(19):
        holding = (start + timedelta(days=91 * index)).isoformat()
        holding_dates.append(holding)
        store.replace_fund_disclosure_snapshot(
            "SOXX", holding, "disclosure", [{
                "raw_row_index": member, "raw_symbol": "TEST", "symbol": "TEST",
                "name": "TEST", "weight_pct": 4.0, "market_value": 40.0,
                "included": 1, "filter_reason": None, "covered_by": None,
                "row_accepted_at": "2025-11-20 16:00:00",
            } for member in range(25)],
            rebalance_close_date=holding,
            composition_effective_date=holding,
            composition_available_date=holding,
            fetched_at="2026-07-14T00:00:00Z")
    live_rows = [{
        "raw_row_index": member, "raw_symbol": "TEST", "symbol": "TEST",
        "name": "TEST", "weight_pct": 4.0, "market_value": 40.0,
        "included": 1, "filter_reason": None, "covered_by": None,
        "row_accepted_at": None,
    } for member in range(25)]
    store.replace_fund_disclosure_snapshot(
        "SOXX", "2026-01-30", "live", live_rows,
        rebalance_close_date="2025-12-19",
        composition_effective_date="2025-12-22",
        composition_available_date="2026-01-30",
        fetched_at="2026-01-30T22:00:00Z")
    sanity = scan_market_cap_candidates(
        [{"symbol": "TEST", "date": "2026-01-30", "market_cap": 1000.0},
         {"symbol": "TEST", "date": "2026-02-02", "market_cap": 1500.0}],
        [{"symbol": "TEST", "date": "2026-01-30", "close": 10.0},
         {"symbol": "TEST", "date": "2026-02-02", "close": 15.0}], [])
    output = compute_daily_basket_valuation(
        valuation_date="2026-02-02",
        holding_rows=live_rows,
        income_by_symbol={"TEST": _quarter_rows()},
        market_cap_by_symbol={"TEST": [
            {"date": "2026-01-30", "market_cap": 1000.0},
            {"date": "2026-02-02", "market_cap": 1500.0}]},
        fx_by_currency={}, sanity_by_symbol={"TEST": sanity},
        trading_dates=trading_dates,
        composition={
            "holding_date": "2026-01-30",
            "anchor_trading_date": "2026-01-30",
            "composition_effective_date": "2025-12-22",
            "composition_available_date": "2026-01-30",
            "weight_basis": "live_snapshot_backcast_proxy",
            "data_quality_tier": "live_tail_weaker",
        })
    store.replace_basket_ttm_valuation_range(
        "SOXX", "2026-02-02", "2026-02-02", [output])
    store.close()
    return path


def _report(path):
    conn = verifier.connect_readonly(path)
    result = verifier.verify_database(conn, min_date="2026-02-02")
    conn.close()
    return result


def _check(result, name):
    return next(row for row in result["checks"] if row["name"] == name)


def test_all_green_fixture_recomputes_every_output_from_sources(tmp_path):
    result = _report(_make_green_db(tmp_path))
    assert result["passed"] is True
    assert _check(result, "source_recompute_all_rows")["detail"]["rows_recomputed"] == 1
    assert _check(result, "source_snapshots")["detail"]["disclosure_count"] == 19
    assert _check(result, "source_snapshots")["detail"]["current_tail_valid"] is True
    anchors = _check(result, "market_cap_jump_split_sanity")["detail"]["known_anchors"]
    assert set(anchors) == {"KLAC", "MCHP"}


def test_connection_is_mode_ro_not_immutable(monkeypatch, tmp_path):
    path = _make_green_db(tmp_path)
    seen = {}
    real_connect = sqlite3.connect

    def capture(database, *args, **kwargs):
        seen["database"] = database
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(verifier.sqlite3, "connect", capture)
    conn = verifier.connect_readonly(path)
    conn.close()
    assert seen["database"].endswith("?mode=ro")
    assert "immutable" not in seen["database"]


def test_min_date_is_required_to_make_leading_boundary_explicit():
    with pytest.raises(SystemExit):
        verifier.parse_args([])


@pytest.mark.parametrize(
    "mutation,failed_check",
    [
        ("DELETE FROM fmp_fund_disclosure_holdings WHERE holding_date = "
         "(SELECT MIN(holding_date) FROM fmp_fund_disclosure_holdings)",
         "source_snapshots"),
        ("UPDATE basket_ttm_valuation SET weight_coverage = 0.89",
         "coverage_gate_consistency"),
        ("UPDATE basket_ttm_valuation SET rebalance_weighted_ttm_pe_gaap_proxy = 99",
         "source_recompute_all_rows"),
        ("UPDATE basket_ttm_valuation SET members_json = 'bad'",
         "duplicates_and_json"),
        ("UPDATE basket_ttm_valuation SET warnings_json = '[\"fake\"]'",
         "source_recompute_all_rows"),
        ("UPDATE basket_ttm_valuation SET data_quality_tier = 'fake'",
         "source_recompute_all_rows"),
    ],
)
def test_failure_classifications_are_distinct(tmp_path, mutation, failed_check):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(mutation)
    conn.commit()
    conn.close()
    result = _report(path)
    assert _check(result, failed_check)["passed"] is False
    assert result["passed"] is False


def test_missing_output_trading_date_fails_denominator_and_coverage(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO daily_price (symbol,date,close) VALUES (?,?,?)", [
        ("SOXX", "2026-02-03", 500), ("SOXX", "2026-02-04", 500),
        ("TEST", "2026-02-04", 10),
    ])
    columns = [row[1] for row in conn.execute(
        "PRAGMA table_info(basket_ttm_valuation)")]
    original = dict(zip(columns, conn.execute(
        "SELECT * FROM basket_ttm_valuation").fetchone()))
    original["valuation_date"] = "2026-02-04"
    conn.execute(
        f"INSERT INTO basket_ttm_valuation ({','.join(columns)}) "
        f"VALUES ({','.join(['?'] * len(columns))})",
        [original[column] for column in columns])
    conn.commit()
    conn.close()
    result = _report(path)
    assert _check(result, "trading_calendar_denominator")["passed"] is False


def test_verifier_detects_leading_and_trailing_output_gaps(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO daily_price (symbol,date,close) "
                 "VALUES ('SOXX','2026-02-03',500)")
    conn.commit()
    conn.close()
    trailing = _report(path)
    assert _check(trailing, "trading_calendar_denominator")["passed"] is False

    conn = verifier.connect_readonly(path)
    leading = verifier.verify_database(conn, min_date="2026-01-30")
    conn.close()
    assert _check(leading, "trading_calendar_denominator")["passed"] is False


def test_truncated_snapshot_with_date_still_present_is_blocking(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    holding = conn.execute(
        "SELECT MIN(holding_date) FROM fmp_fund_disclosure_holdings").fetchone()[0]
    conn.execute(
        "DELETE FROM fmp_fund_disclosure_holdings "
        "WHERE holding_date = ? AND raw_row_index >= 5", [holding])
    conn.commit()
    conn.close()
    result = _report(path)
    assert _check(result, "source_snapshots")["passed"] is True
    assert _check(result, "snapshot_plausibility")["passed"] is False
    assert result["passed"] is False


def test_missing_live_tail_without_same_rebalance_disclosure_is_blocking(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM fmp_fund_disclosure_holdings WHERE source_kind = 'live'")
    conn.commit()
    conn.close()
    result = _report(path)
    assert _check(result, "source_snapshots")["passed"] is False
    assert _check(result, "source_snapshots")["detail"]["current_tail_valid"] is False


def test_same_rebalance_official_disclosure_can_replace_live_tail(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE fmp_fund_disclosure_holdings SET source_kind = 'disclosure' "
        "WHERE source_kind = 'live'")
    conn.execute(
        "UPDATE basket_ttm_valuation SET "
        "weight_basis = 'fixed_rebalance_weight_proxy', "
        "data_quality_tier = 'historical_disclosure_fixed_proxy'")
    conn.commit()
    conn.close()
    result = _report(path)
    assert _check(result, "source_snapshots")["detail"]["current_tail_valid"] is True
    assert result["passed"] is True


def test_tampered_member_and_sanity_evidence_are_blocking(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    member = json.loads(conn.execute(
        "SELECT members_json FROM basket_ttm_valuation").fetchone()[0])[0]
    member.update({
        "market_cap": 999999.0,
        "ttm_net_income_usd": -123.0,
        "fiscal_dates": ["1900-01-01"],
    })
    member["fx_evidence"][0]["usd_per_unit"] = 99.0
    conn.execute(
        "UPDATE basket_ttm_valuation SET members_json = ?, mcap_sanity_json = '[]'",
        [json.dumps([member])])
    conn.commit()
    conn.close()
    result = _report(path)
    check = _check(result, "persisted_member_and_sanity_evidence")
    assert check["passed"] is False
    assert any("members_json" in error for error in check["detail"])
    assert any("mcap_sanity_json" in error for error in check["detail"])
    assert result["passed"] is False


def test_published_invalid_market_cap_is_detected_from_raw_sources(tmp_path):
    path = _make_green_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE historical_market_cap SET market_cap = 100 "
                 "WHERE symbol = 'TEST' AND date = '2026-02-02'")
    conn.commit()
    conn.close()
    result = _report(path)
    assert _check(result, "source_recompute_all_rows")["passed"] is False
    assert _check(result, "market_cap_jump_split_sanity")["passed"] is False
    assert result["passed"] is False


def test_missing_required_table_is_operational_error(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    conn = verifier.connect_readonly(path)
    with pytest.raises(ValueError, match="required tables missing"):
        verifier.verify_database(conn, min_date="2021-09-20")
    conn.close()
