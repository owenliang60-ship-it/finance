"""Tests for src/data/extended_universe_manager.py."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.extended_universe_manager import (
    get_extended_only_symbols,
    get_extended_symbols,
    refresh_extended_universe,
    refresh_with_snapshot,
)


def test_weekly_refresh_holds_market_writer_lock_for_extended_membership_write(tmp_path):
    """The weekly manager writes market.db, so its cron step must share the
    same writer lock as every other market.db producer."""
    project = tmp_path / "project"
    fake_bin = tmp_path / "bin"
    lock_dir = tmp_path / "locks"
    calls = tmp_path / "python-calls.log"
    project.mkdir()
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text('#!/bin/sh\necho "$*" >> "$CALL_LOG"\nexit 0\n')
    fake_python.chmod(0o755)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text("#!/bin/sh\nexit 1\n")
    fake_flock.chmod(0o755)

    env = os.environ.copy()
    env.update({
        "PATH": str(fake_bin) + os.pathsep + env["PATH"],
        "FINANCE_PROJECT_DIR": str(project),
        "FINANCE_CRON_LOCK_DIR": str(lock_dir),
        "CALL_LOG": str(calls),
    })
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "broad_universe_cron_wrapper.sh"),
         "weekly_refresh"],
        env=env, capture_output=True, text=True,
    )

    assert result.returncode == 75
    assert "extended_universe_manager --refresh" not in calls.read_text()
    log = next((project / "logs").glob("cron_broad_weekly_refresh_*.log")).read_text()
    assert "market_db_writer lock busy" in log


class _KnowsEverything(dict):
    """security_master stand-in that already knows every screener symbol.

    T17 routed `refresh_extended_universe` through the DB commit flow; the
    screener/floor/cache tests below predate that and assert only on the
    cache file. Reporting every symbol as known+eligible makes the entrant
    loop empty, so those tests keep exercising exactly what they always did
    (no DB, no profile client). The DB semantics get their own coverage in
    `TestRefreshWithSnapshot`.
    """

    def __contains__(self, key):
        return True

    def get(self, key, default=None):
        return True


class _StubStore:
    def __init__(self):
        self.snapshots = []

    def get_security_eligibility(self):
        return _KnowsEverything()

    def record_membership_snapshot(self, symbols, as_of):
        self.snapshots.append((list(symbols), as_of))
        return {"entered": list(symbols), "exited": []}


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Redirect EXTENDED_UNIVERSE_FILE to tmp_path (+ stub the SSOT store)."""
    cache_file = tmp_path / "extended_universe.json"
    monkeypatch.setattr(
        "src.data.extended_universe_manager.EXTENDED_UNIVERSE_FILE",
        cache_file,
    )
    monkeypatch.setattr(
        "src.data.extended_universe_manager._resolve_store",
        lambda: _StubStore(),
    )
    return cache_file


class TestRefreshExtendedUniverse:
    def test_writes_cache_with_correct_format(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"symbol": "NVDA"}, {"symbol": "XOM"},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_mcap_b=10, min_count_floor=0)

        assert symbols == ["AAPL", "NVDA", "XOM"]
        assert tmp_cache.exists()

        data = json.loads(tmp_cache.read_text())
        assert data["count"] == 3
        assert data["min_mcap_b"] == 10
        assert data["symbols"] == ["AAPL", "NVDA", "XOM"]
        assert "updated" in data

    def test_deduplicates_symbols(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"symbol": "AAPL"}, {"symbol": "NVDA"},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_count_floor=0)

        assert symbols == ["AAPL", "NVDA"]

    def test_skips_entries_without_symbol(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"name": "NoSymbol"}, {"symbol": ""},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_count_floor=0)

        assert symbols == ["AAPL"]


class TestGetExtendedSymbols:
    def test_returns_empty_when_no_cache(self, tmp_cache):
        assert get_extended_symbols() == []

    def test_returns_symbols_from_cache(self, tmp_cache):
        tmp_cache.write_text(json.dumps({
            "updated": "2026-03-28",
            "count": 2,
            "symbols": ["AAPL", "NVDA"],
        }))
        assert get_extended_symbols() == ["AAPL", "NVDA"]


