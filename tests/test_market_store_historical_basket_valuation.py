"""Storage contracts for historical SOXX basket valuation."""
import json
import sqlite3

import pytest

from src.data.market_store import MarketStore, _validate_table


EXPECTED_TABLES = {
    "fmp_fund_disclosure_holdings",
    "fx_daily",
    "fmp_stock_splits",
    "basket_ttm_valuation",
}


@pytest.fixture
def store(tmp_path):
    value = MarketStore(tmp_path / "market.db")
    yield value
    value.close()


def _holding(index=0, symbol="NVDA", weight=8.5):
    return {
        "raw_row_index": index,
        "raw_symbol": symbol,
        "symbol": symbol,
        "alias_symbol": "TER",
        "alias_mode": "authoritative",
        "alias_reason": "vendor symbol error",
        "name": symbol,
        "weight_pct": weight,
        "market_value": 100.0,
        "cik": "0000000001",
        "cusip": "123456789",
        "isin": "US1234567890",
        "included": 1,
        "filter_reason": None,
        "covered_by": None,
        "row_accepted_at": "2025-11-26 12:01:37",
        "snapshot_warnings_json": ["non_reconstitution_membership_delta"],
    }


def _valuation(valuation_date="2026-07-10", pe=24.5):
    return {
        "valuation_date": valuation_date,
        "holding_date": "2026-07-14",
        "composition_effective_date": "2026-06-22",
        "composition_available_date": "2026-07-14",
        "is_ex_post_composition": 1,
        "weight_basis": "live_snapshot_backcast_proxy",
        "data_quality_tier": "live_tail_weaker",
        "is_observed_weight_date": 0,
        "eligible_weight": 100.0,
        "covered_weight": 96.0,
        "rebalance_weighted_ttm_pe_gaap_proxy": pe,
        "weighted_earnings_yield": 1.0 / pe,
        "uncapped_mcap_basket_pe_gaap": 25.0,
        "covered_market_cap": 1_000.0,
        "ttm_net_income_usd": 40.0,
        "member_count": 30,
        "covered_count": 29,
        "weight_coverage": 0.96,
        "mcap_weight_coverage": 0.96,
        "income_weight_coverage": 0.98,
        "fx_weight_coverage": 1.0,
        "members_json": [{"symbol": "NVDA"}],
        "warnings_json": ["live-tail"],
        "mcap_sanity_json": [{"symbol": "KLAC", "status": "clean"}],
        "methodology_version": "1.0",
    }


def test_four_tables_and_indexes_exist(store):
    conn = sqlite3.connect(str(store.db_path))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert EXPECTED_TABLES <= tables
    assert {
        "idx_ffdh_basket_effective",
        "idx_fx_daily_date",
        "idx_fss_symbol_date",
        "idx_btv_basket_date",
    } <= indexes
    for table in EXPECTED_TABLES:
        _validate_table(table)


def test_replace_disclosure_snapshot_is_atomic(store):
    store.replace_fund_disclosure_snapshot(
        "SOXX", "2025-09-30", "disclosure", [_holding()],
        rebalance_close_date="2025-09-19",
        composition_effective_date="2025-09-22",
        composition_available_date="2025-11-26",
        fetched_at="2026-07-14T02:00:00Z",
    )
    with pytest.raises(ValueError):
        store.replace_fund_disclosure_snapshot(
            "SOXX", "2025-09-30", "disclosure",
            [_holding(), {**_holding(1), "included": 2}],
            rebalance_close_date="2025-09-19",
            composition_effective_date="2025-09-22",
            composition_available_date="2025-11-26",
            fetched_at="2026-07-14T02:00:00Z",
        )
    rows = store.get_fund_disclosure_snapshots(
        "SOXX", holding_date="2025-09-30", source_kind="disclosure")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["alias_mode"] == "authoritative"
    assert json.loads(rows[0]["snapshot_warnings_json"]) == [
        "non_reconstitution_membership_delta"]


