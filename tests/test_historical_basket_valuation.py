"""Pure as-of GAAP TTM basket valuation tests."""
from datetime import date, timedelta

import pytest

from terminal.historical_basket_valuation import (
    compute_daily_basket_valuation,
    compute_member_ttm_income_usd,
    compute_uncapped_mcap_basket_pe,
    compute_weighted_ttm_pe_proxy,
    merge_covered_weights,
    select_asof_fx,
    select_asof_market_cap,
    select_four_continuous_asof_quarters,
)


def _calendar(start="2024-01-01", end="2026-12-31"):
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    values = []
    while current <= final:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _quarters(symbol="TEST", currency="USD", income=25.0):
    return [
        {"symbol": symbol, "date": "2025-03-31", "period": "Q1",
         "accepted_date": "2025-04-20 16:00:00", "reported_currency": currency,
         "net_income": income},
        {"symbol": symbol, "date": "2025-06-30", "period": "Q2",
         "accepted_date": "2025-07-20 16:00:00", "reported_currency": currency,
         "net_income": income},
        {"symbol": symbol, "date": "2025-09-30", "period": "Q3",
         "accepted_date": "2025-10-20 16:00:00", "reported_currency": currency,
         "net_income": income},
        {"symbol": symbol, "date": "2025-12-31", "period": "Q4",
         "accepted_date": "2026-01-20 16:00:00", "reported_currency": currency,
         "net_income": income},
    ]


def test_quarter_accepted_on_valuation_date_is_visible_next_trading_day_only():
    rows = _quarters()
    rows[-1]["accepted_date"] = "2026-02-02 08:00:00"
    assert select_four_continuous_asof_quarters(
        rows, "2026-02-02", _calendar()) is None
    selected = select_four_continuous_asof_quarters(
        rows, "2026-02-03", _calendar())
    assert selected is not None
    assert selected[-1]["visibility_date"] == "2026-02-03"


def test_duplicate_fiscal_period_uses_latest_visible_acceptance_deterministically():
    rows = _quarters() + [{
        **_quarters()[-1], "accepted_date": "2026-01-25 12:00:00",
        "net_income": 30.0,
    }]
    selected = select_four_continuous_asof_quarters(
        rows, "2026-02-02", _calendar())
    assert selected is not None
    assert selected[-1]["net_income"] == 30.0


def test_missing_middle_quarter_and_malformed_acceptance_fail_closed():
    assert select_four_continuous_asof_quarters(
        _quarters()[:2] + _quarters()[3:], "2026-02-02", _calendar()) is None
    malformed = _quarters()
    malformed[-1]["accepted_date"] = None
    assert select_four_continuous_asof_quarters(
        malformed, "2026-02-02", _calendar()) is None


def test_ancient_malformed_quarter_does_not_poison_current_ttm():
    ancient = {
        "symbol": "TEST", "date": "2008-03-31", "period": "Q1",
        "accepted_date": None, "reported_currency": "USD", "net_income": None,
    }
    selected = select_four_continuous_asof_quarters(
        [ancient, *_quarters()], "2026-02-02", _calendar("2008-01-01"))
    assert selected is not None
    assert [row["date"] for row in selected] == [
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]


def test_recent_malformed_quarter_cannot_fallback_to_stale_ttm():
    older = {
        "symbol": "TEST", "date": "2024-12-31", "period": "Q4",
        "accepted_date": "2025-01-20 16:00:00", "reported_currency": "USD",
        "net_income": 25.0,
    }
    malformed = {
        "symbol": "TEST", "date": "2025-12-31", "period": "Q4",
        "accepted_date": None, "reported_currency": "USD", "net_income": None,
    }
    assert select_four_continuous_asof_quarters(
        [older, *_quarters()[:-1], malformed], "2026-02-02", _calendar()) is None


def test_non_quarter_period_is_not_shifted_into_ttm():
    rows = _quarters() + [{
        **_quarters()[-1], "date": "2026-01-01", "period": "FY",
        "accepted_date": "2026-01-10 12:00:00", "net_income": 999.0,
    }]
    selected = select_four_continuous_asof_quarters(
        rows, "2026-02-02", _calendar())
    assert selected is not None
    assert sum(row["net_income"] for row in selected) == 100.0


