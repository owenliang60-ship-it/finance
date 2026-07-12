"""MarketStore FMP forward 5 表契约测试（4 业务表 + 1 run-manifest 审计表）。"""
import json
import sqlite3

import pytest

from src.data.market_store import MarketStore, _validate_table

EXPECTED = {
    "fmp_estimates",
    "fmp_earnings",
    "fmp_etf_holdings_snapshot",
    "fmp_basket_valuation",
    "fmp_forward_runs",
}


@pytest.fixture
def store(tmp_path):
    s = MarketStore(tmp_path / "market.db")
    yield s
    s.close()


def _est_row(fiscal_date, snapshot_date="2026-07-12", period_type="Q",
             snapshot_kind="weekly", eps_avg=1.5):
    return {
        "snapshot_date": snapshot_date, "fiscal_date": fiscal_date,
        "period_type": period_type, "snapshot_kind": snapshot_kind,
        "eps_avg": eps_avg, "eps_high": eps_avg + 0.2, "eps_low": eps_avg - 0.2,
        "rev_avg": 1e9, "rev_high": 1.1e9, "rev_low": 0.9e9,
        "net_income_avg": 2.5e8, "ebitda_avg": 4e8,
        "num_analysts_eps": 10, "num_analysts_rev": 9,
    }


def _earn_row(announce_date, fiscal_date="2026-06-30",
              match_method="estimates_window", eps_actual=1.2):
    return {
        "announce_date": announce_date, "fiscal_date": fiscal_date,
        "match_method": match_method, "eps_actual": eps_actual,
        "eps_estimated": 1.1, "revenue_actual": 1e9, "revenue_estimated": 0.95e9,
        "last_updated": "2026-07-12T00:00:00Z",
    }


def _holding_row(idx, raw_asset="AAPL", symbol="AAPL", included=1,
                 filter_reason=None, covered_by=None):
    return {
        "raw_row_index": idx, "raw_asset": raw_asset, "symbol": symbol,
        "name": "TEST", "weight_pct": 1.0, "market_value": 1e9,
        "updated_at": "2026-07-10", "included": included,
        "filter_reason": filter_reason, "covered_by": covered_by,
    }


