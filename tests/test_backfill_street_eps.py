"""Tests for the street EPS backfill wrapper and statement-window remap
(prosperity M2 Task 5).

The wrapper reuses the weekly forward chain verbatim (get_earnings →
normalize_earnings → replace_fmp_earnings) with a deeper limit, then fills
missing fiscal mappings from the income statement's fiscal dates. Invariants:
only `match_method='none'` rows are ever remapped, the 120-day rule holds,
a busy lock touches nothing, and a provider outage trips the breaker.
"""
import json

import pytest

from scripts.backfill_street_eps import (
    BREAKER_MIN_SYMBOLS,
    EARNINGS_LIMIT,
    run_street_eps,
)
from src.data.market_store import MarketStore
from tests.test_backfill_runner import FakeLock


@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


def _none_row(announce, eps=1.0):
    return {"announce_date": announce, "fiscal_date": None, "match_method": "none",
            "eps_actual": eps}


def _seed_income(store, symbol, fiscals):
    store.upsert_income(symbol, [{"date": f, "symbol": symbol, "period": "Q",
                                  "revenue": 1.0, "filingDate": f} for f in fiscals])


def _seed_estimates(store, symbol, fiscals):
    store.upsert_fmp_estimates(symbol, [
        {"symbol": symbol, "snapshot_date": "2026-09-19", "fiscal_date": f,
         "period_type": "Q", "snapshot_kind": "weekly", "eps_avg": 1.0}
        for f in fiscals])


def _by_announce(store, symbol):
    return {r["announce_date"]: r for r in store.get_fmp_earnings(symbol)}


# ---------------------------------------------------------------------------
# remap_unmatched_earnings
# ---------------------------------------------------------------------------

def test_remap_only_touches_unmatched(tmp_store):
    tmp_store.replace_fmp_earnings("ABC", [
        _none_row("2019-02-01"),
        {"announce_date": "2025-02-01", "fiscal_date": "2024-12-31",
         "match_method": "estimates_window", "eps_actual": 2.0}])
    n = tmp_store.remap_unmatched_earnings("ABC", ["2018-12-31", "2024-12-28"])
    rows = _by_announce(tmp_store, "ABC")
    assert n == 1
    assert rows["2019-02-01"]["fiscal_date"] == "2018-12-31"
    assert rows["2019-02-01"]["match_method"] == "statement_window"
    assert rows["2019-02-01"]["eps_actual"] == 1.0
    assert rows["2025-02-01"]["fiscal_date"] == "2024-12-31"
    assert rows["2025-02-01"]["match_method"] == "estimates_window"


def test_remap_respects_120_day_rule(tmp_store):
    tmp_store.replace_fmp_earnings("ABC", [_none_row("2019-08-01")])
    assert tmp_store.remap_unmatched_earnings("ABC", ["2019-03-31"]) == 0   # 123 days
    assert _by_announce(tmp_store, "ABC")["2019-08-01"]["match_method"] == "none"


def test_remap_never_duplicates_a_mapped_fiscal(tmp_store):
    # A fast reporter whose true quarter has no statement row would otherwise
    # be pinned onto the previous quarter, which already has its own report.
    tmp_store.replace_fmp_earnings("ABC", [
        {"announce_date": "2019-01-20", "fiscal_date": "2018-12-31",
         "match_method": "estimates_window", "eps_actual": 1.0},
        _none_row("2019-04-20")])
    assert tmp_store.remap_unmatched_earnings("ABC", ["2018-12-31"]) == 0
    assert _by_announce(tmp_store, "ABC")["2019-04-20"]["fiscal_date"] is None


def test_remap_skips_scheduled_rows_without_actual(tmp_store):
    tmp_store.replace_fmp_earnings("ABC", [_none_row("2019-02-01", eps=1.0)])
    conn = tmp_store._get_conn()
    with conn:
        conn.execute("INSERT INTO fmp_earnings (symbol, announce_date, match_method) "
                     "VALUES ('ABC', '2019-05-01', 'none')")
    assert tmp_store.remap_unmatched_earnings("ABC", ["2018-12-31", "2019-03-31"]) == 1
    assert _by_announce(tmp_store, "ABC")["2019-05-01"]["fiscal_date"] is None


# ---------------------------------------------------------------------------
# wrapper
# ---------------------------------------------------------------------------

class FakeEarningsClient:
    def __init__(self, payloads=None, fail=False):
        self.payloads = payloads or {}
        self.fail = fail
        self.calls = []

    def get_earnings(self, symbol, limit=8):
        self.calls.append((symbol, limit))
        if self.fail:
            raise RuntimeError("HTTP 500")
        return self.payloads.get(symbol, [])