def test_market_cap_asof_returns_observation_and_blocks_quarantine_crossing():
    rows = [
        {"date": "2026-01-02", "market_cap": 100.0},
        {"date": "2026-01-05", "market_cap": 101.0},
    ]
    sanity = {"2026-01-02": "clean", "2026-01-05": "clean"}
    selected = select_asof_market_cap(rows, "2026-01-08", sanity)
    assert selected["date"] == "2026-01-05"
    assert selected["staleness_days"] == 3
    assert select_asof_market_cap(
        rows, "2026-01-08", sanity, {"2026-01-06"}) is None
    assert select_asof_market_cap(rows, "2026-01-20", sanity) is None


def test_invalid_latest_market_cap_does_not_fallback_to_previous_clean_row():
    rows = [
        {"date": "2026-01-02", "market_cap": 100.0},
        {"date": "2026-01-05", "market_cap": 10.0},
    ]
    sanity = {"2026-01-02": "clean", "2026-01-05": "invalid_mcap"}
    assert select_asof_market_cap(rows, "2026-01-05", sanity) is None


def test_fx_asof_and_mixed_currency_conversion_are_per_quarter():
    eur = [{"currency": "EUR", "date": "2026-01-30", "usd_per_unit": 1.2,
            "source_symbol": "EURUSD"}]
    twd = [{"currency": "TWD", "date": "2026-01-30", "usd_per_unit": 0.03,
            "source_symbol": "TWDUSD"}]
    assert select_asof_fx("USD", [], "2026-02-02")["usd_per_unit"] == 1.0
    assert select_asof_fx("EUR", eur, "2026-02-02")["date"] == "2026-01-30"
    quarters = _quarters()
    quarters[0]["reported_currency"] = "EUR"
    quarters[0]["net_income"] = 10.0
    quarters[1]["reported_currency"] = "TWD"
    quarters[1]["net_income"] = 100.0
    quarters[2]["net_income"] = 5.0
    quarters[3]["net_income"] = 5.0
    result = compute_member_ttm_income_usd(
        quarters, {"EUR": eur, "TWD": twd}, "2026-02-02")
    assert result["ttm_net_income_usd"] == pytest.approx(25.0)
    assert {item["currency"] for item in result["quarters"]} == {
        "EUR", "TWD", "USD"}


@pytest.mark.parametrize("rate,source_symbol", [
    (30.0, "TWDUSD"),
    (0.03, "USDTWD"),
])
def test_fx_asof_rejects_reverse_or_implausible_twd_quote(rate, source_symbol):
    rows = [{"currency": "TWD", "date": "2026-01-30",
             "usd_per_unit": rate, "source_symbol": source_symbol}]
    assert select_asof_fx("TWD", rows, "2026-02-02") is None


def test_negative_income_is_included_in_both_metrics():
    members = [
        {"symbol": "A", "weight_pct": 60.0, "market_cap": 600.0,
         "ttm_net_income_usd": 60.0},
        {"symbol": "B", "weight_pct": 40.0, "market_cap": 400.0,
         "ttm_net_income_usd": -20.0},
    ]
    primary = compute_weighted_ttm_pe_proxy(members, eligible_weight=100.0)
    secondary = compute_uncapped_mcap_basket_pe(primary["covered_members"])
    assert primary["weighted_earnings_yield"] == pytest.approx(0.04)
    assert primary["pe"] == pytest.approx(25.0)
    assert secondary["ttm_net_income_usd"] == 40.0
    assert secondary["pe"] == pytest.approx(25.0)


def test_weight_gate_normalizes_by_covered_weight_and_nulls_below_90_pct():
    members = [{"symbol": "A", "weight_pct": 89.0, "market_cap": 890.0,
                "ttm_net_income_usd": 89.0}]
    result = compute_weighted_ttm_pe_proxy(members, eligible_weight=100.0)
    assert result["weighted_earnings_yield"] == pytest.approx(0.1)
    assert result["weight_coverage"] == 0.89
    assert result["pe"] is None


