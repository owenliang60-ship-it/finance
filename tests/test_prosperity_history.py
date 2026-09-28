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