class TestGetExtendedOnlySymbols:
    """Deprecated forwarder (matrix #8). The extended−core formula it used to
    own now lives in the yfinance line's pre-bootstrap fallback, so these cases
    pin the pre-bootstrap window explicitly instead of relying on whatever
    `extended_membership` happens to hold."""

    @staticmethod
    def _pre_bootstrap():
        return patch(
            "src.data.universe_resolver.current_base_universe",
            side_effect=RuntimeError("extended_membership empty — run bootstrap first"),
        )

    def test_excludes_pool_symbols(self, tmp_cache):
        tmp_cache.write_text(json.dumps({
            "updated": "2026-03-28",
            "count": 3,
            "symbols": ["AAPL", "NVDA", "XOM"],
        }))

        with self._pre_bootstrap(), patch(
            "src.data.pool_manager.get_symbols",
            return_value=["AAPL", "NVDA"],
        ):
            result = get_extended_only_symbols()

        assert result == ["XOM"]

    def test_returns_all_when_pool_empty(self, tmp_cache):
        tmp_cache.write_text(json.dumps({
            "updated": "2026-03-28",
            "count": 2,
            "symbols": ["XOM", "CVX"],
        }))

        with self._pre_bootstrap(), patch(
            "src.data.pool_manager.get_symbols",
            return_value=[],
        ):
            result = get_extended_only_symbols()

        assert result == ["CVX", "XOM"]


class TestRefreshFloorGuard:
    """P0 floor guard: FMP empty/partial returns must NOT overwrite cache.

    Background: Boss review v1 -> v2 caught fmp_client.get_large_cap_stocks()
    silent-fail mode (returns [] on API error). Without guard,
    refresh_extended_universe() would write count:0 / symbols:[] and exit 0,
    corrupting the production cache. Guard raises RuntimeError before
    _write_cache() and preserves whatever was on disk.
    """

    @pytest.fixture
    def populated_cache(self, tmp_cache):
        """Pre-populate cache with 548 fake symbols (= 5/9 actual count)."""
        tmp_cache.write_text(json.dumps({
            "updated": "2026-04-25",
            "min_mcap_b": 10,
            "count": 548,
            "symbols": [f"SYM{i}" for i in range(548)],
        }))
        return tmp_cache

    def test_aborts_when_fmp_returns_empty(self, populated_cache):
        """FMP API silent failure (returns []) -> raise + old cache intact."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = []

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="below floor"):
                refresh_extended_universe()

        data = json.loads(populated_cache.read_text())
        assert data["updated"] == "2026-04-25"
        assert data["count"] == 548

    def test_aborts_when_fmp_returns_partial(self, populated_cache):
        """FMP API jitter (returns 100 < floor 400) -> raise + old cache intact."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"SYM{i}"} for i in range(100)
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="below floor"):
                refresh_extended_universe()

        data = json.loads(populated_cache.read_text())
        assert data["count"] == 548

    def test_writes_when_above_floor(self, populated_cache):
        """FMP normal return (900 >= floor 800) -> writes new cache."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"NEW{i}"} for i in range(900)
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe()

        assert len(symbols) == 900
        data = json.loads(populated_cache.read_text())
        assert data["count"] == 900
        assert data["symbols"][0] == "NEW0"
        assert data["updated"] != "2026-04-25"

    def test_respects_explicit_floor_override(self, populated_cache):
        """Explicit min_count_floor=50 allows tiny refresh through (test/dev path)."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"TINY{i}"} for i in range(60)
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_count_floor=50)

        assert len(symbols) == 60
        data = json.loads(populated_cache.read_text())
        assert data["count"] == 60

    def test_aborts_when_below_new_floor_800(self, populated_cache):
        """A1: MIN_COUNT_FLOOR raised from 400 to 800 to catch screener regression."""
        from src.data.extended_universe_manager import MIN_COUNT_FLOOR
        assert MIN_COUNT_FLOOR == 800, "A1 floor: ~84% of post-A1 ~949 baseline"

        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"S{i}"} for i in range(700)
        ]
        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="below floor 800"):
                refresh_extended_universe()

        # populated_cache fixture wrote a cache; mtime/contents untouched
        data = json.loads(populated_cache.read_text())
        assert data["count"] == 548
        assert data["updated"] == "2026-04-25"

    def test_floor_guard_does_not_block_when_5000_returned(self, tmp_cache, caplog):
        """Manager-layer test: floor_guard MUST not block when 5000 rows arrive.

        Scope: this test only covers the refresh_extended_universe() manager
        layer behavior — it patches FMPClient itself, so the fmp_client
        sentinel never executes. (For sentinel-level coverage see
        test_fmp_client_mcap.py::test_get_large_cap_stocks_warns_on_exact_limit_match.)

        Asserts: when get_large_cap_stocks returns exactly 5000 symbols, the
        manager still writes the cache (floor guard is a hard raise, sentinel
        is a soft warning — they must not block each other).
        """
        import logging
        mock_client = MagicMock()
        # FMP-style payload: 5000 rows, all valid symbols
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"S{i:04d}"} for i in range(5000)
        ]
        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            with caplog.at_level(logging.WARNING):
                symbols = refresh_extended_universe(min_count_floor=0)

        assert len(symbols) == 5000, "cache must write — manager floor guard != fmp sentinel"
        cache_path_data = json.loads(tmp_cache.read_text())
        assert cache_path_data["count"] == 5000