def test_covered_by_weight_merges_and_orphan_stays_in_denominator():
    rows = [
        {"symbol": "GOOGL", "included": 1, "covered_by": None,
         "weight_pct": 5.0},
        {"symbol": None, "included": 0, "covered_by": "GOOGL",
         "weight_pct": 2.0},
        {"symbol": None, "included": 0, "covered_by": "MISSING",
         "weight_pct": 1.0},
        {"symbol": None, "included": 0, "covered_by": None,
         "filter_reason": "cash_or_fund", "weight_pct": 3.0},
    ]
    result = merge_covered_weights(rows)
    assert result["weights"] == {"GOOGL": 7.0}
    assert result["eligible_weight"] == 8.0
    assert result["orphan_covered_weight"] == 1.0
    assert result["warnings"] == ["covered_by_target_missing:MISSING"]


def test_daily_engine_evidence_coverage_weekend_anchor_and_expost_label():
    holdings = [
        {"symbol": "TEST", "included": 1, "covered_by": None,
         "weight_pct": 100.0, "raw_symbol": "TEST"},
    ]
    sanity = [{"date": "2026-01-30", "status": "clean"}]
    result = compute_daily_basket_valuation(
        valuation_date="2026-02-02",
        holding_rows=holdings,
        income_by_symbol={"TEST": _quarters()},
        market_cap_by_symbol={"TEST": [
            {"date": "2026-01-30", "market_cap": 1_000.0}]},
        fx_by_currency={},
        sanity_by_symbol={"TEST": sanity},
        trading_dates=_calendar(),
        composition={
            "holding_date": "2026-01-31",  # Saturday
            "anchor_trading_date": "2026-01-30",
            "composition_effective_date": "2025-12-22",
            "composition_available_date": "2026-02-10",
            "weight_basis": "fixed_rebalance_weight_proxy",
            "data_quality_tier": "historical_disclosure_fixed_proxy",
        },
    )
    assert result["holding_date"] == "2026-01-31"
    assert result["is_observed_weight_date"] == 0
    assert result["is_ex_post_composition"] == 1
    assert result["weight_coverage"] == 1.0
    assert result["rebalance_weighted_ttm_pe_gaap_proxy"] == pytest.approx(10.0)
    evidence = result["members_json"][0]
    assert evidence["market_cap_date"] == "2026-01-30"
    assert len(evidence["fiscal_dates"]) == 4
    assert len(evidence["accepted_dates"]) == 4


def test_forced_refresh_evidence_survives_when_post_refresh_row_is_clean():
    result = compute_daily_basket_valuation(
        valuation_date="2026-02-02",
        holding_rows=[{"symbol": "TEST", "included": 1,
                       "covered_by": None, "weight_pct": 100.0}],
        income_by_symbol={"TEST": _quarters()},
        market_cap_by_symbol={"TEST": [
            {"date": "2026-01-30", "market_cap": 1_000.0}]},
        fx_by_currency={},
        sanity_by_symbol={"TEST": [{
            "date": "2026-01-30", "status": "clean", "candidate": False,
            "pre_refresh_status": "invalid_mcap",
            "forced_refresh_planned": True,
            "forced_refresh_attempted": True,
            "refresh_succeeded": True,
            "refresh_windows": [{
                "from_date": "2026-01-23", "to_date": "2026-02-02"}],
            "quarantined": False,
        }]},
        trading_dates=_calendar(),
        composition={
            "holding_date": "2025-12-31",
            "anchor_trading_date": "2025-12-31",
            "composition_effective_date": "2025-12-22",
            "composition_available_date": "2026-02-10",
            "weight_basis": "fixed_rebalance_weight_proxy",
            "data_quality_tier": "historical_disclosure_fixed_proxy",
        },
    )
    assert result["mcap_sanity_json"] == [{
        "raw_symbol": "TEST", "symbol": "TEST",
        "date": "2026-01-30", "status": "clean", "candidate": False,
        "pre_refresh_status": "invalid_mcap",
        "forced_refresh_planned": True,
        "forced_refresh_attempted": True,
        "refresh_succeeded": True,
        "refresh_windows": [{
            "from_date": "2026-01-23", "to_date": "2026-02-02"}],
        "quarantined": False,
    }]


