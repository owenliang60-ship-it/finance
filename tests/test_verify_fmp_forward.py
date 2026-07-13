"""verify_fmp_forward 测试：manifest 分母 SSOT / 90% gate / 动态 missing 三分类 / RO。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.verify_fmp_forward import parse_args, verify_run  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402

SNAP = "2026-07-12"
FUTURE_Q = ["2026-09-30", "2026-12-31", "2027-03-31", "2027-06-30"]
BASKETS = ("SPY", "QQQ", "SOX", "IGV", "XLF")


@pytest.fixture
def db(tmp_path):
    return tmp_path / "market.db"


def _seed(db_path, symbols, covered, snapshot=SNAP, kind="weekly",
          quarter_empty=(), status="running", holdings_baskets=BASKETS,
          extra_manifest=None):
    store = MarketStore(db_path)
    summary_json = json.dumps({
        "run_state": {
            "quarter_empty": sorted(quarter_empty),
            "earnings_failed": [],
        },
        "attempts": [],
    })
    store.upsert_fmp_forward_run({
        "snapshot_date": snapshot, "run_kind": kind, "status": status,
        "target_universe": symbols, "started_at": "2026-07-12T02:45:00Z",
        "summary_json": summary_json, **(extra_manifest or {}),
    })
    for sym in covered:
        rows = [{
            "snapshot_date": snapshot, "fiscal_date": fd, "period_type": "Q",
            "snapshot_kind": kind, "eps_avg": 1.0, "eps_high": 1.2,
            "eps_low": 0.8, "rev_avg": 1e9, "rev_high": None, "rev_low": None,
            "net_income_avg": 1e8, "ebitda_avg": 2e8,
            "num_analysts_eps": 5, "num_analysts_rev": 5,
        } for fd in FUTURE_Q]
        store.upsert_fmp_estimates(sym, rows)
    for basket in holdings_baskets:
        store.replace_fmp_etf_holdings(basket, snapshot, [{
            "raw_row_index": 0, "raw_asset": "AAPL", "symbol": "AAPL",
            "name": "APPLE INC", "weight_pct": 5.0, "market_value": 1e9,
            "updated_at": snapshot, "included": 1,
            "filter_reason": None, "covered_by": None,
        }])
    store.close()
    return store


SYMS10 = ["S%02d" % i for i in range(10)]


def test_missing_db_fails_without_creating_file(tmp_path):
    db_path = tmp_path / "nope" / "market.db"
    rc, report = verify_run(db_path, tmp_path, SNAP)
    assert rc == 1
    assert not report["ok"]
    assert db_path.exists() is False


def test_missing_manifest_fails_never_reconstructs(db, tmp_path):
    _seed(db, SYMS10, covered=SYMS10, snapshot=SNAP)
    rc, report = verify_run(db, tmp_path, "2026-07-19")  # 无该日 manifest
    assert rc == 1
    assert any("manifest" in f for f in report["failures"])


def test_manifest_count_json_mismatch_fails(db, tmp_path):
    _seed(db, SYMS10, covered=SYMS10)
    # 手工破坏 target_count（绕过 store 校验）
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE fmp_forward_runs SET target_count = 99")
    conn.commit()
    conn.close()
    rc, report = verify_run(db, db.parent, SNAP)
    assert rc == 1
    assert any("target_count" in f for f in report["failures"])


def test_exactly_90pct_passes_below_fails(db, tmp_path):
    _seed(db, SYMS10, covered=SYMS10[:9])  # 9/10 = 90%
    rc, report = verify_run(db, db.parent, SNAP)
    assert rc == 0 and report["ok"]
    assert report["universe"]["pct"] == 90.0
    assert report["universe"]["missing"] == ["S09"]

    db2 = db.parent / "market2.db"
    _seed(db2, SYMS10, covered=SYMS10[:8])  # 80%
    rc2, report2 = verify_run(db2, db.parent, SNAP)
    assert rc2 == 1 and not report2["ok"]


def test_only_future_nonnull_quarters_count(db, tmp_path):
    store = _seed(db, ["AAA"], covered=[])
    store = MarketStore(db)
    # 4 行未来季但其中 1 行 eps_avg NULL → 不算 covered_4q
    rows = [{
        "snapshot_date": SNAP, "fiscal_date": fd, "period_type": "Q",
        "snapshot_kind": "weekly",
        "eps_avg": None if fd == FUTURE_Q[0] else 1.0,
    } for fd in FUTURE_Q]
    store.upsert_fmp_estimates("AAA", rows)
    # 过去季 + 120d 窗口行不算
    store.upsert_fmp_estimates("AAA", [{
        "snapshot_date": SNAP, "fiscal_date": "2026-06-30",
        "period_type": "Q", "snapshot_kind": "weekly", "eps_avg": 1.0,
    }])
    store.close()
    rc, report = verify_run(db, db.parent, SNAP)
    assert rc == 1
    assert report["universe"]["covered_4q"] == 0
    assert "AAA" in report["universe"]["missing"]


def test_backfill_and_weekly_rows_do_not_cross_satisfy(db, tmp_path):
    store = _seed(db, ["AAA"], covered=[])
    store = MarketStore(db)
    rows = [{
        "snapshot_date": SNAP, "fiscal_date": fd, "period_type": "Q",
        "snapshot_kind": "backfill", "eps_avg": 1.0,
    } for fd in FUTURE_Q]
    store.upsert_fmp_estimates("AAA", rows)  # 只有 backfill 行
    store.close()
    rc, report = verify_run(db, db.parent, SNAP, run_kind="weekly")
    assert rc == 1  # backfill 行不满足 weekly 覆盖
    assert "AAA" in report["universe"]["missing"]


def test_backfill_run_kind_reads_backfill_manifest_and_depth(db, tmp_path):
    _seed(db, ["AAA", "BBB"], covered=[], kind="backfill")
    store = MarketStore(db)
    for sym in ("AAA", "BBB"):
        rows = [{
            "snapshot_date": SNAP, "fiscal_date": fd, "period_type": "Q",
            "snapshot_kind": "backfill", "eps_avg": 1.0,
        } for fd in ["2021-03-31", "2021-06-30"] + FUTURE_Q]
        store.upsert_fmp_estimates(sym, rows)
    store.close()
    rc, report = verify_run(db, db.parent, SNAP, run_kind="backfill")
    assert rc == 0
    assert report["run_kind"] == "backfill"
    assert report["universe"]["covered_4q"] == 2  # 同样的 ≥4 未来非空 gate
    assert report["estimates"]["backfill_rows"] > 0
    assert report["estimates"]["min_fiscal_date"] == "2021-03-31"
    assert report["estimates"]["max_fiscal_date"] == max(FUTURE_Q)


def test_all_five_baskets_require_nonzero_rows(db, tmp_path):
    _seed(db, SYMS10, covered=SYMS10, holdings_baskets=("SPY", "QQQ",
                                                        "SOX", "IGV"))
    rc, report = verify_run(db, db.parent, SNAP)
    assert rc == 1
    assert any("XLF" in f for f in report["failures"])


def test_blank_assets_reported_not_dropped(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    store = MarketStore(db)
    store.replace_fmp_etf_holdings("SPY", SNAP, [
        {"raw_row_index": 0, "raw_asset": "", "symbol": None, "name": None,
         "weight_pct": 0.1, "market_value": 1e6, "updated_at": SNAP,
         "included": 0, "filter_reason": "unrecognized_asset",
         "covered_by": None},
        {"raw_row_index": 1, "raw_asset": "AAPL", "symbol": "AAPL",
         "name": "APPLE INC", "weight_pct": 5.0, "market_value": 1e9,
         "updated_at": SNAP, "included": 1, "filter_reason": None,
         "covered_by": None},
    ])
    store.close()
    rc, report = verify_run(db, db.parent, SNAP)
    assert report["holdings"]["SPY"]["blank_assets"] == 1
    assert report["holdings"]["SPY"]["rows"] == 2
    assert any("unrecognized_asset" in w for w in report["warnings"])


def test_foreign_unmapped_becomes_warning(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    store = MarketStore(db)
    store.replace_fmp_etf_holdings("IGV", SNAP, [
        {"raw_row_index": 0, "raw_asset": "SAP.DE", "symbol": None,
         "name": "SAP SE", "weight_pct": 0.5, "market_value": 5e8,
         "updated_at": SNAP, "included": 0,
         "filter_reason": "foreign_listing_unmapped", "covered_by": None},
        {"raw_row_index": 1, "raw_asset": "MSFT", "symbol": "MSFT",
         "name": "MICROSOFT CORP", "weight_pct": 8.0, "market_value": 8e9,
         "updated_at": SNAP, "included": 1, "filter_reason": None,
         "covered_by": None},
    ])
    store.close()
    rc, report = verify_run(db, db.parent, SNAP)
    assert any("foreign_listing_unmapped" in w for w in report["warnings"])


def test_issuer_name_collision_warning(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    store = MarketStore(db)
    # 两只 included、去类别后缀后同名、且不在 share_class_groups → 警示
    store.replace_fmp_etf_holdings("QQQ", SNAP, [
        {"raw_row_index": 0, "raw_asset": "XYZA", "symbol": "XYZA",
         "name": "XYZ HOLDINGS CLASS A", "weight_pct": 1.0,
         "market_value": 1e9, "updated_at": SNAP, "included": 1,
         "filter_reason": None, "covered_by": None},
        {"raw_row_index": 1, "raw_asset": "XYZB", "symbol": "XYZB",
         "name": "XYZ HOLDINGS CLASS B", "weight_pct": 1.0,
         "market_value": 1e9, "updated_at": SNAP, "included": 1,
         "filter_reason": None, "covered_by": None},
        # 无关公司不得被模糊合并
        {"raw_row_index": 2, "raw_asset": "MSFT", "symbol": "MSFT",
         "name": "MICROSOFT CORP", "weight_pct": 8.0, "market_value": 8e9,
         "updated_at": SNAP, "included": 1, "filter_reason": None,
         "covered_by": None},
    ])
    store.close()
    rc, report = verify_run(db, db.parent, SNAP)
    collision_warnings = [w for w in report["warnings"] if "collision" in w]
    assert collision_warnings
    assert all("MICROSOFT" not in w for w in collision_warnings)


def test_dynamic_missing_three_way_classification(db, tmp_path):
    # 上一个 complete weekly run：quarter_empty = {S08}
    _seed(db, SYMS10, covered=[], snapshot="2026-07-05", status="complete",
          quarter_empty=["S08"], holdings_baskets=())
    # 当前 run：S08 仍 empty（结构性），S09 新 empty（候选）
    store = MarketStore(db)
    summary_json = json.dumps({
        "run_state": {
            "quarter_empty": ["S08", "S09"],
            "earnings_failed": [],
        },
        "attempts": [],
    })
    store.upsert_fmp_forward_run({
        "snapshot_date": SNAP, "run_kind": "weekly", "status": "running",
        "target_universe": SYMS10, "started_at": "2026-07-12T02:45:00Z",
        "summary_json": summary_json,
    })
    for sym in SYMS10[:7]:  # S07/S08/S09 未覆盖
        rows = [{
            "snapshot_date": SNAP, "fiscal_date": fd, "period_type": "Q",
            "snapshot_kind": "weekly", "eps_avg": 1.0,
        } for fd in FUTURE_Q]
        store.upsert_fmp_estimates(sym, rows)
    for basket in BASKETS:
        store.replace_fmp_etf_holdings(basket, SNAP, [{
            "raw_row_index": 0, "raw_asset": "AAPL", "symbol": "AAPL",
            "name": "APPLE INC", "weight_pct": 5.0, "market_value": 1e9,
            "updated_at": SNAP, "included": 1, "filter_reason": None,
            "covered_by": None,
        }])
    store.close()
    rc, report = verify_run(db, db.parent, SNAP)
    uni = report["universe"]
    assert uni["known_structural_missing"] == ["S08"]
    assert uni["structural_candidates"] == ["S09"]
    assert uni["unexpected_missing"] == ["S07"]
    assert set(uni["missing"]) == {"S07", "S08", "S09"}
    # 分母与 gate 不变：7/10 = 70% < 90% → fail
    assert rc == 1
    # 结构性集合漂移（{S08} → {S08,S09}）→ 警示
    assert any("structural" in w and "drift" in w for w in report["warnings"])


def test_first_run_valid_empty_are_candidates(db, tmp_path):
    _seed(db, SYMS10, covered=SYMS10[:9], quarter_empty=["S09"])
    rc, report = verify_run(db, db.parent, SNAP)
    assert rc == 0
    uni = report["universe"]
    assert uni["known_structural_missing"] == []
    assert uni["structural_candidates"] == ["S09"]  # 首跑无已知结构集，保持可见
    assert uni["unexpected_missing"] == []


def test_earnings_match_none_counted(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    store = MarketStore(db)
    store.replace_fmp_earnings("AAA", [
        {"announce_date": "2026-04-24", "fiscal_date": "2026-03-31",
         "match_method": "estimates_window", "eps_actual": 1.0,
         "eps_estimated": 0.9, "revenue_actual": 1e9,
         "revenue_estimated": 1e9, "last_updated": "2026-07-12T00:00:00Z"},
        {"announce_date": "2026-05-01", "fiscal_date": None,
         "match_method": "none", "eps_actual": 2.0, "eps_estimated": None,
         "revenue_actual": None, "revenue_estimated": None,
         "last_updated": "2026-07-12T00:00:00Z"},
    ])
    store.close()
    rc, report = verify_run(db, db.parent, SNAP)
    assert report["earnings"]["matched"] == 1
    assert report["earnings"]["unmatched"] == 1


def test_stage_data_does_not_require_basket_valuation(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    rc, report = verify_run(db, db.parent, SNAP, stage="data")
    assert rc == 0


def test_stage_full_requires_six_basket_rows(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    rc, report = verify_run(db, db.parent, SNAP, stage="full")
    assert rc == 1
    assert any("basket" in f for f in report["failures"])

    store = MarketStore(db)
    for basket in ("SPY", "QQQ", "SOX", "MAGS", "IGV", "XLF"):
        store.upsert_fmp_basket_valuation({
            "basket": basket, "snapshot_date": SNAP, "fwd_pe_blend": 25.0,
            "fwd_pe_ntm": 23.0, "members_json": json.dumps([]),
        })
    store.close()
    rc2, report2 = verify_run(db, db.parent, SNAP, stage="full")
    assert rc2 == 0


def test_report_shape_stable(db, tmp_path):
    _seed(db, ["AAA"], covered=["AAA"])
    rc, report = verify_run(db, db.parent, SNAP)
    assert set(report.keys()) >= {"ok", "snapshot_date", "run_kind",
                                  "universe", "holdings", "earnings",
                                  "estimates", "warnings", "failures"}
    assert set(report["universe"].keys()) >= {
        "expected", "covered_4q", "pct", "missing",
        "known_structural_missing", "structural_candidates",
        "unexpected_missing"}
    assert set(report["holdings"].keys()) == set(BASKETS)


def test_cli_defaults_and_json(db, tmp_path):
    args = parse_args(["--stage", "data", "--snapshot-date", SNAP])
    assert args.stage == "data" and args.run_kind == "weekly"
    assert args.min_quarter_coverage_pct == 90.0
    with pytest.raises(SystemExit) as ei:
        parse_args(["--stage", "data", "--snapshot-date", "bad-date"])
    assert ei.value.code == 2
