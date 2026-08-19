"""Tests for the extended fundamentals backfill runner CLI (T10, R6/R14).

The runner is the production driver that turns "the frozen universe" into
"rows in market.db": it takes the shared writer lock, freezes its target list
through the T7 resolver, drives the T8 kernel symbol by symbol, and records
every dataset outcome in the T9 manifest. The invariants under test here are
the ones that make an unattended cron run safe to interrupt: a busy lock
never touches the manifest, a provider outage trips the circuit breaker
before it burns the whole quota, and no crash path can leave a job stuck
`in_progress` forever.

Historical mode (`--include-historical --as-of`) is tested against the
R5-P1-1 scenario: targets are chosen by DATA COMPLETENESS, never by current
membership.
"""
import json
import sqlite3
from datetime import date, datetime

import pytest

import scripts.backfill_extended_fundamentals as runner
from scripts.backfill_extended_fundamentals import (
    CIRCUIT_BREAKER_MIN_DATASETS,
    HISTORICAL_LIMIT_QUARTERS_CAP,
    JOB_STATUS_MAP,
    LOCK_PATH,
    FileLock,
    deepen_limit_quarters,
    has_asof_window,
    parse_args,
    run_backfill,
)
from src.data.fundamental_collector import DATASETS
from src.data.market_store import MarketStore

# ---------------------------------------------------------------------------
# Fixtures: stores seeded by hand (no network, no data/)
# ---------------------------------------------------------------------------

MEMBERSHIP_AS_OF = "2026-01-02"

# Quarter ends used to seed the three current statement tables. Adjacent gaps
# are 90-92 days, comfortably inside FUNDAMENTAL_QUARTER_GAP_MAX_DAYS.
QUARTER_ENDS = [
    "2022-12-31", "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    "2026-03-31", "2026-06-30",
]

STATEMENT_TABLES = ("income_quarterly", "balance_sheet_quarterly",
                    "cash_flow_quarterly")


def _sm_row(symbol, eligible=1, reason="ok"):
    return {
        "symbol": symbol, "cik": None, "company_name": symbol + " Inc.",
        "exchange": "NASDAQ", "is_etf": 0, "is_fund": 0, "is_adr": 0,
        "share_class_of": None, "eligible": eligible, "reason": reason,
        "updated_at": "2026-08-19T00:00:00Z",
    }


def _seed_statements(store, symbol, fiscal_dates):
    """Give a symbol the same fiscal-date coverage in all three current tables."""
    rows = [{"date": d, "symbol": symbol, "period": "Q", "revenue": 1.0}
            for d in fiscal_dates]
    store.upsert_income(symbol, rows)
    store.upsert_balance_sheet(symbol, rows)
    store.upsert_cash_flow(symbol, rows)


def _seed_hmcap(store, symbol, as_of, market_cap=5e11):
    store.upsert_historical_market_cap(symbol, [{"date": as_of, "market_cap": market_cap}])


def _table_dates(store, table, symbol):
    conn = sqlite3.connect(str(store.db_path))
    try:
        sql = "SELECT date FROM " + table + " WHERE symbol = ? ORDER BY date"
        return [r[0] for r in conn.execute(sql, (symbol,)).fetchall()]
    finally:
        conn.close()


@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


@pytest.fixture
def tmp_store_sm3(tmp_store):
    """Three eligible, currently-active symbols."""
    symbols = ["AAA", "BBB", "CCC"]
    tmp_store.upsert_security_master([_sm_row(s) for s in symbols])
    tmp_store.record_membership_snapshot(symbols, as_of=MEMBERSHIP_AS_OF)
    return tmp_store


@pytest.fixture
def tmp_store_sm3_plus_historical(tmp_store_sm3):
    """AAA/BBB/CCC active + OLDCO, an eligible SM identity that is NOT a
    current member (dropped out / delisted). R3-P1-1: it must never appear in
    a default backfill's target list."""
    tmp_store_sm3.upsert_security_master([_sm_row("OLDCO")])
    for symbol in ("AAA", "BBB", "CCC", "OLDCO"):
        _seed_hmcap(tmp_store_sm3, symbol, "2025-06-30")
    return tmp_store_sm3