# ---------------------------------------------------------------------------
# T17: 周频 membership 快照接线 (R2-P1-2 / R3-P1-4 / R4-P1-1)
#
# DB membership is the SSOT commit point; extended_universe.json is a
# rebuildable cache published only AFTER that commit succeeds.
# ---------------------------------------------------------------------------

AS_OF = "2026-08-22"


class _FakeProfileClient:
    """Stand-in for FMPClient.get_dataset_with_status("profile", sym)."""

    def __init__(self, fetch_failed=()):
        self.fetch_failed = set(fetch_failed)
        self.symbols_fetched = []

    def get_dataset_with_status(self, kind, symbol, limit=8):
        assert kind == "profile"
        self.symbols_fetched.append(symbol)
        if symbol in self.fetch_failed:
            return [], "fetch_failed"
        return [{
            "symbol": symbol, "cik": "CIK-" + symbol,
            "companyName": symbol + " Inc.", "exchangeShortName": "NASDAQ",
            "isEtf": False, "isFund": False, "isAdr": False, "mktCap": 1000,
        }], "ok"


@pytest.fixture
def tmp_store(tmp_path):
    from src.data.market_store import MarketStore
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


@pytest.fixture
def fake_client_full():
    return _FakeProfileClient()


@pytest.fixture
def fake_client_newco_500():
    return _FakeProfileClient(fetch_failed=["NEWCO"])


def _bootstrap_minimal_sm(store, symbols):
    """Seed security_master as a prior T6 bootstrap would have left it."""
    store.upsert_security_master([{
        "symbol": s, "cik": "CIK-" + s, "company_name": s + " Inc.",
        "exchange": "NASDAQ", "is_etf": 0, "is_fund": 0, "is_adr": 0,
        "share_class_of": None, "eligible": 1, "reason": "ok",
        "updated_at": "2026-08-01T00:00:00Z",
    } for s in symbols])


def _write_old_cache(cache_dir, symbols):
    path = Path(cache_dir) / "extended_universe.json"
    path.write_text(json.dumps({
        "updated": "2026-08-15", "min_mcap_b": 10,
        "count": len(symbols), "symbols": list(symbols),
    }), encoding="utf-8")
    return path


def _read_cache_symbols(cache_dir):
    path = Path(cache_dir) / "extended_universe.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["symbols"]


def _table_count(store, table):
    import sqlite3
    conn = sqlite3.connect(str(store.db_path))
    try:
        return conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
    finally:
        conn.close()


