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
# Task 3 — street EPS depth / SUE, split suspects, gap reasons
# ---------------------------------------------------------------------------

from src.data.prosperity_history import (  # noqa: E402
    INHERENT_GAP_REASONS,
    arrival_day,
    eps_gap_reason,
    gap_reason,
    split_suspects,
    street_eps_depth,
    three_table_ok,
)

FISCALS = quarter_ends("2022-03-31", "2026-03-31")          # 17 quarters


def _eps_rows(fiscals, eps=None, lag_days=35):
    """Mapped street EPS rows; default EPS varies so YoY changes have σ > 0."""
    from datetime import date, timedelta
    return [{"announce_date": (date.fromisoformat(f) + timedelta(days=lag_days)).isoformat(),
             "fiscal_date": f, "match_method": "estimates_window",
             "eps_actual": eps if eps is not None else 1 + 0.05 * i + 0.03 * (i % 3)}
            for i, f in enumerate(fiscals)]


def test_street_eps_depth_full_window():
    d = street_eps_depth(_eps_rows(FISCALS[-13:]), "2026-06-30", "2026-03-31")
    assert d["consecutive"] == 13
    assert d["depth_ok"] and d["depth_full"] and d["sue_ok"] and d["dsue_ok"]
    assert d["latest_fiscal"] == "2026-03-31"
    assert d["mapped_ratio"] == 1.0 and d["dup_fiscal"] == []


def test_street_eps_depth_counts_to_the_break():
    rows = _eps_rows(FISCALS[:5] + FISCALS[6:])              # drop one quarter
    d = street_eps_depth(rows, "2026-06-30")
    assert d["consecutive"] == 11
    assert d["depth_ok"] and not d["depth_full"]


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


def test_street_eps_depth_merges_near_duplicate_fiscal_dates():
    rows = _eps_rows(FISCALS[-11:])
    rows.append({"announce_date": "2026-05-25", "fiscal_date": "2026-03-29",
                 "match_method": "statement_window", "eps_actual": 1.0})
    d = street_eps_depth(rows, "2026-06-30")
    assert d["consecutive"] == 11
    assert d["dup_fiscal"] == ["2026-03-31"]


def test_street_eps_missing_current_quarter_is_not_computable():
    # Statements reached 2026-03; EPS stops at 2025-12 with 13 deep quarters.
    rows = _eps_rows(FISCALS[-14:-1])
    d = street_eps_depth(rows, "2026-06-30", current_fiscal="2026-03-31")
    assert d["behind_current"] and d["stale"]
    assert not (d["depth_ok"] or d["sue_ok"] or d["dsue_ok"])
    assert eps_gap_reason(d, FISCALS, [], "done", "2023-06-01") == "missing_current_quarter"


def test_street_eps_stale_without_anchor():
    d = street_eps_depth(_eps_rows(FISCALS[:12]), "2026-06-30")   # ends 2024-12-31
    assert d["consecutive"] == 12 and d["stale"] and not d["sue_ok"]


def test_constant_eps_is_deep_but_sue_degenerate():
    d = street_eps_depth(_eps_rows(FISCALS[-13:], eps=1.0), "2026-06-30", "2026-03-31")
    assert d["depth_full"] and not d["sue_ok"] and d["sue_missing"] == "zero_sigma"
    assert eps_gap_reason(d, FISCALS, [], "done", "2023-06-01") == "sue_degenerate"


def test_sue_needs_six_sigma_observations():
    d = street_eps_depth(_eps_rows(FISCALS[-10:]), "2026-06-30", "2026-03-31")
    assert not d["sue_ok"] and d["sue_missing"] == "few_sigma_obs"


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
    ws = "2019-06-01"
    assert gap_reason([], None, ws) == "not_attempted"
    assert gap_reason([], "fetch_failed", ws) == "fetch_failed"
    assert gap_reason([], "pending", ws) == "fetch_failed"
    assert gap_reason([], "provider_empty", ws) == "provider_empty"
    assert gap_reason(["2019-03-31", "2021-06-30"], "done", ws) == "gap_in_series"
    short = ["2021-03-31", "2021-06-30"]
    # "done" alone is not evidence the company is young
    assert gap_reason(short, "done", ws) == "history_depth_unknown"
    assert gap_reason(short, "done", ws, listed_after="2020-11-01",
                      listed_by="2020-11-01") == "short_history"
    assert gap_reason(short, "done", ws, listed_by="2005-01-01") == "vendor_short"
    assert INHERENT_GAP_REASONS == {"short_history", "sue_degenerate"}


def test_eps_gap_reason_classes():
    start = "2023-06-01"
    d = street_eps_depth(_eps_rows(FISCALS[-5:]), "2026-06-30")
    assert eps_gap_reason(d, FISCALS[-5:], [], "done", start,
                          listed_after="2025-01-01") == "short_history"
    assert eps_gap_reason(d, FISCALS[-5:], [], "done", start) == \
        "statements_history_depth_unknown"
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
    assert eps_gap_reason(none_d, ["2020-03-31", "2026-03-31"], [], "done",
                          "2023-06-01") == "statements_gap_in_series"


def test_three_table_ok_delegates_to_backfill_window(tmp_store, monkeypatch):
    seen = []
    monkeypatch.setattr("scripts.backfill_extended_fundamentals.has_asof_window",
                        lambda store, s, a, quarters=8: seen.append((s, a)) or True)
    assert three_table_ok(tmp_store, "ABC", "2024-06-30") is True
    assert seen == [("ABC", "2024-06-30")]


def test_arrival_season_is_a_partition():
    rows = [{"date": "2026-04-07", "accepted_date": None, "filing_date": "2026-05-05"}]
    tables = {"i": rows, "b": rows, "c": rows}
    assert arrival_day(tables, "2026-03-31") == 35
    assert arrival_day(tables, "2026-06-30") is None