@pytest.fixture
def tmp_store_sm_many(tmp_store):
    """60 eligible active symbols — enough to cross the circuit breaker's
    250-dataset floor (50 symbols x 5 datasets) with symbols left over."""
    symbols = ["S{:02d}".format(i) for i in range(60)]
    tmp_store.upsert_security_master([_sm_row(s) for s in symbols])
    tmp_store.record_membership_snapshot(symbols, as_of=MEMBERSHIP_AS_OF)
    return tmp_store


@pytest.fixture
def tmp_store_hist_scenario(tmp_store):
    """R5-P1-1 scenario at as_of=2026-03-31:

      AAPL  — current member, three tables hold only the most recent 8Q
              (2024-09-30..2026-06-30), so only 7 land at/below as_of
      MSFT  — current member with a full history back to 2023-03-31
      OLDCO — historical-only candidate (hmcap qualifies), no statement rows
    """
    for symbol in ("AAPL", "MSFT", "OLDCO"):
        tmp_store.upsert_security_master([_sm_row(symbol)])
        _seed_hmcap(tmp_store, symbol, "2026-03-31")
    tmp_store.record_membership_snapshot(["AAPL", "MSFT"], as_of=MEMBERSHIP_AS_OF)
    _seed_statements(tmp_store, "AAPL", QUARTER_ENDS[-8:])
    _seed_statements(tmp_store, "MSFT", QUARTER_ENDS[1:])
    return tmp_store


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeLock:
    """Stands in for the fcntl flock wrapper: `acquire() -> bool` + `release()`."""

    def __init__(self, busy=False):
        self.busy = busy
        self.acquired = False
        self.released = False

    def acquire(self):
        if self.busy:
            return False
        self.acquired = True
        return True

    def release(self):
        self.released = True


def _rows_for(kind, symbol):
    if kind == "profile":
        return [{"symbol": symbol, "companyName": symbol + " Inc.",
                 "sector": "Technology", "isEtf": False, "isFund": False}]
    if kind == "ratios":
        return [{"date": "2025-12-31", "symbol": symbol, "period": "FY",
                 "grossProfitMargin": 0.5}]
    return [{"date": "2026-06-30", "symbol": symbol, "period": "Q2",
             "revenue": 1.0, "filingDate": "2026-07-30"}]


class FakeClient:
    """Stands in for FMPClient.get_dataset_with_status (T4 interface).

    Records every call so tests can pin which symbols were touched and how
    deep the statement pulls went.
    """

    def __init__(self, status="ok", crash_on_call=None, crash_exc=KeyboardInterrupt):
        self.status = status
        self.crash_on_call = crash_on_call
        self.crash_exc = crash_exc
        self.calls = []
        self.symbols_fetched = []

    def get_dataset_with_status(self, kind, symbol, limit=None):
        self.calls.append({"kind": kind, "symbol": symbol, "limit": limit})
        if symbol not in self.symbols_fetched:
            self.symbols_fetched.append(symbol)
        if self.crash_on_call is not None and len(self.calls) >= self.crash_on_call:
            raise self.crash_exc()
        if self.status != "ok":
            return [], self.status
        return _rows_for(kind, symbol), "ok"

    @property
    def last_limit(self):
        """Depth of the most recent STATEMENT pull (profile takes no limit,
        ratios is pinned at 4 by RULING #10 — neither answers "how deep")."""
        for call in reversed(self.calls):
            if call["kind"] in ("income", "balance", "cashflow"):
                return call["limit"]
        raise AssertionError("no statement dataset was ever fetched")


@pytest.fixture
def fake_client_full():
    return FakeClient()


@pytest.fixture
def fake_client_all_500():
    return FakeClient(status="fetch_failed")


@pytest.fixture
def fake_client_crash_mid():
    # 5 datasets per symbol: crashes partway through the second symbol, with
    # that symbol's remaining jobs still claimed/in_progress.
    return FakeClient(crash_on_call=7)