def test_live_snapshot_is_frozen_per_rebalance_unless_explicit_refresh(store):
    common = dict(
        basket_symbol="SOXX", source_kind="live",
        rebalance_close_date="2026-06-19",
        composition_effective_date="2026-06-22",
        composition_available_date="2026-07-14",
        fetched_at="2026-07-14T02:00:00Z",
    )
    store.replace_fund_disclosure_snapshot(
        holding_date="2026-07-14", rows=[_holding()], **common)
    assert store.replace_fund_disclosure_snapshot(
        holding_date="2026-07-15", rows=[_holding(weight=9.0)], **common) == 0
    frozen = store.get_fund_disclosure_snapshots("SOXX", source_kind="live")
    assert {(row["holding_date"], row["weight_pct"]) for row in frozen} == {
        ("2026-07-14", 8.5)}
    store.replace_fund_disclosure_snapshot(
        holding_date="2026-07-15", rows=[_holding(weight=9.0)],
        refresh_live=True, **common)
    rows = store.get_fund_disclosure_snapshots("SOXX", source_kind="live")
    assert {(row["holding_date"], row["weight_pct"]) for row in rows} == {
        ("2026-07-15", 9.0)}


def test_fx_upsert_is_idempotent_and_asof_returns_observation_date(store):
    rows = [{
        "currency": "EUR", "date": "2026-07-03",
        "usd_per_unit": 1.14376, "source_symbol": "EURUSD", "source": "fmp",
    }]
    assert store.upsert_fx_daily(rows) == 1
    assert store.upsert_fx_daily([{**rows[0], "usd_per_unit": 1.15}]) == 1
    got = store.get_fx_at_or_before("EUR", "2026-07-05")
    assert got["date"] == "2026-07-03"
    assert got["usd_per_unit"] == 1.15


def test_split_history_replace_handles_vendor_ratio_revision_and_empty(store):
    store.replace_stock_splits("KLAC", [{
        "date": "2026-06-12", "numerator": 10, "denominator": 1,
        "split_type": "stock-split", "source": "fmp",
    }])
    store.replace_stock_splits("KLAC", [{
        "date": "2026-06-12", "numerator": 5, "denominator": 1,
        "split_type": "stock-split", "source": "fmp",
    }])
    got = store.get_stock_splits("KLAC")
    assert len(got) == 1
    assert (got[0]["numerator"], got[0]["denominator"]) == (5.0, 1.0)
    assert store.replace_stock_splits("KLAC", []) == 0
    assert store.get_stock_splits("KLAC") == []


def test_split_replace_rejects_bad_batch_without_deleting_old(store):
    good = {"date": "2026-06-12", "numerator": 10, "denominator": 1,
            "split_type": "stock-split", "source": "fmp"}
    store.replace_stock_splits("KLAC", [good])
    with pytest.raises(ValueError):
        store.replace_stock_splits("KLAC", [{**good, "denominator": 0}])
    assert store.get_stock_splits("KLAC")[0]["numerator"] == 10.0


def test_valuation_range_replace_removes_stale_output_and_roundtrips_json(store):
    store.replace_basket_ttm_valuation_range(
        "SOXX", "2026-07-09", "2026-07-10",
        [_valuation("2026-07-09", 23.0), _valuation("2026-07-10", 24.5)],
    )
    store.replace_basket_ttm_valuation_range(
        "SOXX", "2026-07-09", "2026-07-10",
        [_valuation("2026-07-10", 25.0)],
    )
    got = store.get_basket_ttm_valuations(
        "SOXX", from_date="2026-07-09", to_date="2026-07-10")
    assert [row["valuation_date"] for row in got] == ["2026-07-10"]
    assert json.loads(got[0]["members_json"]) == [{"symbol": "NVDA"}]
    assert json.loads(got[0]["warnings_json"]) == ["live-tail"]


def test_valuation_bad_batch_rolls_back_complete_prior_range(store):
    store.replace_basket_ttm_valuation_range(
        "SOXX", "2026-07-10", "2026-07-10", [_valuation()])
    with pytest.raises(ValueError):
        store.replace_basket_ttm_valuation_range(
            "SOXX", "2026-07-10", "2026-07-10",
            [{**_valuation(pe=99.0), "weight_coverage": 1.5}],
        )
    got = store.get_basket_ttm_valuations("SOXX")
    assert got[0]["rebalance_weighted_ttm_pe_gaap_proxy"] == 24.5