def _vendor(announce, eps):
    return {"date": announce, "epsActual": eps, "epsEstimated": None,
            "revenueActual": None, "revenueEstimated": None}


def _targets(tmp_path, symbols):
    path = tmp_path / "eps_targets.json"
    path.write_text(json.dumps({"symbols": symbols}), encoding="utf-8")
    return path


def test_wrapper_fetches_maps_and_remaps(tmp_store, tmp_path):
    _seed_estimates(tmp_store, "ABC", ["2025-12-31"])
    _seed_income(tmp_store, "ABC", ["2019-03-31", "2025-12-31"])
    client = FakeEarningsClient({"ABC": [_vendor("2026-02-01", 2.0),
                                         _vendor("2019-05-01", 1.0),
                                         _vendor("2016-05-01", 0.5)]})
    progress = tmp_path / "progress.json"
    rc = run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                        client=client, lock=FakeLock(), progress_path=progress)
    assert rc == 0
    assert client.calls == [("ABC", EARNINGS_LIMIT)]
    rows = _by_announce(tmp_store, "ABC")
    assert (rows["2026-02-01"]["fiscal_date"], rows["2026-02-01"]["match_method"]) == \
        ("2025-12-31", "estimates_window")
    assert (rows["2019-05-01"]["fiscal_date"], rows["2019-05-01"]["match_method"]) == \
        ("2019-03-31", "statement_window")
    assert rows["2016-05-01"]["match_method"] == "none"
    assert json.loads(progress.read_text())["done"] == ["ABC"]


def test_wrapper_resume_skips_done_and_remap_only_makes_no_calls(tmp_store, tmp_path):
    progress = tmp_path / "progress.json"
    targets = _targets(tmp_path, ["ABC", "DEF"])
    client = FakeEarningsClient({"ABC": [_vendor("2019-05-01", 1.0)],
                                 "DEF": [_vendor("2019-05-01", 1.0)]})
    run_street_eps(targets_file=targets, store=tmp_store, client=client,
                   lock=FakeLock(), progress_path=progress)
    again = FakeEarningsClient()
    run_street_eps(targets_file=targets, store=tmp_store, client=again,
                   lock=FakeLock(), progress_path=progress)
    assert again.calls == []

    _seed_income(tmp_store, "ABC", ["2019-03-31"])
    silent = FakeEarningsClient()
    rc = run_street_eps(targets_file=targets, store=tmp_store, client=silent,
                        lock=FakeLock(), progress_path=tmp_path / "unused.json",
                        remap_only=True)
    assert rc == 0 and silent.calls == []
    assert _by_announce(tmp_store, "ABC")["2019-05-01"]["match_method"] == "statement_window"


def test_wrapper_breaker_trips_after_min_symbols(tmp_store, tmp_path):
    symbols = ["S{:02d}".format(i) for i in range(60)]
    client = FakeEarningsClient(fail=True)
    progress = tmp_path / "progress.json"
    rc = run_street_eps(targets_file=_targets(tmp_path, symbols), store=tmp_store,
                        client=client, lock=FakeLock(), progress_path=progress)
    assert rc == 1
    assert len(client.calls) == BREAKER_MIN_SYMBOLS
    doc = json.loads(progress.read_text())
    assert doc["done"] == [] and len(doc["failed"]) == BREAKER_MIN_SYMBOLS


def test_wrapper_busy_lock_exits_75_untouched(tmp_store, tmp_path):
    client = FakeEarningsClient({"ABC": [_vendor("2019-05-01", 1.0)]})
    progress = tmp_path / "progress.json"
    rc = run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                        client=client, lock=FakeLock(busy=True), progress_path=progress)
    assert rc == 75
    assert client.calls == [] and not progress.exists()
    assert tmp_store.get_fmp_earnings("ABC") == []


def test_wrapper_refuses_progress_from_another_targets_file(tmp_store, tmp_path):
    progress = tmp_path / "progress.json"
    run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                   client=FakeEarningsClient(), lock=FakeLock(), progress_path=progress)
    rc = run_street_eps(targets_file=_targets(tmp_path, ["ABC", "XYZ"]), store=tmp_store,
                        client=FakeEarningsClient(), lock=FakeLock(),
                        progress_path=progress)
    assert rc == 2


def test_wrapper_provider_empty_is_not_a_failure(tmp_store, tmp_path):
    progress = tmp_path / "progress.json"
    rc = run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                        client=FakeEarningsClient(), lock=FakeLock(),
                        progress_path=progress)
    assert rc == 0
    assert json.loads(progress.read_text())["empty"] == ["ABC"]