# ---------------------------------------------------------------------------
# Brief Step 1 — mandatory tests
# ---------------------------------------------------------------------------

def test_freeze_targets_from_eligible_nonzero(tmp_store_sm3, fake_client_full):
    lock = FakeLock()
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full, lock=lock)
    assert rc == 0 and tmp_store_sm3.run_progress("r1")["done"] > 0
    assert lock.acquired and lock.released


def test_empty_eligible_exit2(tmp_store, fake_client_full):
    # SM populated but every member blocked -> zero eligible targets.
    tmp_store.upsert_security_master([_sm_row("SOXX", eligible=0, reason="etf")])
    tmp_store.record_membership_snapshot(["SOXX"], as_of=MEMBERSHIP_AS_OF)
    assert run_backfill(run_id="r1", store=tmp_store, client=fake_client_full,
                        lock=FakeLock()) == 2
    assert fake_client_full.symbols_fetched == []


def test_resolver_failloud_exit2(tmp_store, fake_client_full):
    # No membership at all: current_base_universe raises (T7 fail-loud). The
    # runner must translate that into exit 2, never a traceback.
    tmp_store.upsert_security_master([_sm_row("AAA")])
    assert run_backfill(run_id="r1", store=tmp_store, client=fake_client_full,
                        lock=FakeLock()) == 2


def test_lock_busy_exits_75_untouched(tmp_store_sm3, fake_client_full):
    lock = FakeLock(busy=True)
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full, lock=lock)
    assert rc == 75
    with pytest.raises(ValueError):
        tmp_store_sm3.run_progress("r1")        # manifest never created
    assert fake_client_full.symbols_fetched == []
    assert lock.released is False               # nothing acquired, nothing to release


def test_circuit_breaker_aborts_and_preserves_pending(tmp_store_sm_many,
                                                      fake_client_all_500):
    rc = run_backfill(run_id="r1", store=tmp_store_sm_many,
                      client=fake_client_all_500, lock=FakeLock())
    assert rc == 1
    prog = tmp_store_sm_many.run_progress("r1")
    assert prog["pending"] > 0                                   # remainder preserved
    assert prog["in_progress"] == 0
    assert tmp_store_sm_many.get_backfill_run("r1")["status"] == "aborted"
    # Tripped at the floor, not after burning the whole universe.
    assert prog["fetch_failed"] == CIRCUIT_BREAKER_MIN_DATASETS


def test_crash_leaves_no_permanent_in_progress(tmp_store_sm3, fake_client_crash_mid):
    lock = FakeLock()
    with pytest.raises(KeyboardInterrupt):
        run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_crash_mid,
                     lock=lock)
    assert tmp_store_sm3.run_progress("r1")["in_progress"] == 0
    assert lock.released is True                 # lock released on the crash path too


def test_canary_limits_scope(tmp_store_sm_many, fake_client_full):
    run_backfill(run_id="c1", store=tmp_store_sm_many, client=fake_client_full,
                 lock=FakeLock(), canary=2)
    prog = tmp_store_sm_many.run_progress("c1")
    assert prog["total_symbols"] == 2
    assert fake_client_full.symbols_fetched == ["S00", "S01"]   # lexicographic first N


def test_e2e_happy_path_manifest_completes(tmp_store_sm3, fake_client_full):
    # R3-P1-3: ok -> done mapping closes the loop; a happy path must be able
    # to drive the manifest all the way to complete.
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                      lock=FakeLock())
    prog = tmp_store_sm3.run_progress("r1")
    assert rc == 0
    assert prog["done"] == prog["total_jobs"] == 3 * len(DATASETS)
    assert prog["is_complete"] is True
    assert tmp_store_sm3.get_backfill_run("r1")["status"] == "complete"


