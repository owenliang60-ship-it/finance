"""Tests for prosperity D9 history helpers (M2 Tasks 2-4).

Pure logic over a hand-seeded MarketStore: quarter-end calendar, as-of member
union with alias folding, street EPS depth, split suspects, gap reasons and
the read-only coverage report.
"""
import json

import pytest

from src.data.market_store import MarketStore
from src.data.prosperity_history import members_by_quarter_end, quarter_ends
from scripts.verify_prosperity_history import main as verify_main

RENAME_ABC_COR = {"alias": "ABC", "canonical": "COR", "kind": "rename",
                  "status": "verified", "reviewed_at": "2026-09-27",
                  "evidence": "test"}


@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


def seed_hmcap(store, symbol, day, market_cap):
    store.upsert_historical_market_cap(symbol, [{"date": day, "market_cap": market_cap}])


# ---------------------------------------------------------------------------
# Task 2 — quarter ends, member union, targets freezer
# ---------------------------------------------------------------------------

def test_quarter_ends_span():
    q = quarter_ends("2021-09-30", "2026-06-30")
    assert q[0] == "2021-09-30" and q[-1] == "2026-06-30" and len(q) == 20
    assert q[1:3] == ["2021-12-31", "2022-03-31"]


def test_quarter_ends_rejects_non_quarter_end():
    with pytest.raises(ValueError):
        quarter_ends("2021-09-29", "2022-06-30")


def test_members_union_uses_canonical_codes(tmp_store):
    seed_hmcap(tmp_store, "ABC", "2022-06-30", 30e9)
    seed_hmcap(tmp_store, "COR", "2024-06-28", 40e9)
    seed_hmcap(tmp_store, "SMALL", "2024-06-28", 5e9)
    by_qe = members_by_quarter_end(tmp_store, ["2022-06-30", "2024-06-30"],
                                   aliases=[RENAME_ABC_COR])
    assert by_qe == {"2022-06-30": ["COR"], "2024-06-30": ["COR"]}