def test_raw_symbol_is_preferred_and_alias_is_only_complete_data_fallback():
    holdings = [{
        "symbol": "CREE", "raw_symbol": "CREE", "alias_symbol": "WOLF",
        "alias_reason": "rename", "included": 1, "covered_by": None,
        "weight_pct": 100.0,
    }]
    common = dict(
        valuation_date="2026-02-02", holding_rows=holdings,
        fx_by_currency={}, trading_dates=_calendar(),
        composition={
            "holding_date": "2025-12-31", "anchor_trading_date": "2025-12-31",
            "composition_effective_date": "2025-12-22",
            "composition_available_date": "2026-02-10",
            "weight_basis": "fixed_rebalance_weight_proxy",
            "data_quality_tier": "historical_disclosure_fixed_proxy",
        })
    full_income = {"CREE": _quarters("CREE"), "WOLF": _quarters("WOLF")}
    full_mcap = {"CREE": [{"date": "2026-01-30", "market_cap": 1000.0}],
                 "WOLF": [{"date": "2026-01-30", "market_cap": 2000.0}]}
    clean_sanity = {
        "CREE": [{"date": "2026-01-30", "status": "clean"}],
        "WOLF": [{"date": "2026-01-30", "status": "clean"}],
    }
    fallback_sanity = {
        "CREE": [{
            "date": "2026-01-30", "status": "invalid_mcap",
            "candidate": True, "quarantined": True,
        }],
        "WOLF": [{"date": "2026-01-30", "status": "clean"}],
    }
    direct = compute_daily_basket_valuation(
        income_by_symbol=full_income, market_cap_by_symbol=full_mcap,
        sanity_by_symbol=clean_sanity, **common)
    assert direct["members_json"][0]["resolved_symbol"] == "CREE"
    assert direct["members_json"][0]["alias_reason"] is None

    fallback = compute_daily_basket_valuation(
        income_by_symbol={"CREE": [], "WOLF": _quarters("WOLF")},
        market_cap_by_symbol=full_mcap, sanity_by_symbol=fallback_sanity, **common)
    assert fallback["members_json"][0]["raw_symbol"] == "CREE"
    assert fallback["members_json"][0]["resolved_symbol"] == "WOLF"
    assert fallback["members_json"][0]["alias_reason"] == "rename"
    assert fallback["mcap_sanity_json"] == [{
        "raw_symbol": "CREE", "symbol": "CREE",
        "date": "2026-01-30", "status": "invalid_mcap",
        "candidate": True, "quarantined": True,
    }]


def test_authoritative_alias_never_uses_complete_wrong_company_data():
    result = compute_daily_basket_valuation(
        valuation_date="2026-02-02",
        holding_rows=[{
            "symbol": "TERN", "raw_symbol": "TERN", "alias_symbol": "TER",
            "alias_mode": "authoritative", "alias_reason": "vendor symbol error",
            "included": 1, "covered_by": None, "weight_pct": 100.0,
        }],
        income_by_symbol={
            "TERN": _quarters("TERN", income=999.0),
            "TER": _quarters("TER", income=25.0),
        },
        market_cap_by_symbol={
            "TERN": [{"date": "2026-01-30", "market_cap": 999_000.0}],
            "TER": [{"date": "2026-01-30", "market_cap": 1_000.0}],
        },
        fx_by_currency={},
        sanity_by_symbol={symbol: [{"date": "2026-01-30", "status": "clean"}]
                          for symbol in ("TERN", "TER")},
        trading_dates=_calendar(),
        composition={
            "holding_date": "2025-12-31", "anchor_trading_date": "2025-12-31",
            "composition_effective_date": "2025-12-22",
            "composition_available_date": "2026-02-10",
            "weight_basis": "fixed_rebalance_weight_proxy",
            "data_quality_tier": "historical_disclosure_fixed_proxy",
        },
    )
    evidence = result["members_json"][0]
    assert evidence["raw_symbol"] == "TERN"
    assert evidence["resolved_symbol"] == "TER"
    assert evidence["alias_mode"] == "authoritative"
    assert result["rebalance_weighted_ttm_pe_gaap_proxy"] == pytest.approx(10.0)
