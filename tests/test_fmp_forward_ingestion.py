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


# ========== Task 5: estimates 规范化 + earnings fiscal 匹配 ==========

from src.data.fmp_forward_ingestion import (  # noqa: E402
    extract_valid_quarter_fiscal_dates,
    match_fiscal_date,
    normalize_earnings,
    normalize_estimates,
)

SNAP = "2026-07-12"  # snapshot - 120d = 2026-03-14


def _q_rows():
    return json.loads((FIXTURES / "analyst_estimates_quarter.json").read_text())


def test_weekly_window_boundary_inclusive():
    rows, counters = normalize_estimates("TESTCO", _q_rows(), SNAP, "Q", "weekly")
    dates = {r["fiscal_date"] for r in rows}
    assert "2026-03-14" in dates       # == snapshot - 120d 边界保留
    assert "2026-01-31" not in dates   # 更早一天以上的行剔除
    assert counters["input"] == 7
    assert counters["kept"] == 6


def test_weekly_keeps_future_ntm_rows_and_labels():
    rows, _ = normalize_estimates("TESTCO", _q_rows(), SNAP, "Q", "weekly")
    future = [r for r in rows if r["fiscal_date"] >= SNAP]
    assert len(future) == 4
    assert all(r["period_type"] == "Q" for r in rows)
    assert all(r["snapshot_kind"] == "weekly" for r in rows)
    assert all(r["snapshot_date"] == SNAP for r in rows)

    annual = json.loads((FIXTURES / "analyst_estimates_annual.json").read_text())
    fy_rows, _ = normalize_estimates("TESTCO", annual, SNAP, "FY", "weekly")
    assert all(r["period_type"] == "FY" for r in fy_rows)


def test_backfill_includes_2021_and_labels_backfill():
    raw = _q_rows() + [
        {"symbol": "TESTCO", "date": "2021-03-31", "epsAvg": 0.5},
        {"symbol": "TESTCO", "date": "2020-12-31", "epsAvg": 0.4},
    ]
    rows, _ = normalize_estimates("TESTCO", raw, SNAP, "Q", "backfill")
    dates = {r["fiscal_date"] for r in rows}
    assert "2021-03-31" in dates
    assert "2020-12-31" not in dates  # backfill_start 之前剔除
    assert all(r["snapshot_kind"] == "backfill" for r in rows)


def test_duplicate_vendor_dates_resolve_to_one_row():
    raw = [
        {"symbol": "TESTCO", "date": "2026-09-30", "epsAvg": 1.30},
        {"symbol": "TESTCO", "date": "2026-09-30", "epsAvg": 1.99},
    ]
    rows, counters = normalize_estimates("TESTCO", raw, SNAP, "Q", "weekly")
    assert len(rows) == 1
    assert rows[0]["eps_avg"] == 1.30  # 确定性：保留首行
    assert counters["duplicate"] == 1


def test_malformed_date_skipped_and_counted():
    raw = [
        {"symbol": "TESTCO", "date": "not-a-date", "epsAvg": 1.0},
        {"symbol": "TESTCO", "epsAvg": 1.0},
        {"symbol": "TESTCO", "date": "2026-09-30", "epsAvg": 1.3},
    ]
    rows, counters = normalize_estimates("TESTCO", raw, SNAP, "Q", "weekly")
    assert len(rows) == 1
    assert counters["malformed"] == 2
    assert all(r["fiscal_date"] for r in rows)  # 不产出 null PK


def test_estimates_rejects_bad_mode():
    with pytest.raises(ValueError):
        normalize_estimates("TESTCO", [], SNAP, "Q", "monthly")


