"""Schema + fail-closed validation for the three-index historical basket config.

Covers `load_index_pe_basket_configs` (`config/baskets/index_pe_baskets.json`):
the alias between each basket's FMP-forward source identity (`source_basket`,
matching `ETF_HOLDING_SOURCES` in `src/data/fmp_forward_ingestion.py`) and the
disclosure/backfill `basket_symbol` used as the storage key; the frozen
5-year window; per-basket disclosure snapshot coverage/staleness thresholds;
and morning-report display order.

This file does not cover the disclosure normalizer itself or date-semantics
derivation -- see `tests/test_index_holdings_normalizer.py` for that.
"""
import json

import pytest

from src.data.fmp_forward_ingestion import (
    ETF_HOLDING_SOURCES,
    load_index_pe_basket_configs,
)


CONFIG_DIR = __import__("pathlib").Path(__file__).parent.parent / "config" / "baskets"


def _valid_entry(**overrides):
    entry = {
        "source_basket": "SPY",
        "display_order": 1,
        "five_year_window_years": 5,
        "rebalance_months": [3, 6, 9, 12],
        "expected_reconstitution_month": None,
        "market_cap_staleness_days": 7,
        "snapshot_quality": {
            "minimum_members": 480, "maximum_members": 520,
            "minimum_weight": 99.0, "maximum_weight": 101.0,
        },
        "history_available_from": None,
    }
    entry.update(overrides)
    return entry


def _write_config(tmp_path, payload):
    (tmp_path / "index_pe_baskets.json").write_text(
        json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_load_returns_all_three_baskets_with_source_basket_mapping():
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    assert set(configs) == {"SPY", "QQQ", "SOXX"}
    assert configs["SPY"]["source_basket"] == "SPY"
    assert configs["QQQ"]["source_basket"] == "QQQ"
    assert configs["SOXX"]["source_basket"] == "SOX"


def test_soxx_source_basket_matches_existing_live_pit_forward_mapping():
    # The historical disclosure pipeline's basket key must not silently
    # diverge from the already-frozen live PIT forward mapping (SOX index
    # identity -> SOXX holdings-representative ETF).
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    source_basket = configs["SOXX"]["source_basket"]
    assert ETF_HOLDING_SOURCES[source_basket] == "SOXX"


def test_five_year_window_frozen_for_every_basket():
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    for basket in ("SPY", "QQQ", "SOXX"):
        assert configs[basket]["five_year_window_years"] == 5


def test_display_order_is_unique_and_orders_spy_qqq_soxx():
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    orders = [configs[b]["display_order"] for b in configs]
    assert len(set(orders)) == len(orders)
    assert sorted(configs, key=lambda b: configs[b]["display_order"]) == [
        "SPY", "QQQ", "SOXX"]


def test_snapshot_quality_and_staleness_present_for_every_basket():
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    for basket, entry in configs.items():
        assert entry["market_cap_staleness_days"] > 0
        quality = entry["snapshot_quality"]
        assert 0 < quality["minimum_members"] <= quality["maximum_members"]
        assert 0 < quality["minimum_weight"] <= quality["maximum_weight"]


def test_soxx_snapshot_quality_matches_audited_backfill_defaults():
    # These bounds are load-bearing: they must stay identical to the
    # already-audited SOXX defaults in scripts/backfill_soxx_historical_pe.py
    # (validate_snapshot_quality's minimum_members=25, maximum_members=31,
    # minimum_weight=99.5, maximum_weight=100.5), not just "reasonable".
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    quality = configs["SOXX"]["snapshot_quality"]
    assert quality == {
        "minimum_members": 25, "maximum_members": 31,
        "minimum_weight": 99.5, "maximum_weight": 100.5,
    }


def test_soxx_history_gap_boundary_is_frozen():
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    assert configs["SOXX"]["history_available_from"] == "2021-09-01"
    assert configs["SPY"]["history_available_from"] is None
    assert configs["QQQ"]["history_available_from"] is None


def test_soxx_expected_reconstitution_month_is_september():
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    assert configs["SOXX"]["expected_reconstitution_month"] == 9


def test_spy_and_qqq_have_no_expected_reconstitution_month():
    # S&P 500 / Nasdaq-100 do not concentrate membership changes into a
    # single documented month the way SOXX's ICE Semiconductor Sector Index
    # does (see docs/plans/2026-07-19-index-pe-morning-chart-design.md §8):
    # every observed delta is treated as off-cycle rather than assuming an
    # unverified exemption window.
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    assert configs["SPY"]["expected_reconstitution_month"] is None
    assert configs["QQQ"]["expected_reconstitution_month"] is None


def test_duplicate_source_basket_fails_closed(tmp_path):
    payload = {
        "SPY": _valid_entry(source_basket="SPY", display_order=1),
        "IVV": _valid_entry(source_basket="SPY", display_order=2),
    }
    config_dir = _write_config(tmp_path, payload)
    with pytest.raises(ValueError, match="duplicate source_basket"):
        load_index_pe_basket_configs(config_dir)


def test_duplicate_display_order_fails_closed(tmp_path):
    payload = {
        "SPY": _valid_entry(source_basket="SPY", display_order=1),
        "QQQ": _valid_entry(source_basket="QQQ", display_order=1),
    }
    config_dir = _write_config(tmp_path, payload)
    with pytest.raises(ValueError, match="display_order"):
        load_index_pe_basket_configs(config_dir)


def test_expected_reconstitution_month_must_be_one_of_rebalance_months(tmp_path):
    payload = {
        "SPY": _valid_entry(
            source_basket="SPY", display_order=1,
            rebalance_months=[3, 6, 9, 12], expected_reconstitution_month=7),
    }
    config_dir = _write_config(tmp_path, payload)
    with pytest.raises(ValueError, match="expected_reconstitution_month"):
        load_index_pe_basket_configs(config_dir)


def test_non_uppercase_basket_key_fails_closed(tmp_path):
    payload = {"spy": _valid_entry(source_basket="SPY", display_order=1)}
    config_dir = _write_config(tmp_path, payload)
    with pytest.raises(ValueError, match="uppercase"):
        load_index_pe_basket_configs(config_dir)


def test_empty_config_fails_closed(tmp_path):
    config_dir = _write_config(tmp_path, {})
    with pytest.raises(ValueError):
        load_index_pe_basket_configs(config_dir)
