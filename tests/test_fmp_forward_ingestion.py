"""fmp_forward_ingestion 纯转换层测试：配置加载 / holdings 规范化 / universe resolver。"""
import copy
import json
from pathlib import Path

import pytest

from src.data.fmp_forward_ingestion import (
    ETF_HOLDING_SOURCES,
    load_basket_configs,
    normalize_holdings,
    resolve_fmp_forward_universe,
    validate_listing_overrides,
    validate_share_class_groups,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fmp_forward"
CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"

LISTING = {"NVMI.TA": "NVMI", "OTEX.TO": "OTEX", "LSPD.TO": "LSPD"}
GROUPS = {"GOOGL": ["GOOG"], "FOXA": ["FOX"], "NWSA": ["NWS"], "HEI": ["HEI.A"]}


def _rows():
    return json.loads((FIXTURES / "etf_holdings.json").read_text())


def _norm(rows=None):
    return normalize_holdings("SPY", "2026-07-12", rows if rows is not None
                              else _rows(), LISTING, GROUPS)


# ---- 配置契约 ----

def test_etf_holding_sources_frozen():
    assert ETF_HOLDING_SOURCES == {
        "SPY": "SPY", "QQQ": "QQQ", "SOX": "SOXX", "IGV": "IGV", "XLF": "XLF",
    }


def test_load_basket_configs_from_repo():
    listing, groups, mags = load_basket_configs(CONFIG_DIR)
    assert listing["NVMI.TA"] == "NVMI"
    assert groups["GOOGL"] == ["GOOG"]
    assert mags == ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]


def test_invalid_configs_fail_fast():
    with pytest.raises(ValueError):  # lowercase 映射目标
        validate_listing_overrides({"NVMI.TA": "nvmi"})
    with pytest.raises(ValueError):  # 重复副类股
        validate_share_class_groups({"A": ["X"], "B": ["X"]})
    with pytest.raises(ValueError):  # 主类股同时是副类股
        validate_share_class_groups({"GOOGL": ["GOOG"], "GOOG": ["Y"]})


# ---- holdings 规范化 ----

def test_normal_ticker_included():
    out = _norm()
    aapl = [r for r in out if r["raw_asset"] == "AAPL"][0]
    assert aapl["included"] == 1
    assert aapl["symbol"] == "AAPL"
    assert aapl["filter_reason"] is None


def test_mapped_foreign_ticker_included_as_us_symbol():
    out = _norm()
    nvmi = [r for r in out if r["raw_asset"] == "NVMI.TA"][0]
    assert nvmi["included"] == 1
    assert nvmi["symbol"] == "NVMI"


def test_unmapped_dotted_foreign_excluded():
    rows = _rows() + [{"asset": "SAP.DE", "name": "SAP SE",
                       "weightPercentage": 0.5, "marketValue": 5e8,
                       "updatedAt": "2026-07-10"}]
    out = _norm(rows)
    sap = [r for r in out if r["raw_asset"] == "SAP.DE"][0]
    assert sap["included"] == 0
    assert sap["filter_reason"] == "foreign_listing_unmapped"
    assert sap["symbol"] is None


def test_cash_fund_and_swap_excluded_with_distinct_reasons():
    out = _norm()
    cash = [r for r in out if r["raw_asset"] == "XTSLA"][0]
    swap = [r for r in out if r["raw_asset"] == "SWP001"][0]
    assert (cash["included"], cash["filter_reason"]) == (0, "cash_or_fund")
    assert (swap["included"], swap["filter_reason"]) == (0, "swap")


def test_secondary_share_class_excluded_with_covered_by():
    out = _norm()
    goog = [r for r in out if r["raw_asset"] == "GOOG"][0]
    googl = [r for r in out if r["raw_asset"] == "GOOGL"][0]
    assert (goog["included"], goog["filter_reason"], goog["covered_by"]) == (
        0, "dual_class_secondary", "GOOGL")
    assert googl["included"] == 1


def test_dotted_us_share_class_not_treated_as_foreign():
    rows = [{"asset": "HEI.A", "name": "HEICO CORP CLASS A",
             "weightPercentage": 0.1, "marketValue": 1e8,
             "updatedAt": "2026-07-10"}]
    out = _norm(rows)
    assert out[0]["filter_reason"] == "dual_class_secondary"
    assert out[0]["covered_by"] == "HEI"


def test_every_input_row_emitted_once_with_enumeration_index():
    rows = _rows()
    out = _norm(rows)
    assert len(out) == len(rows)
    assert [r["raw_row_index"] for r in out] == list(range(len(rows)))


def test_two_blank_asset_rows_both_emitted():
    out = _norm()
    blanks = [r for r in out if r["raw_asset"] == ""]
    assert len(blanks) == 2
    # 空 asset + CASH 名 → cash_or_fund；全空 → fail-closed unrecognized_asset
    reasons = {r["filter_reason"] for r in blanks}
    assert reasons == {"cash_or_fund", "unrecognized_asset"}
    assert all(r["included"] == 0 for r in blanks)


def test_no_input_mutation():
    rows = _rows()
    snapshot = copy.deepcopy(rows)
    _norm(rows)
    assert rows == snapshot


def test_output_rows_contain_all_table_columns():
    out = _norm()
    expected_cols = {"basket", "snapshot_date", "raw_row_index", "raw_asset",
                     "symbol", "name", "weight_pct", "market_value",
                     "updated_at", "included", "filter_reason", "covered_by"}
    for r in out:
        assert expected_cols <= set(r.keys())
        assert r["basket"] == "SPY"
        assert r["snapshot_date"] == "2026-07-12"


# ---- universe resolver ----

def test_resolver_unions_core_extended_included_mags():
    holdings = _norm()
    universe = resolve_fmp_forward_universe(
        core_symbols=["qs", "AAPL"],          # QS 是核心池非扩展池成员
        extended_symbols=["MSFT", "AAPL"],
        normalized_holdings=holdings,
        mags_symbols=["TSLA"],
    )
    assert "QS" in universe          # round-4 回归：核心池非扩展池成员必须入 universe
    assert "NVMI" in universe        # 篮子 included 规范化 symbol
    assert "GOOGL" in universe
    assert "GOOG" not in universe    # 副类股不入 universe
    assert "TSLA" in universe
    assert universe == sorted(set(universe))  # 排序去重


def test_resolver_fails_fast_on_empty_core_or_extended():
    holdings = _norm()
    with pytest.raises(ValueError):
        resolve_fmp_forward_universe([], ["MSFT"], holdings, ["TSLA"])
    with pytest.raises(ValueError):
        resolve_fmp_forward_universe(["AAPL"], [], holdings, ["TSLA"])