class TestRefreshWithSnapshot:
    def test_entrants_bootstrapped_before_membership(
        self, tmp_store, fake_client_full, tmp_path
    ):
        _bootstrap_minimal_sm(tmp_store, ["AAPL"])

        refresh_with_snapshot(["AAPL", "NEWCO"], store=tmp_store,
                              client=fake_client_full, cache_dir=tmp_path,
                              min_count_floor=0, as_of=AS_OF)

        # identity resolved first...
        assert tmp_store.get_security_eligibility().get("NEWCO") is True
        # ...only then can it be in the membership snapshot
        assert "NEWCO" in tmp_store.get_members_as_of(AS_OF)
        # already-mastered symbols are not re-fetched (weekly call budget)
        assert fake_client_full.symbols_fetched == ["NEWCO"]

    def test_failed_entrant_queued_not_membered(
        self, tmp_store, fake_client_newco_500, tmp_path
    ):
        _bootstrap_minimal_sm(tmp_store, ["AAPL"])

        refresh_with_snapshot(["AAPL", "NEWCO"], store=tmp_store,
                              client=fake_client_newco_500, cache_dir=tmp_path,
                              min_count_floor=0, as_of=AS_OF)

        members = tmp_store.get_members_as_of(AS_OF)
        assert "NEWCO" not in members
        assert members == ["AAPL"]
        # in the identity repair queue (T12 phase 0 retries it), not in SM
        assert tmp_store.get_coverage("identity").get("NEWCO") == "fetch_failed"
        assert "NEWCO" not in tmp_store.get_security_eligibility()
        # cache is the RAW screener list — membership is the eligible subset
        assert _read_cache_symbols(tmp_path) == ["AAPL", "NEWCO"]

    def test_json_not_rebuilt_when_db_hooks_fail(
        self, tmp_store, fake_client_full, monkeypatch, tmp_path
    ):
        """R3-P1-4: membership commit fails -> JSON bytes unchanged, no tmp residue."""
        _bootstrap_minimal_sm(tmp_store, ["AAPL"])
        _write_old_cache(tmp_path, ["AAPL"])

        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(tmp_store, "record_membership_snapshot", _boom)

        with pytest.raises(RuntimeError, match="db down"):
            refresh_with_snapshot(["AAPL", "NEWCO"], store=tmp_store,
                                  client=fake_client_full, cache_dir=tmp_path,
                                  min_count_floor=0, as_of=AS_OF)

        assert _read_cache_symbols(tmp_path) == ["AAPL"]
        assert not (tmp_path / "extended_universe.json.tmp").exists()
        assert _table_count(tmp_store, "extended_membership") == 0

    def test_membership_committed_even_if_json_publish_fails(
        self, tmp_store, fake_client_full, monkeypatch, tmp_path, caplog
    ):
        """R4-P1-1: DB commit lands, os.replace fails -> warn only, never raise."""
        import logging

        _bootstrap_minimal_sm(tmp_store, ["AAPL"])
        _write_old_cache(tmp_path, ["AAPL"])

        def _disk_full(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", _disk_full)

        with caplog.at_level(logging.WARNING):
            result = refresh_with_snapshot(["AAPL", "NEWCO"], store=tmp_store,
                                           client=fake_client_full, cache_dir=tmp_path,
                                           min_count_floor=0, as_of=AS_OF)

        assert "NEWCO" in tmp_store.get_members_as_of(AS_OF)   # DB is the truth
        assert _read_cache_symbols(tmp_path) == ["AAPL"]       # stale but harmless
        assert result["cache_published"] is False
        assert any("cache" in r.message.lower() for r in caplog.records)
        assert not (tmp_path / "extended_universe.json.tmp").exists()

    def test_current_base_universe_reads_db_not_json(self, tmp_store, tmp_path):
        """SSOT assertion: when JSON and DB disagree, DB wins."""
        _bootstrap_minimal_sm(tmp_store, ["AAPL", "NEWCO"])
        tmp_store.record_membership_snapshot(["AAPL", "NEWCO"], as_of=AS_OF)
        _write_old_cache(tmp_path, ["AAPL"])

        from src.data.universe_resolver import current_base_universe
        assert set(current_base_universe(store=tmp_store)) == {"AAPL", "NEWCO"}

    def test_floor_failure_writes_nothing(self, tmp_store, fake_client_full, tmp_path):
        """Floor guard (T1) still fires ahead of every write, DB included."""
        with pytest.raises(RuntimeError, match="below floor"):
            refresh_with_snapshot(["ONLY1"], store=tmp_store, client=fake_client_full,
                                  cache_dir=tmp_path, min_count_floor=800, as_of=AS_OF)

        assert _table_count(tmp_store, "extended_membership") == 0
        assert _table_count(tmp_store, "security_master") == 0
        assert fake_client_full.symbols_fetched == []
        assert _read_cache_symbols(tmp_path) is None

    def test_dropouts_exit_membership(self, tmp_store, fake_client_full, tmp_path):
        """SCD-2: a name the screener no longer returns closes its window."""
        _bootstrap_minimal_sm(tmp_store, ["AAPL", "GONE"])
        tmp_store.record_membership_snapshot(["AAPL", "GONE"], as_of="2026-08-15")

        result = refresh_with_snapshot(["AAPL"], store=tmp_store,
                                       client=fake_client_full, cache_dir=tmp_path,
                                       min_count_floor=0, as_of=AS_OF)

        assert tmp_store.get_active_members() == ["AAPL"]
        assert result["membership"]["exited"] == ["GONE"]
        assert "GONE" in tmp_store.get_members_as_of("2026-08-15")   # history intact

    def test_ineligible_symbols_never_enter_membership(
        self, tmp_store, fake_client_full, tmp_path
    ):
        """An ETF/fund already blocked in SM stays out even though the screener
        keeps returning it (JSON cache keeps it, DB membership does not)."""
        _bootstrap_minimal_sm(tmp_store, ["AAPL"])
        tmp_store.upsert_security_master([{
            "symbol": "SOXX", "cik": "CIK-SOXX", "company_name": "iShares Semi",
            "exchange": "NASDAQ", "is_etf": 1, "is_fund": 0, "is_adr": 0,
            "share_class_of": None, "eligible": 0, "reason": "etf",
            "updated_at": "2026-08-01T00:00:00Z",
        }])

        refresh_with_snapshot(["AAPL", "SOXX"], store=tmp_store,
                              client=fake_client_full, cache_dir=tmp_path,
                              min_count_floor=0, as_of=AS_OF)

        assert tmp_store.get_active_members() == ["AAPL"]
        assert fake_client_full.symbols_fetched == []   # SOXX is known, not an entrant
        assert _read_cache_symbols(tmp_path) == ["AAPL", "SOXX"]

    def test_default_as_of_is_today(self, tmp_store, fake_client_full, tmp_path):
        from datetime import date

        _bootstrap_minimal_sm(tmp_store, ["AAPL"])
        result = refresh_with_snapshot(["AAPL"], store=tmp_store,
                                       client=fake_client_full, cache_dir=tmp_path,
                                       min_count_floor=0)

        today = date.today().isoformat()
        assert result["as_of"] == today
        assert tmp_store.get_members_as_of(today) == ["AAPL"]

    def test_zero_eligible_refuses_to_empty_membership(
        self, tmp_store, fake_client_full, tmp_path
    ):
        """SM says nothing is eligible -> that is a broken SM, not an empty
        universe; committing it would exit every member at once."""
        _bootstrap_minimal_sm(tmp_store, ["AAPL"])
        tmp_store.record_membership_snapshot(["AAPL"], as_of="2026-08-15")
        tmp_store.upsert_security_master([{
            "symbol": "AAPL", "cik": "CIK-AAPL", "company_name": "Apple Inc.",
            "exchange": "NASDAQ", "is_etf": 0, "is_fund": 0, "is_adr": 0,
            "share_class_of": None, "eligible": 0, "reason": "identity_conflict",
            "updated_at": "2026-08-18T00:00:00Z",
        }])

        with pytest.raises(RuntimeError, match="Refusing to empty"):
            refresh_with_snapshot(["AAPL"], store=tmp_store, client=fake_client_full,
                                  cache_dir=tmp_path, min_count_floor=0, as_of=AS_OF)

        assert tmp_store.get_active_members() == ["AAPL"]   # old window still open
        assert _read_cache_symbols(tmp_path) is None

    def test_empty_security_master_fails_loud_before_any_write(
        self, tmp_store, fake_client_full, tmp_path
    ):
        """No T6 bootstrap yet -> refuse (a weekly-only cold start would bake
        survivorship bias into SM); membership and cache both stay put."""
        with pytest.raises(RuntimeError, match="security_master empty"):
            refresh_with_snapshot(["AAPL"], store=tmp_store, client=fake_client_full,
                                  cache_dir=tmp_path, min_count_floor=0, as_of=AS_OF)

        assert _table_count(tmp_store, "extended_membership") == 0
        assert _read_cache_symbols(tmp_path) is None


class TestRefreshExtendedUniverseWiring:
    """The weekly cron entry (scripts/update_extended_prices.py) must get the
    whole flow, not just the cache write."""

    def test_refresh_delegates_to_snapshot_flow(self, tmp_store, tmp_path, monkeypatch):
        _bootstrap_minimal_sm(tmp_store, ["AAPL"])
        monkeypatch.setattr(
            "src.data.extended_universe_manager.EXTENDED_UNIVERSE_FILE",
            tmp_path / "extended_universe.json",
        )
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"symbol": "NEWCO"},
        ]
        mock_client.get_dataset_with_status.side_effect = (
            lambda kind, symbol, limit=8: _FakeProfileClient().get_dataset_with_status(
                kind, symbol, limit)
        )

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_count_floor=0, store=tmp_store,
                                                as_of=AS_OF)

        assert symbols == ["AAPL", "NEWCO"]
        assert tmp_store.get_members_as_of(AS_OF) == ["AAPL", "NEWCO"]
        assert _read_cache_symbols(tmp_path) == ["AAPL", "NEWCO"]