def test_historical_symbols_never_in_default_targets(tmp_store_sm3_plus_historical,
                                                     fake_client_full):
    # R3-P1-1: OLDCO is an eligible SM identity but not an active member, so
    # a default backfill must never fetch it.
    run_backfill(run_id="r1", store=tmp_store_sm3_plus_historical,
                 client=fake_client_full, lock=FakeLock())
    assert "OLDCO" not in fake_client_full.symbols_fetched
    assert sorted(fake_client_full.symbols_fetched) == ["AAA", "BBB", "CCC"]


def test_historical_targets_by_data_completeness_not_membership(
        tmp_store_hist_scenario, fake_client_full):
    rc = run_backfill(run_id="h1", store=tmp_store_hist_scenario,
                      client=fake_client_full, lock=FakeLock(),
                      include_historical=True, as_of="2026-03-31")
    assert rc == 0
    assert "AAPL" in fake_client_full.symbols_fetched    # member, window short -> re-pull
    assert "MSFT" not in fake_client_full.symbols_fetched  # window already satisfied
    assert "OLDCO" in fake_client_full.symbols_fetched   # historical candidate, no data
    assert tmp_store_hist_scenario.run_progress("h1")["total_symbols"] == 2


def test_historical_no_candidates_exits_2(tmp_store_sm3, fake_client_full):
    # No historical_market_cap at that date -> no as-of denominator at all.
    # Silently "succeeding" here would publish a ranking over nothing.
    assert run_backfill(run_id="h1", store=tmp_store_sm3, client=fake_client_full,
                        lock=FakeLock(), include_historical=True,
                        as_of="2026-03-31") == 2
    assert fake_client_full.symbols_fetched == []


def test_historical_all_candidates_covered_is_success(tmp_store_hist_scenario,
                                                      fake_client_full, capsys):
    # Only MSFT qualifies as a candidate, and it already has its window:
    # nothing to collect is success, but the coverage number still ships.
    conn = sqlite3.connect(str(tmp_store_hist_scenario.db_path))
    try:
        conn.execute("DELETE FROM historical_market_cap WHERE symbol != 'MSFT'")
        conn.commit()
    finally:
        conn.close()
    rc = run_backfill(run_id="h1", store=tmp_store_hist_scenario,
                      client=fake_client_full, lock=FakeLock(),
                      include_historical=True, as_of="2026-03-31")
    assert rc == 0
    assert fake_client_full.symbols_fetched == []
    out = capsys.readouterr().out
    assert "candidate_coverage_pct" in out and "1/1" in out
    with pytest.raises(ValueError):
        tmp_store_hist_scenario.run_progress("h1")   # no manifest for an empty grid


def test_has_asof_window_boundary(tmp_store_hist_scenario):
    assert has_asof_window(tmp_store_hist_scenario, "MSFT", "2026-03-31") is True
    assert has_asof_window(tmp_store_hist_scenario, "AAPL", "2026-03-31") is False
    assert has_asof_window(tmp_store_hist_scenario, "OLDCO", "2026-03-31") is False


def test_has_asof_window_requires_all_three_tables(tmp_store_hist_scenario):
    # Drop MSFT's cash flow history: two-of-three is not a window.
    conn = sqlite3.connect(str(tmp_store_hist_scenario.db_path))
    try:
        conn.execute("DELETE FROM cash_flow_quarterly WHERE symbol = 'MSFT'")
        conn.commit()
    finally:
        conn.close()
    assert has_asof_window(tmp_store_hist_scenario, "MSFT", "2026-03-31") is False


def test_has_asof_window_rejects_oversized_quarter_gap(tmp_store_hist_scenario):
    # 8 in-window quarters, but one adjacent gap blows past
    # FUNDAMENTAL_QUARTER_GAP_MAX_DAYS — a hole in the middle of the window is
    # not a rankable 8Q history.
    gapped = ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30",
              "2025-06-30",                                  # ~9 months missing
              "2025-09-30", "2025-12-31", "2026-03-31"]
    _seed_statements(tmp_store_hist_scenario, "GAPCO", gapped)
    assert len(gapped) == 8
    assert has_asof_window(tmp_store_hist_scenario, "GAPCO", "2026-03-31") is False