def test_remap_guard_treats_near_dates_as_one_quarter(tmp_store):
    tmp_store.replace_fmp_earnings("ABC", [
        {"announce_date": "2024-04-25", "fiscal_date": "2024-03-31",
         "match_method": "estimates_window", "eps_actual": 1.0},
        _none_row("2024-05-10")])
    assert tmp_store.remap_unmatched_earnings("ABC", ["2024-03-30"]) == 0


def test_wrapper_retries_empty_symbols_on_rerun(tmp_store, tmp_path):
    progress = tmp_path / "progress.json"
    targets = _targets(tmp_path, ["ABC"])
    run_street_eps(targets_file=targets, store=tmp_store, client=FakeEarningsClient(),
                   lock=FakeLock(), progress_path=progress)
    retry = FakeEarningsClient({"ABC": [_vendor("2019-05-01", 1.0)]})
    run_street_eps(targets_file=targets, store=tmp_store, client=retry,
                   lock=FakeLock(), progress_path=progress)
    assert retry.calls == [("ABC", EARNINGS_LIMIT)]
    doc = json.loads(progress.read_text())
    assert doc["done"] == ["ABC"] and doc["empty"] == []


def test_dry_run_refuses_foreign_progress_like_the_real_run(tmp_store, tmp_path, capsys):
    progress = tmp_path / "progress.json"
    run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                   client=FakeEarningsClient(), lock=FakeLock(), progress_path=progress)
    rc = run_street_eps(targets_file=_targets(tmp_path, ["ABC", "XYZ"]), store=tmp_store,
                        client=None, lock=FakeLock(), progress_path=progress, dry_run=True)
    assert rc == 2


# ---------------------------------------------------------------------------
# mapping protection across remap → fetch (external review P1)
# ---------------------------------------------------------------------------

def test_fetch_never_overwrites_a_repaired_mapping(tmp_store, tmp_path):
    tmp_store.replace_fmp_earnings("ABC", [_none_row("2024-04-20")])
    _seed_income(tmp_store, "ABC", ["2024-03-31"])
    assert tmp_store.remap_unmatched_earnings("ABC", ["2024-03-31"]) == 1
    _seed_estimates(tmp_store, "ABC", ["2023-12-31"])        # true quarter missing
    client = FakeEarningsClient({"ABC": [_vendor("2024-04-20", 1.0)]})
    run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                   client=client, lock=FakeLock(), progress_path=tmp_path / "p.json")
    row = _by_announce(tmp_store, "ABC")["2024-04-20"]
    assert (row["fiscal_date"], row["match_method"]) == ("2024-03-31", "statement_window")


def test_fetch_prefers_the_statement_quarter_over_a_stale_estimate(tmp_store, tmp_path):
    _seed_income(tmp_store, "ABC", ["2023-12-31", "2024-03-31"])
    _seed_estimates(tmp_store, "ABC", ["2023-12-31"])
    client = FakeEarningsClient({"ABC": [_vendor("2024-04-20", 1.0)]})
    run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                   client=client, lock=FakeLock(), progress_path=tmp_path / "p.json")
    row = _by_announce(tmp_store, "ABC")["2024-04-20"]
    assert (row["fiscal_date"], row["match_method"]) == ("2024-03-31", "statement_window")


def test_fetch_uses_estimate_date_for_near_statement_date(tmp_store, tmp_path):
    _seed_income(tmp_store, "ABC", ["2024-03-30"])
    _seed_estimates(tmp_store, "ABC", ["2024-03-31"])
    client = FakeEarningsClient({"ABC": [_vendor("2024-04-20", 1.0)]})
    run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                   client=client, lock=FakeLock(), progress_path=tmp_path / "p.json")
    row = _by_announce(tmp_store, "ABC")["2024-04-20"]
    assert (row["fiscal_date"], row["match_method"]) == ("2024-03-31", "estimates_window")


def test_fetch_quarantines_a_conflicting_new_mapping(tmp_store, tmp_path):
    tmp_store.replace_fmp_earnings("ABC", [
        {"announce_date": "2024-04-20", "fiscal_date": "2024-03-31",
         "match_method": "estimates_window", "eps_actual": 1.0}])
    _seed_estimates(tmp_store, "ABC", ["2024-03-31"])
    progress = tmp_path / "p.json"
    client = FakeEarningsClient({"ABC": [_vendor("2024-04-20", 1.0),
                                         _vendor("2024-05-15", 1.1)]})   # restated repost
    run_street_eps(targets_file=_targets(tmp_path, ["ABC"]), store=tmp_store,
                   client=client, lock=FakeLock(), progress_path=progress)
    rows = _by_announce(tmp_store, "ABC")
    assert rows["2024-05-15"]["match_method"] == "none"
    assert json.loads(progress.read_text())["conflicts"] == {"ABC": 1}