def test_arrival_waits_for_all_three_tables():
    def row(filed):
        return [{"date": "2026-03-31", "accepted_date": None, "filing_date": filed}]
    tables = {"i": row("2026-05-05"), "b": row("2026-06-29"), "c": row("2026-06-29")}
    assert arrival_day(tables, "2026-03-31") == 90


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


def _seed_profile(store, symbol, ipo_date):
    conn = store._get_conn()
    with conn:
        conn.execute("INSERT OR REPLACE INTO company_profile (symbol, payload, updated_at) "
                     "VALUES (?, ?, '2026-09-28')",
                     (symbol, json.dumps({"symbol": symbol, "ipoDate": ipo_date})))


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
    path = out_dir / "d9-coverage-2026-09-28.json"
    return rc, (json.loads(path.read_text()) if path.exists() else None), out_dir


def test_report_quarter_metrics_and_gate(report_store, tmp_path, monkeypatch):
    rc, doc, out_dir = _run_report(report_store, tmp_path, monkeypatch)
    assert rc == 1                                   # 50% < 95% three-table gate
    q = {row["quarter_end"]: row for row in doc["quarter_ends"]}
    jun = q["2025-06-30"]
    assert jun["members"] == 2
    assert jun["three_table_ok"] == 1 and jun["three_table_pass"] is False
    assert (jun["depth_ok"], jun["depth_full"], jun["sue_ok"], jun["dsue_ok"]) == (1, 0, 1, 1)
    assert jun["mapping"] == {"estimates_window": 12, "none": 1}
    assert q["2025-09-30"]["depth_full"] == 1
    assert jun["freeze"] == {"cov_d60": 0.5, "cov_d80": 1.0, "first_day_ge95": 70}
    assert q["2025-09-30"]["freeze"]["first_day_ge95"] is None
    assert doc["street_eps_threshold"] == "pending_boss"
    assert (out_dir / "d9-coverage-2026-09-28.md").exists()


def test_report_no_listing_evidence_stays_fixable(report_store, tmp_path, monkeypatch):
    rc, doc, _ = _run_report(report_store, tmp_path, monkeypatch)
    three = {(g["quarter_end"], g["symbol"]): g for g in doc["gaps"]["three_table"]}
    assert three[("2025-06-30", "BBB")]["reason"] == "history_depth_unknown"
    assert three[("2025-06-30", "BBB")]["inherent"] is False
    eps = {(g["quarter_end"], g["symbol"]): g for g in doc["gaps"]["street_eps"]}
    assert eps[("2025-06-30", "BBB")]["reason"] == "statements_history_depth_unknown"
    assert doc["summary"]["eps_fixable_symbols"] == ["BBB"]


def test_report_listing_evidence_makes_short_history(report_store, tmp_path, monkeypatch):
    _seed_profile(report_store, "BBB", "2025-02-14")
    rc, doc, _ = _run_report(report_store, tmp_path, monkeypatch)
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


def test_report_refuses_to_overwrite_frozen_eps_targets(report_store, tmp_path, monkeypatch):
    eps_out = tmp_path / "eps_targets.json"
    eps_out.write_text(json.dumps({"symbols": ["ABC", "DEF"]}))
    rc, doc, _ = _run_report(report_store, tmp_path, monkeypatch,
                             extra=("--eps-targets-out", str(eps_out)))
    assert rc == 2 and doc is None
    assert json.loads(eps_out.read_text())["symbols"] == ["ABC", "DEF"]


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


# ---------------------------------------------------------------------------
# Second external review repros
# ---------------------------------------------------------------------------

def _linear_rows(bump=None):
    rows = _eps_rows(FISCALS)                       # 2022-03 .. 2026-03
    for i, r in enumerate(rows):
        r["eps_actual"] = float(i)
    if bump is not None:
        rows[bump]["eps_actual"] += 1.0
    return rows


def test_constant_yoy_change_has_zero_sigma():
    # EPS 0,1,2,...: every YoY change is 4 -> standard deviation 0.
    d = street_eps_depth(_linear_rows(), "2026-06-30", "2026-03-31")
    assert d["depth_full"] and not d["sue_ok"] and d["sue_missing"] == "zero_sigma"


def test_sue_is_judged_at_the_statement_quarter_not_a_later_eps():
    rows = _linear_rows(bump=15)                    # 2025-12 bumped: only its YoY is 5
    at_statements = street_eps_depth(rows, "2026-06-30", current_fiscal="2025-12-31")
    assert at_statements["latest_fiscal"] == "2026-03-31"
    assert at_statements["anchor_fiscal"] == "2025-12-31"
    assert at_statements["sue_missing"] == "zero_sigma" and not at_statements["sue_ok"]
    ahead = street_eps_depth(rows, "2026-06-30", current_fiscal="2026-03-31")
    assert ahead["sue_ok"]


def test_first_market_cap_is_not_listing_evidence(report_store, tmp_path, monkeypatch):
    report_store.upsert_historical_market_cap("AAA", [{"date": "2021-04-13", "market_cap": 5e10}])
    report_store.upsert_historical_market_cap("BBB", [{"date": "2025-01-02", "market_cap": 5e10}])
    rc, doc, _ = _run_report(report_store, tmp_path, monkeypatch)
    eps = {(g["quarter_end"], g["symbol"]): g for g in doc["gaps"]["street_eps"]}
    assert eps[("2025-06-30", "BBB")]["reason"] == "statements_history_depth_unknown"
    assert doc["summary"]["eps_fixable_symbols"] == ["BBB"]