def test_kernel_receives_full_utc_timestamp(tmp_store_sm3, fake_client_full,
                                            monkeypatch):
    # CONTROLLER RULING #11: a pure date normalizes to T00:00:00Z, so a
    # same-day re-collection with changed content would collide on the vintage
    # PK and surface as fetch_failed instead of appending.
    seen = []
    real = runner.collect_fundamentals_for_symbol

    def _spy(symbol, **kwargs):
        seen.append(kwargs["observed_at"])
        return real(symbol, **kwargs)

    monkeypatch.setattr(runner, "collect_fundamentals_for_symbol", _spy)
    run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                 lock=FakeLock())
    assert seen and len(set(seen)) == 1          # one stamp for the whole run
    datetime.strptime(seen[0], "%Y-%m-%dT%H:%M:%SZ")   # full timestamp, not a date


def test_historical_mode_requires_asof_and_deepens_limit(
        tmp_store_sm3_plus_historical, fake_client_full):
    with pytest.raises(SystemExit):      # --include-historical without --as-of
        parse_args(["--run-id", "h1", "--include-historical"])
    run_backfill(run_id="h1", store=tmp_store_sm3_plus_historical,
                 client=fake_client_full, lock=FakeLock(),
                 include_historical=True, as_of="2025-06-30")
    assert fake_client_full.last_limit >= 8 + 4     # >4 quarters back -> deepened


def test_historical_report_coverage_pct_over_all_candidates(
        tmp_store_hist_scenario, fake_client_full, capsys):
    # R5-P1-1: denominator = every as-of candidate (AAPL+MSFT+OLDCO = 3),
    # not just the symbols this run happened to fetch.
    run_backfill(run_id="h1", store=tmp_store_hist_scenario,
                 client=fake_client_full, lock=FakeLock(),
                 include_historical=True, as_of="2026-03-31")
    out = capsys.readouterr().out
    assert "candidate_coverage_pct" in out
    assert "3/3" in out or "denominator=3" in out


# ---------------------------------------------------------------------------
# Additional coverage: resume, dry-run, deepening bounds, status mapping
# ---------------------------------------------------------------------------

def test_resume_reuses_frozen_grid_and_skips_terminal(tmp_store_sm3, fake_client_full):
    assert run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                        lock=FakeLock()) == 0

    # Universe grows after the freeze; --resume must stay on the frozen grid.
    tmp_store_sm3.upsert_security_master([_sm_row("DDD")])
    tmp_store_sm3.record_membership_snapshot(["AAA", "BBB", "CCC", "DDD"],
                                             as_of="2026-02-02")
    second_client = FakeClient()
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=second_client,
                      lock=FakeLock(), resume=True)
    assert rc == 0
    assert second_client.symbols_fetched == []          # every job already terminal
    prog = tmp_store_sm3.run_progress("r1")
    assert prog["total_symbols"] == 3                   # DDD never joined the run
    assert prog["done"] == 3 * len(DATASETS)


def test_resume_recovers_stale_in_progress_jobs(tmp_store_sm3, fake_client_full):
    # A SIGKILLed predecessor leaves jobs claimed; resume must reclaim them.
    tmp_store_sm3.create_backfill_run("r1", ["AAA", "BBB", "CCC"], list(DATASETS), {})
    tmp_store_sm3.claim_pending_jobs("r1", "AAA")
    assert tmp_store_sm3.run_progress("r1")["in_progress"] == len(DATASETS)

    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                      lock=FakeLock(), resume=True)
    assert rc == 0
    assert "AAA" in fake_client_full.symbols_fetched
    assert tmp_store_sm3.run_progress("r1")["done"] == 3 * len(DATASETS)


def test_dry_run_touches_nothing(tmp_store_sm3, fake_client_full, capsys):
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                      lock=FakeLock(), dry_run=True)
    assert rc == 0
    assert fake_client_full.symbols_fetched == []
    with pytest.raises(ValueError):
        tmp_store_sm3.run_progress("r1")
    assert "AAA" in capsys.readouterr().out