# 1. 所有表和索引存在
def test_all_five_tables_and_indexes_exist(store):
    conn = sqlite3.connect(str(store.db_path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert EXPECTED <= tables
    assert {"idx_fest_symbol_snap", "idx_fest_snap",
            "idx_fearn_symbol_fiscal", "idx_ffr_status"} <= indexes


# 2. 关同一 tmp DB 再开成功（索引幂等）
def test_reopen_same_db_is_idempotent(tmp_path):
    path = tmp_path / "market.db"
    s1 = MarketStore(path)
    s1.close()
    s2 = MarketStore(path)  # 二次 executescript 不得抛 index already exists
    s2.close()


# 3. 同 PK 重跑替换、不同 snapshot 追加
def test_estimates_same_pk_replaces_different_snapshot_appends(store):
    rows = [_est_row("2026-09-30", eps_avg=1.5)]
    assert store.upsert_fmp_estimates("TESTCO", rows) == 1
    rows2 = [_est_row("2026-09-30", eps_avg=1.8)]
    store.upsert_fmp_estimates("TESTCO", rows2)
    got = store.get_fmp_estimates("TESTCO", snapshot_date="2026-07-12")
    assert len(got) == 1
    assert got[0]["eps_avg"] == 1.8

    store.upsert_fmp_estimates("TESTCO", [_est_row("2026-09-30",
                                                   snapshot_date="2026-07-19")])
    all_weekly = store.get_fmp_estimates("TESTCO", snapshot_kind="weekly",
                                         snapshot_date=None)
    # 默认只取最新 weekly snapshot
    assert {r["snapshot_date"] for r in all_weekly} == {"2026-07-19"}


# 4. snapshot_kind CHECK
def test_snapshot_kind_check_rejects_invalid(store):
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        store.upsert_fmp_estimates(
            "TESTCO", [_est_row("2026-09-30", snapshot_kind="daily")])


# 5. earnings null 清理 + upsert 同事务
def test_earnings_null_cleanup_and_upsert_one_transaction(store):
    store.replace_fmp_earnings("TESTCO", [
        _earn_row("2026-04-24", eps_actual=1.15),
        _earn_row("2026-07-24", fiscal_date=None, match_method="none",
                  eps_actual=None),  # 预排行
    ])
    # 重排：新批不含 7/24 预排行 → 幽灵行必须被清删；已报告行保留
    store.replace_fmp_earnings("TESTCO", [
        _earn_row("2026-07-30", fiscal_date=None, match_method="none",
                  eps_actual=None),
    ])
    got = store.get_fmp_earnings("TESTCO")
    dates = {r["announce_date"] for r in got}
    assert "2026-07-24" not in dates
    assert "2026-04-24" in dates  # 非 null actual 不清删
    assert "2026-07-30" in dates


def test_earnings_replace_rolls_back_on_bad_row(store):
    store.replace_fmp_earnings("TESTCO", [
        _earn_row("2026-07-24", fiscal_date=None, match_method="none",
                  eps_actual=None),
    ])
    with pytest.raises(Exception):
        store.replace_fmp_earnings("TESTCO", [
            _earn_row("2026-07-30", eps_actual=1.3),
            {"eps_actual": 9.9},  # 缺 announce_date PK → 整批回滚
        ])
    got = store.get_fmp_earnings("TESTCO")
    assert {r["announce_date"] for r in got} == {"2026-07-24"}  # 原状态保留


# 6. 两行空 raw_asset、不同 raw_row_index 都存活
def test_blank_raw_asset_rows_both_survive(store):
    rows = [
        _holding_row(0, raw_asset="", symbol=None, included=0,
                     filter_reason="unrecognized_asset"),
        _holding_row(1, raw_asset="", symbol=None, included=0,
                     filter_reason="unrecognized_asset"),
        _holding_row(2, raw_asset="AAPL", symbol="AAPL", included=1),
    ]
    assert store.replace_fmp_etf_holdings("SPY", "2026-07-12", rows) == 3
    got = store.get_fmp_etf_holdings("SPY", "2026-07-12")
    assert len(got) == 3
    included = store.get_fmp_etf_holdings("SPY", "2026-07-12", included_only=True)
    assert [r["symbol"] for r in included] == ["AAPL"]


def test_holdings_replace_rolls_back_on_bad_row(store):
    store.replace_fmp_etf_holdings("SPY", "2026-07-12", [_holding_row(0)])
    with pytest.raises(Exception):
        store.replace_fmp_etf_holdings("SPY", "2026-07-12", [
            _holding_row(0),
            {"raw_asset": "X"},  # 缺 raw_row_index/included → 回滚
        ])
    got = store.get_fmp_etf_holdings("SPY", "2026-07-12")
    assert len(got) == 1  # 旧快照未被半删


# 7. basket valuation members_json round-trip
def test_basket_valuation_members_json_roundtrip(store):
    members = [{"symbol": "AAPL", "mcap": 3.7e12, "ntm_ni": 1.2e11,
                "blend_ni": 1.15e11}]
    store.upsert_fmp_basket_valuation({
        "basket": "MAGS", "snapshot_date": "2026-07-12",
        "fwd_pe_blend": 30.1, "fwd_pe_ntm": 28.9,
        "total_mcap": 2e13, "ntm_net_income": 7e11, "blend_net_income": 6.6e11,
        "n_members": 7, "n_covered_ntm": 7, "n_covered_blend": 7,
        "mcap_coverage_ntm": 1.0, "mcap_coverage_blend": 1.0,
        "weight_coverage": 1.0,
        "members_json": json.dumps(members),
    })
    got = store.get_fmp_basket_valuation("MAGS", snapshot_date="2026-07-12")
    assert len(got) == 1
    assert json.loads(got[0]["members_json"]) == members


# 8. run manifest：同 PK 只更新执行统计，universe JSON 不可变
def test_forward_run_manifest_immutable_universe(store):
    universe = ["MSFT", "AAPL", "AAPL", "NVDA"]
    store.upsert_fmp_forward_run({
        "snapshot_date": "2026-07-12", "run_kind": "weekly",
        "status": "running", "target_universe": universe,
        "started_at": "2026-07-12T02:45:00Z",
    })
    run = store.get_fmp_forward_run("2026-07-12", "weekly")
    assert run["target_universe"] == ["AAPL", "MSFT", "NVDA"]  # 排序去重
    assert run["target_count"] == 3
    assert run["status"] == "running"

    # 同 universe 更新统计 → 允许
    store.upsert_fmp_forward_run({
        "snapshot_date": "2026-07-12", "run_kind": "weekly",
        "status": "complete", "target_universe": ["AAPL", "NVDA", "MSFT"],
        "quarter_success": 3, "quarter_failure_count": 0,
        "started_at": "2026-07-12T02:45:00Z",
        "completed_at": "2026-07-12T03:10:00Z",
        "summary_json": json.dumps({"ok": True}),
    })
    run = store.get_fmp_forward_run("2026-07-12", "weekly")
    assert run["status"] == "complete"
    assert run["quarter_success"] == 3
    assert run["target_universe"] == ["AAPL", "MSFT", "NVDA"]

    # 不同 universe → 拒绝改写历史
    with pytest.raises(ValueError):
        store.upsert_fmp_forward_run({
            "snapshot_date": "2026-07-12", "run_kind": "weekly",
            "status": "complete", "target_universe": ["AAPL"],
            "started_at": "2026-07-12T02:45:00Z",
        })


def test_forward_run_missing_returns_none(store):
    assert store.get_fmp_forward_run("2099-01-01", "weekly") is None


# 9. 白名单
def test_all_five_names_in_validate_table():
    for name in EXPECTED:
        _validate_table(name)  # 不抛即通过


# 10. 现有 forward_estimates 表不受影响
def test_yfinance_forward_estimates_still_readable(store):
    store.upsert_forward_estimates("AAPL", [
        {"date": "2026-07-11", "period": "0q", "eps_avg": 1.4},
    ])
    got = store.get_latest_forward_estimates("AAPL")
    assert got and got[0]["eps_avg"] == 1.4


# get_fmp_estimates 契约细则
def test_get_fmp_estimates_kind_filters(store):
    store.upsert_fmp_estimates("TESTCO", [
        _est_row("2026-09-30", snapshot_date="2026-07-01",
                 snapshot_kind="backfill"),
        _est_row("2026-09-30", snapshot_date="2026-07-12",
                 snapshot_kind="weekly"),
    ])
    # 默认 weekly：backfill 不可能成为隐式最新 PIT 快照
    got = store.get_fmp_estimates("TESTCO")
    assert {r["snapshot_kind"] for r in got} == {"weekly"}
    # 显式审计口径 snapshot_kind=None → 全量
    audit = store.get_fmp_estimates("TESTCO", snapshot_kind=None)
    assert {r["snapshot_kind"] for r in audit} == {"weekly", "backfill"}
    # period_type 过滤 + 校验
    q_only = store.get_fmp_estimates("TESTCO", period_type="Q")
    assert all(r["period_type"] == "Q" for r in q_only)
    with pytest.raises(ValueError):
        store.get_fmp_estimates("TESTCO", period_type="M")


def test_estimates_rejects_missing_required_dates(store):
    with pytest.raises(ValueError):
        store.upsert_fmp_estimates("TESTCO", [{"period_type": "Q",
                                               "snapshot_kind": "weekly"}])