def test_vendor_camelcase_maps_only_to_spec_fields():
    raw = [{"symbol": "TESTCO", "date": "2026-09-30", "epsAvg": 1.3,
            "epsHigh": 1.5, "epsLow": 1.2, "revenueAvg": 1e9,
            "revenueHigh": 1.1e9, "revenueLow": 0.9e9,
            "netIncomeAvg": 2.8e8, "ebitdaAvg": 4.3e8,
            "numAnalystsEps": 9, "numAnalystsRevenue": 8,
            "sgaExpenseAvg": 123.0}]  # 非 Spec 字段必须被丢弃
    rows, _ = normalize_estimates("TESTCO", raw, SNAP, "Q", "weekly")
    r = rows[0]
    assert r["eps_avg"] == 1.3 and r["rev_avg"] == 1e9
    assert r["net_income_avg"] == 2.8e8 and r["ebitda_avg"] == 4.3e8
    assert r["num_analysts_eps"] == 9 and r["num_analysts_rev"] == 8
    assert "sga_expense_avg" not in r and "sgaExpenseAvg" not in r


def test_match_fiscal_date_picks_max_within_120d():
    dates = ["2026-03-31", "2026-06-30", "2026-09-30"]
    assert match_fiscal_date("2026-07-24", dates) == "2026-06-30"


def test_match_fiscal_date_121d_gap_is_none():
    assert match_fiscal_date("2026-10-29", ["2026-06-30"]) is None  # 121d
    assert match_fiscal_date("2026-10-28", ["2026-06-30"]) == "2026-06-30"  # 120d


def test_extract_full_quarter_dates_beats_storage_window():
    """8 条历史 earnings 要能对上全量 raw quarter 日期集（而非 120d 存储窗口）。"""
    raw = _q_rows()  # 含 2026-01-31（在 120d 窗口外）
    full_dates = extract_valid_quarter_fiscal_dates(raw)
    assert "2026-01-31" in full_dates
    # announce 2026-03-01 唯一候选是 2026-01-31——该日期在 120d 存储窗口之外，
    # 只有全量 raw 日期集才能匹配到（若用存储窗口该行会失配为 none）
    assert match_fiscal_date("2026-03-01", full_dates) == "2026-01-31"


def test_normalize_earnings_maps_and_matches():
    earnings = json.loads((FIXTURES / "earnings.json").read_text())
    full_dates = extract_valid_quarter_fiscal_dates(_q_rows())
    rows, counters = normalize_earnings("TESTCO", earnings, full_dates)
    assert len(rows) == 2
    reported = [r for r in rows if r["announce_date"] == "2026-04-24"][0]
    scheduled = [r for r in rows if r["announce_date"] == "2026-07-24"][0]
    assert reported["eps_actual"] == 1.15
    assert reported["fiscal_date"] == "2026-03-14"  # 最近已结束财季（41d 滞后）
    assert reported["match_method"] == "estimates_window"
    assert scheduled["eps_actual"] is None  # 预排行保留
    assert scheduled["fiscal_date"] == "2026-06-30"
    assert reported["last_updated"]
    assert counters["matched"] == 2


def test_normalize_earnings_no_match_method_none():
    rows, counters = normalize_earnings(
        "TESTCO",
        [{"symbol": "TESTCO", "date": "2026-04-24", "epsActual": 1.0,
          "epsEstimated": 0.9, "revenueActual": 1e9, "revenueEstimated": 1e9}],
        ["2025-06-30"],  # 相距远超 120d
    )
    assert rows[0]["match_method"] == "none"
    assert rows[0]["fiscal_date"] is None
    assert counters["unmatched"] == 1


def test_weekly_rerun_does_not_downgrade_stored_mapping(tmp_path):
    """冻结行为：重跑时更弱映射（none）不得覆盖既有 estimates_window 映射。"""
    from src.data.market_store import MarketStore

    store = MarketStore(tmp_path / "market.db")
    earnings = json.loads((FIXTURES / "earnings.json").read_text())
    full_dates = extract_valid_quarter_fiscal_dates(_q_rows())
    rows, _ = normalize_earnings("TESTCO", earnings, full_dates)
    store.replace_fmp_earnings("TESTCO", rows)

    # 临时残缺的 raw estimates → 全部 match none
    weak_rows, _ = normalize_earnings("TESTCO", earnings, [])
    store.replace_fmp_earnings("TESTCO", weak_rows)

    got = store.get_fmp_earnings("TESTCO")
    reported = [r for r in got if r["announce_date"] == "2026-04-24"][0]
    assert reported["fiscal_date"] == "2026-03-14"  # 既有映射保留
    assert reported["match_method"] == "estimates_window"
    store.close()