def test_provider_empty_maps_to_terminal_job(tmp_store_sm3):
    client = FakeClient(status="provider_empty")
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=client, lock=FakeLock())
    prog = tmp_store_sm3.run_progress("r1")
    assert rc == 0
    assert prog["provider_empty"] == 3 * len(DATASETS)
    assert prog["is_complete"] is True


def test_profiles_mirror_refreshed_once_at_the_end(tmp_store_sm3, fake_client_full,
                                                   tmp_path):
    mirror = tmp_path / "profiles.json"
    rc = run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                      lock=FakeLock(), profiles_mirror_path=mirror)
    assert rc == 0
    payload = json.loads(mirror.read_text(encoding="utf-8"))
    assert sorted(k for k in payload if k != "_meta") == ["AAA", "BBB", "CCC"]


def test_profiles_mirror_untouched_when_no_path_given(tmp_store_sm3, fake_client_full,
                                                      tmp_path, monkeypatch):
    # Default must have zero filesystem side effects — tests and dry runs must
    # never write into the real data/fundamental/profiles.json.
    called = []
    monkeypatch.setattr("scripts.backfill_extended_fundamentals.rebuild_profiles_json",
                        lambda *a, **k: called.append(a))
    run_backfill(run_id="r1", store=tmp_store_sm3, client=fake_client_full,
                 lock=FakeLock())
    assert called == []


def test_file_lock_is_exclusive_and_releasable(tmp_path):
    # The real fcntl lock: non-blocking, so a busy peer gets False rather than
    # a hung cron job, and a released lock is immediately re-acquirable.
    path = tmp_path / "resource-market_db_writer.lock"
    first, second = FileLock(path), FileLock(path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_lock_path_matches_cron_wrapper_convention():
    # Same file cron_wrapper.sh derives from FINANCE_CRON_RESOURCE_KEY
    # (`$LOCK_DIR/resource-<key>.lock`) — a mismatch means no mutual exclusion.
    assert LOCK_PATH.name == "resource-market_db_writer.lock"
    assert LOCK_PATH.parent.name == "finance-cron-locks"


def test_parse_args_defaults_and_validation():
    args = parse_args(["--run-id", "r1"])
    assert args.canary is None and args.limit_quarters == 8
    assert args.resume is False and args.no_lock is False
    hist = parse_args(["--run-id", "h1", "--include-historical", "--as-of", "2026-03-31"])
    assert hist.include_historical and hist.as_of == "2026-03-31"
    with pytest.raises(SystemExit):
        parse_args(["--run-id", "h1", "--as-of", "03/31/2026"])
    with pytest.raises(SystemExit):
        parse_args(["--run-id", "r1", "--canary", "0"])


def test_job_status_map_is_the_frozen_mapping():
    assert JOB_STATUS_MAP == {"ok": "done", "provider_empty": "provider_empty",
                              "fetch_failed": "fetch_failed"}


def test_deepen_limit_quarters_bounds():
    today = date(2026, 8, 19)
    assert deepen_limit_quarters(8, "2026-08-19", today=today) == 8      # as-of today
    assert deepen_limit_quarters(8, "2025-06-30", today=today) == 8 + 5  # 415 days
    # A decade back saturates at the cap; an explicit deeper request wins.
    assert deepen_limit_quarters(8, "2016-01-01", today=today) == HISTORICAL_LIMIT_QUARTERS_CAP
    assert deepen_limit_quarters(20, "2026-05-01", today=today) == 20


def test_historical_run_deepens_current_member_without_losing_recent_quarters(
        tmp_store_hist_scenario, fake_client_full):
    before = _table_dates(tmp_store_hist_scenario, "income_quarterly", "AAPL")
    run_backfill(run_id="h1", store=tmp_store_hist_scenario, client=fake_client_full,
                 lock=FakeLock(), include_historical=True, as_of="2026-03-31")
    after = _table_dates(tmp_store_hist_scenario, "income_quarterly", "AAPL")
    assert set(before).issubset(set(after))     # idempotent upsert, no truncation
