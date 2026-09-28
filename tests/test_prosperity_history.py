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
    assert gap_reason([], None, 40, window_start) == "not_attempted"
    assert gap_reason([], "fetch_failed", 40, window_start) == "fetch_failed"
    assert gap_reason([], "pending", 40, window_start) == "fetch_failed"
    assert gap_reason([], "provider_empty", 40, window_start) == "provider_empty"
    assert gap_reason(["2021-03-31", "2021-06-30"], "done", 40,
                      window_start) == "short_history"
    assert gap_reason(["2019-03-31", "2021-06-30"], "done", 40,
                      window_start) == "gap_in_series"
    assert INHERENT_GAP_REASONS == {"short_history"}


def test_eps_gap_reason_classes():
    d = street_eps_depth(_eps_rows(FISCALS[-5:]), "2026-06-30")
    assert eps_gap_reason(d, FISCALS[-5:], []) == "short_history"
    none_d = street_eps_depth([], "2026-06-30")
    assert eps_gap_reason(none_d, FISCALS, []) == "no_earnings_rows"
    unmapped = [{"announce_date": "2023-02-01", "fiscal_date": None,
                 "match_method": "none", "eps_actual": 1.0}]
    d2 = street_eps_depth(_eps_rows(FISCALS[-5:]) + unmapped, "2026-06-30")
    assert eps_gap_reason(d2, FISCALS, unmapped) == "unmapped"
    assert eps_gap_reason(d, FISCALS, []) == "missing_quarters"


def test_three_table_ok_delegates_to_backfill_window(tmp_store, monkeypatch):
    seen = []
    monkeypatch.setattr("scripts.backfill_extended_fundamentals.has_asof_window",
                        lambda store, s, a, quarters=8: seen.append((s, a)) or True)
    assert three_table_ok(tmp_store, "ABC", "2024-06-30") is True
    assert seen == [("ABC", "2024-06-30")]