def test_targets_cli_writes_union(tmp_store, tmp_path, monkeypatch):
    seed_hmcap(tmp_store, "AAA", "2021-09-30", 20e9)
    seed_hmcap(tmp_store, "BBB", "2021-12-31", 20e9)
    monkeypatch.setattr("scripts.verify_prosperity_history._open_store",
                        lambda: tmp_store)
    out = tmp_path / "targets.json"
    rc = verify_main(["targets", "--start", "2021-09-30", "--end", "2021-12-31",
                      "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["symbols"] == ["AAA", "BBB"]
    assert doc["by_quarter_end"] == {"2021-09-30": ["AAA"], "2021-12-31": ["BBB"]}
    assert doc["quarter_ends"] == ["2021-09-30", "2021-12-31"]
    assert doc["generated_at"] and "code_sha" in doc


# ---------------------------------------------------------------------------
# Task 3 — street EPS depth, split suspects, gap reasons
# ---------------------------------------------------------------------------

from src.data.prosperity_history import (  # noqa: E402
    INHERENT_GAP_REASONS,
    eps_gap_reason,
    gap_reason,
    split_suspects,
    street_eps_depth,
    three_table_ok,
)

FISCALS = quarter_ends("2022-03-31", "2026-03-31")          # 17 quarters


def _eps_rows(fiscals, eps=1.0, lag_days=35):
    from datetime import date, timedelta
    return [{"announce_date": (date.fromisoformat(f) + timedelta(days=lag_days)).isoformat(),
             "fiscal_date": f, "match_method": "estimates_window", "eps_actual": eps}
            for f in fiscals]


def test_street_eps_depth_full_window():
    d = street_eps_depth(_eps_rows(FISCALS[-13:]), "2026-06-30")
    assert d["consecutive"] == 13
    assert d["sue_ok"] and d["sue_full"] and d["dsue_ok"]
    assert d["latest_fiscal"] == "2026-03-31"
    assert d["mapped_ratio"] == 1.0 and d["dup_fiscal"] == []


def test_street_eps_depth_counts_to_the_break():
    rows = _eps_rows(FISCALS[:5] + FISCALS[6:])              # drop one quarter
    d = street_eps_depth(rows, "2026-06-30")
    assert d["consecutive"] == 11
    assert d["sue_ok"] and not d["dsue_ok"] and not d["sue_full"]


def test_street_eps_depth_ignores_future_unmapped_and_null_eps():
    rows = _eps_rows(FISCALS[-12:])
    rows[-1]["announce_date"] = "2026-07-15"                  # after as_of
    rows.append({"announce_date": "2021-02-01", "fiscal_date": None,
                 "match_method": "none", "eps_actual": 0.5})
    rows.append({"announce_date": "2026-08-01", "fiscal_date": None,
                 "match_method": "none", "eps_actual": None})  # scheduled row
    d = street_eps_depth(rows, "2026-06-30")
    assert d["consecutive"] == 11
    assert d["latest_fiscal"] == "2025-12-31"
    assert d["mapped_ratio"] == pytest.approx(11 / 12)


def test_street_eps_depth_reports_duplicate_fiscal():
    rows = _eps_rows(FISCALS[-11:])
    rows.append(dict(rows[-1], announce_date="2026-05-20"))
    d = street_eps_depth(rows, "2026-06-30")
    assert d["dup_fiscal"] == ["2026-03-31"]
    assert d["consecutive"] == 11


def test_split_suspects_flags_unadjusted_eps():
    rows = _eps_rows(FISCALS[-6:-3], eps=2.0) + _eps_rows(FISCALS[-3:], eps=1.02)
    splits = [{"date": "2025-08-01", "numerator": 2, "denominator": 1}]
    out = split_suspects(rows, splits, "2026-06-30")
    assert len(out) == 1 and out[0]["split_date"] == "2025-08-01"
    assert out[0]["ratio"] == 2.0


def test_split_suspects_quiet_when_adjusted_or_after_asof():
    rows = _eps_rows(FISCALS[-6:], eps=1.0)
    splits = [{"date": "2025-08-01", "numerator": 2, "denominator": 1},
              {"date": "2026-09-01", "numerator": 4, "denominator": 1}]
    assert split_suspects(rows, splits, "2026-06-30") == []


def test_gap_reason_classes():
    window_start = "2019-06-01"
    assert gap_reason([], None, window_start) == "not_attempted"
    assert gap_reason([], "fetch_failed", window_start) == "fetch_failed"
    assert gap_reason([], "pending", window_start) == "fetch_failed"
    assert gap_reason([], "provider_empty", window_start) == "provider_empty"
    assert gap_reason(["2021-03-31", "2021-06-30"], "done", window_start) == "short_history"
    assert gap_reason(["2019-03-31", "2021-06-30"], "done", window_start) == "gap_in_series"
    assert INHERENT_GAP_REASONS == {"short_history"}


def test_eps_gap_reason_classes():
    start = "2023-06-01"
    d = street_eps_depth(_eps_rows(FISCALS[-5:]), "2026-06-30")
    assert eps_gap_reason(d, FISCALS[-5:], [], "done", start) == "short_history"
    none_d = street_eps_depth([], "2026-06-30")
    assert eps_gap_reason(none_d, FISCALS, [], "done", start) == "no_earnings_rows"
    unmapped = [{"announce_date": "2023-02-01", "fiscal_date": None,
                 "match_method": "none", "eps_actual": 1.0}]
    d2 = street_eps_depth(_eps_rows(FISCALS[-5:]) + unmapped, "2026-06-30")
    assert eps_gap_reason(d2, FISCALS, unmapped, "done", start) == "unmapped"
    assert eps_gap_reason(d, FISCALS, [], "done", start) == "missing_quarters"


def test_eps_gap_reason_short_statements_from_failed_collection_is_fixable():
    none_d = street_eps_depth([], "2026-06-30")
    assert eps_gap_reason(none_d, [], [], "fetch_failed", "2023-06-01") == \
        "statements_fetch_failed"
    assert eps_gap_reason(none_d, [], [], None, "2023-06-01") == "statements_not_attempted"
    # an old company whose income only goes back a few quarters is not "young"
    assert eps_gap_reason(none_d, ["2020-03-31", "2026-03-31"], [], "done",
                          "2023-06-01") == "statements_gap_in_series"


def test_street_eps_depth_stale_latest_is_not_computable():
    d = street_eps_depth(_eps_rows(FISCALS[:12]), "2026-06-30")   # ends 2024-12-31
    assert d["consecutive"] == 12 and d["stale"] is True
    assert not d["sue_ok"] and not d["dsue_ok"]


def test_street_eps_depth_merges_near_duplicate_fiscal_dates():
    rows = _eps_rows(FISCALS[-11:])
    rows.append({"announce_date": "2026-05-25", "fiscal_date": "2026-03-29",
                 "match_method": "statement_window", "eps_actual": 1.0})
    d = street_eps_depth(rows, "2026-06-30")
    assert d["consecutive"] == 11
    assert d["dup_fiscal"] == ["2026-03-31"]


def test_three_table_ok_delegates_to_backfill_window(tmp_store, monkeypatch):
    seen = []
    monkeypatch.setattr("scripts.backfill_extended_fundamentals.has_asof_window",
                        lambda store, s, a, quarters=8: seen.append((s, a)) or True)
    assert three_table_ok(tmp_store, "ABC", "2024-06-30") is True
    assert seen == [("ABC", "2024-06-30")]


# ---------------------------------------------------------------------------
# Task 4 — read-only coverage report
# ---------------------------------------------------------------------------

def _seed_statements_filed(store, symbol, fiscals, filing_lag_days):
    from datetime import date, timedelta
    rows = [{"date": f, "symbol": symbol, "period": "Q", "revenue": 1.0,
             "filingDate": (date.fromisoformat(f)
                            + timedelta(days=filing_lag_days)).isoformat()}
            for f in fiscals]
    store.upsert_income(symbol, rows)
    store.upsert_balance_sheet(symbol, rows)
    store.upsert_cash_flow(symbol, rows)


@pytest.fixture
def report_store(tmp_store):
    # AAA: 13 statement quarters filed +40d; 13 street EPS quarters +35d and
    #      one ancient unmapped row. BBB: two quarters filed +70d, no EPS.
    _seed_statements_filed(tmp_store, "AAA", quarter_ends("2022-09-30", "2025-09-30"), 40)
    _seed_statements_filed(tmp_store, "BBB", ["2025-03-31", "2025-06-30"], 70)
    tmp_store.replace_fmp_earnings("AAA", _eps_rows(quarter_ends("2022-06-30", "2025-06-30"))
                                   + [{"announce_date": "2020-01-01", "fiscal_date": None,
                                       "match_method": "none", "eps_actual": 1.0}])
    tmp_store.create_backfill_run("d9", ["AAA", "BBB"], ["income", "balance", "cashflow"], {})
    conn = tmp_store._get_conn()
    with conn:
        for sym in ("AAA", "BBB"):
            for ds in ("income", "balance", "cashflow"):
                tmp_store.complete_job_in_conn(conn, "d9", sym, ds, "done")
    return tmp_store


def _run_report(store, tmp_path, monkeypatch, extra=()):
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({
        "symbols": ["AAA", "BBB"], "quarter_ends": ["2025-06-30", "2025-09-30"],
        "by_quarter_end": {"2025-06-30": ["AAA", "BBB"], "2025-09-30": ["AAA", "BBB"]}}))
    monkeypatch.setattr("scripts.verify_prosperity_history._open_store", lambda: store)
    out_dir = tmp_path / "out"
    rc = verify_main(["report", "--targets", str(targets), "--run-id", "d9",
                      "--out-dir", str(out_dir), "--date", "2026-09-28", *extra])
    return rc, json.loads((out_dir / "d9-coverage-2026-09-28.json").read_text()), out_dir


def test_report_quarter_metrics_and_gate(report_store, tmp_path, monkeypatch):
    rc, doc, out_dir = _run_report(report_store, tmp_path, monkeypatch)
    assert rc == 1                                   # 50% < 95% three-table gate
    q = {row["quarter_end"]: row for row in doc["quarter_ends"]}
    jun = q["2025-06-30"]
    assert jun["members"] == 2
    assert jun["three_table_ok"] == 1 and jun["three_table_pass"] is False
    assert jun["sue_ok"] == 1 and jun["sue_full"] == 0 and jun["dsue_ok"] == 1
    assert jun["mapping"] == {"estimates_window": 12, "none": 1}
    assert q["2025-09-30"]["sue_full"] == 1
    assert jun["freeze"] == {"cov_d60": 0.5, "cov_d80": 1.0, "first_day_ge95": 70}
    assert q["2025-09-30"]["freeze"]["first_day_ge95"] is None
    assert doc["street_eps_threshold"] == "pending_boss"
    assert (out_dir / "d9-coverage-2026-09-28.md").exists()


def test_report_gap_reasons_split_inherent_vs_fixable(report_store, tmp_path, monkeypatch):
    rc, doc, _ = _run_report(report_store, tmp_path, monkeypatch)
    three = {(g["quarter_end"], g["symbol"]): g["reason"] for g in doc["gaps"]["three_table"]}
    assert three[("2025-06-30", "BBB")] == "short_history"
    eps = {(g["quarter_end"], g["symbol"]): g for g in doc["gaps"]["street_eps"]}
    assert eps[("2025-06-30", "BBB")]["reason"] == "short_history"
    assert eps[("2025-06-30", "BBB")]["inherent"] is True
    assert doc["summary"]["eps_fixable_symbols"] == []


def test_report_eps_targets_out_lists_fixable_only(report_store, tmp_path, monkeypatch):
    # Give BBB a long statement history but no EPS rows -> fixable gap.
    _seed_statements_filed(report_store, "BBB", quarter_ends("2022-06-30", "2025-06-30"), 70)
    eps_out = tmp_path / "eps_targets.json"
    rc, doc, _ = _run_report(report_store, tmp_path, monkeypatch,
                             extra=("--eps-targets-out", str(eps_out)))
    assert doc["summary"]["eps_fixable_symbols"] == ["BBB"]
    assert json.loads(eps_out.read_text())["symbols"] == ["BBB"]


def test_report_three_table_gate_passes(tmp_store, tmp_path, monkeypatch):
    _seed_statements_filed(tmp_store, "AAA", quarter_ends("2022-09-30", "2025-09-30"), 40)
    targets = tmp_path / "t.json"
    targets.write_text(json.dumps({"symbols": ["AAA"], "quarter_ends": ["2025-06-30"],
                                   "by_quarter_end": {"2025-06-30": ["AAA"]}}))
    monkeypatch.setattr("scripts.verify_prosperity_history._open_store", lambda: tmp_store)
    rc = verify_main(["report", "--targets", str(targets), "--out-dir",
                      str(tmp_path / "o"), "--date", "2026-09-28"])
    assert rc == 0


def test_report_one_short_table_is_not_short_history(tmp_store, tmp_path, monkeypatch):
    # income/cashflow reach 2019, balance only 2025: fixable, not a young company.
    fiscals = quarter_ends("2019-03-31", "2025-03-31")
    rows = [{"date": f, "symbol": "OLD", "period": "Q", "revenue": 1.0, "filingDate": f}
            for f in fiscals]
    tmp_store.upsert_income("OLD", rows)
    tmp_store.upsert_cash_flow("OLD", rows)
    tmp_store.upsert_balance_sheet("OLD", rows[-2:])
    tmp_store.create_backfill_run("d9", ["OLD"], ["income", "balance", "cashflow"], {})
    conn = tmp_store._get_conn()
    with conn:
        for ds in ("income", "balance", "cashflow"):
            tmp_store.complete_job_in_conn(conn, "d9", "OLD", ds, "done")
    targets = tmp_path / "t.json"
    targets.write_text(json.dumps({"symbols": ["OLD"], "quarter_ends": ["2025-06-30"],
                                   "by_quarter_end": {"2025-06-30": ["OLD"]}}))
    monkeypatch.setattr("scripts.verify_prosperity_history._open_store", lambda: tmp_store)
    verify_main(["report", "--targets", str(targets), "--run-id", "d9",
                 "--out-dir", str(tmp_path / "o"), "--date", "x"])
    doc = json.loads((tmp_path / "o" / "d9-coverage-x.json").read_text())
    assert doc["gaps"]["three_table"][0]["reason"] == "gap_in_series"
    assert doc["summary"]["three_table_fixable_symbols"] == ["OLD"]
