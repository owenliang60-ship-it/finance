"""Tests for scripts/reconcile_fundamentals.py (T12, R6/R10).

Weekly coverage-reconciliation CLI: audit the current base universe's
fundamentals coverage (read-only), freeze a bounded repair-target list, and
(only with --repair) drive the shared T8 kernel over exactly that frozen
list — never the full pool. Phase 0 (identity queue) is also --repair-gated:
report-only must make ZERO provider calls.

No network: everything goes through `_FakeClient` (stand-in for
`FMPClient.get_dataset_with_status`, T4 interface) and a temp-file
MarketStore. No real filesystem/lock side effects: `run_reconcile` defaults
`lock` to a no-op and `profiles_mirror_path` to None so a test never touches
the shared `/tmp/finance-cron-locks/...` lock file or writes into the real
repo's data/ directory (mirrors T10's `run_backfill` convention).
"""
import json

import pytest

from scripts.reconcile_fundamentals import _run_identity_phase, parse_args, run_reconcile
from src.data.market_store import MarketStore

AS_OF = "2026-08-24"

STATEMENT_TABLES = ("income_quarterly", "balance_sheet_quarterly", "cash_flow_quarterly")


# ---------------------------------------------------------------------------
# Seeding helpers (direct SQL / store methods — full control, no network)
# ---------------------------------------------------------------------------

def _sm_row(symbol, eligible=1, reason="ok"):
    return {
        "symbol": symbol, "cik": "CIK-" + symbol, "company_name": symbol + " Inc.",
        "exchange": "NASDAQ", "is_etf": 0, "is_fund": 0, "is_adr": 0,
        "share_class_of": None, "eligible": eligible, "reason": reason,
        "updated_at": "2026-08-01T00:00:00Z",
    }


def _blocked_sm_row(symbol, reason="missing_profile"):
    """Matches the frozen Identity 状态契约's `missing_profile` shape exactly
    (bootstrap_security_master.py docstring): cik/company_name/exchange all
    None on a 200+empty profile outcome."""
    return {
        "symbol": symbol, "cik": None, "company_name": None, "exchange": None,
        "is_etf": 0, "is_fund": 0, "is_adr": 0, "share_class_of": None,
        "eligible": 0, "reason": reason, "updated_at": "2026-08-01T00:00:00Z",
    }


def _seed_statements(store, symbol, fiscal_dates):
    rows = [{"date": d, "symbol": symbol, "period": "Q", "revenue": 1.0} for d in fiscal_dates]
    store.upsert_income(symbol, rows)
    store.upsert_balance_sheet(symbol, rows)
    store.upsert_cash_flow(symbol, rows)


def _seed_profile(store, symbol, updated_at="2026-08-01T00:00:00Z"):
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO company_profile (symbol, payload, updated_at) VALUES (?, ?, ?)",
        (symbol, json.dumps({"symbol": symbol}), updated_at),
    )
    conn.commit()


def _seed_coverage(store, symbol, dataset, status, next_retry_at=None,
                   updated_at="2026-08-01T00:00:00Z"):
    """Direct INSERT so tests can pin an exact `next_retry_at`, independent of
    `upsert_coverage_status`'s real-wall-clock-derived backoff/TTL math."""
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO coverage_status "
        "(symbol, dataset, status, detail, updated_at, last_attempt_at, "
        "last_success_at, consecutive_failures, next_retry_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, NULL, 0, ?)",
        (symbol, dataset, status, updated_at, updated_at, next_retry_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Store fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


def _build_cov_store(store, nocfco_next_retry):
    """STALECO / NOCFCO / OKCO — three eligible, currently-active members:

      STALECO — all four datasets present but the latest filed quarter is
                130 days before AS_OF (> default 120d stale window).
      NOCFCO  — income/balance/profile healthy + fresh; cash_flow_quarterly
                alone is provider_empty, gated on `nocfco_next_retry`.
      OKCO    — fully healthy across all four datasets; must never appear
                in any repair-target list.
    """
    symbols = ["STALECO", "NOCFCO", "OKCO"]
    store.upsert_security_master([_sm_row(s) for s in symbols])
    store.record_membership_snapshot(symbols, as_of="2026-01-02")

    stale_dates = ["2025-07-31", "2025-10-31", "2026-01-31", "2026-04-16"]
    _seed_statements(store, "STALECO", stale_dates)
    _seed_profile(store, "STALECO")
    for table in STATEMENT_TABLES + ("company_profile",):
        _seed_coverage(store, "STALECO", table, "ok")

    fresh_dates = ["2025-11-30", "2026-02-28", "2026-05-31", "2026-07-31"]
    store.upsert_income("NOCFCO", [{"date": d, "symbol": "NOCFCO", "period": "Q"}
                                   for d in fresh_dates])
    store.upsert_balance_sheet("NOCFCO", [{"date": d, "symbol": "NOCFCO", "period": "Q"}
                                          for d in fresh_dates])
    _seed_profile(store, "NOCFCO")
    _seed_coverage(store, "NOCFCO", "income_quarterly", "ok")
    _seed_coverage(store, "NOCFCO", "balance_sheet_quarterly", "ok")
    _seed_coverage(store, "NOCFCO", "company_profile", "ok")
    _seed_coverage(store, "NOCFCO", "cash_flow_quarterly", "provider_empty",
                   next_retry_at=nocfco_next_retry)

    _seed_statements(store, "OKCO", fresh_dates)
    _seed_profile(store, "OKCO")
    for table in STATEMENT_TABLES + ("company_profile",):
        _seed_coverage(store, "OKCO", table, "ok")

    return store


@pytest.fixture
def tmp_store_cov(tmp_store):
    """NOCFCO's provider_empty TTL expires well after AS_OF — not retryable."""
    return _build_cov_store(tmp_store, nocfco_next_retry="2026-09-15T00:00:00Z")


@pytest.fixture
def tmp_store_cov_ttl_expired(tmp_store):
    """Same scenario, but NOCFCO's TTL has already expired by AS_OF."""
    return _build_cov_store(tmp_store, nocfco_next_retry="2026-08-01T00:00:00Z")


@pytest.fixture
def tmp_store_cov_many(tmp_store):
    """Five eligible, currently-active, never-collected symbols — all land
    in `missing`, comfortably exceeding a --max-targets=2 cap."""
    symbols = ["MISS1", "MISS2", "MISS3", "MISS4", "MISS5"]
    tmp_store.upsert_security_master([_sm_row(s) for s in symbols])
    tmp_store.record_membership_snapshot(symbols, as_of="2026-01-02")
    return tmp_store


@pytest.fixture
def tmp_store_idq(tmp_store):
    """NEWCO: SM-blocked (`missing_profile`, eligible=0) from an earlier
    provider_empty identity probe whose TTL has since expired — queued for
    phase 0's incremental identity re-probe, ACTIVE in extended_membership
    despite being currently ineligible (R2-P1-2: not limited to eligible)."""
    tmp_store.upsert_security_master([_blocked_sm_row("NEWCO")])
    tmp_store.record_membership_snapshot(["NEWCO"], as_of="2026-01-02")
    _seed_coverage(tmp_store, "NEWCO", "identity", "provider_empty",
                   next_retry_at="2026-08-10T00:00:00Z")
    return tmp_store


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _rows_for(kind, symbol):
    if kind == "profile":
        return [{"symbol": symbol, "cik": "CIK-" + symbol,
                 "companyName": symbol + " Inc.", "exchangeShortName": "NASDAQ",
                 "isEtf": False, "isFund": False, "isAdr": False, "mktCap": 1000}]
    if kind == "ratios":
        return [{"date": "2025-12-31", "symbol": symbol, "period": "FY",
                 "grossProfitMargin": 0.5}]
    return [{"date": "2026-06-30", "symbol": symbol, "period": "Q2",
             "revenue": 1.0, "filingDate": "2026-07-30"}]


class _FakeClient:
    """Stands in for FMPClient.get_dataset_with_status (T4 interface).

    `.calls` is an INT counter (not a list) so `client.calls == 0` is a
    meaningful "zero API calls" assertion for report-only mode.
    """

    def __init__(self, default_status="ok"):
        self.default_status = default_status
        self.calls = 0
        self._symbols_fetched = []
        self.profile_symbols_fetched = []

    def get_dataset_with_status(self, kind, symbol, limit=None):
        self.calls += 1
        if symbol not in self._symbols_fetched:
            self._symbols_fetched.append(symbol)
        if kind == "profile" and symbol not in self.profile_symbols_fetched:
            self.profile_symbols_fetched.append(symbol)
        if self.default_status != "ok":
            return [], self.default_status
        return _rows_for(kind, symbol), "ok"

    @property
    def symbols_fetched(self):
        return sorted(self._symbols_fetched)


@pytest.fixture
def fake_client_full():
    return _FakeClient()


class FakeLock:
    """Stands in for the fcntl flock wrapper: `acquire() -> bool` + `release()`
    (same shape as backfill_extended_fundamentals.FakeLock / T10's test double).
    """

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


@pytest.fixture
def fake_lock():
    return FakeLock()


class _SpyNotifier:
    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(message)

    @property
    def last_message(self):
        return self.messages[-1] if self.messages else None


@pytest.fixture
def spy_notifier():
    return _SpyNotifier()


# ---------------------------------------------------------------------------
# Brief Step 1 — mandatory tests
# ---------------------------------------------------------------------------

def test_report_only_freezes_targets_no_fetch(tmp_store_cov, fake_client_full, spy_notifier):
    rc, targets = run_reconcile(store=tmp_store_cov, client=fake_client_full,
                                repair=False, notifier=spy_notifier, as_of=AS_OF)
    assert rc == 0 and fake_client_full.calls == 0          # only reads, zero API
    assert "STALECO" in targets                              # 130d-old latest quarter -> stale
    status = tmp_store_cov._get_conn().execute(
        "SELECT status FROM coverage_status "
        "WHERE symbol = 'STALECO' AND dataset = 'income_quarterly'"
    ).fetchone()[0]
    assert status == "ok"                                  # report-only means zero DB writes


def test_repair_touches_only_frozen_targets(tmp_store_cov, fake_client_full, spy_notifier,
                                            fake_lock):
    # Ruling #13: lock is mandatory keyword-only under repair=True (test adjustment
    # controller-authorized — the brief's original test text predates the self-lock
    # ruling; T10's run_backfill sets the precedent of no silent default).
    rc, targets = run_reconcile(store=tmp_store_cov, client=fake_client_full,
                                repair=True, max_targets=1, notifier=spy_notifier,
                                as_of=AS_OF, lock=fake_lock)
    assert fake_client_full.symbols_fetched == sorted(targets)[:1]   # truncation holds, no full pool


def test_max_targets_cap_and_notice(tmp_store_cov_many, fake_client_full, spy_notifier, fake_lock):
    run_reconcile(store=tmp_store_cov_many, client=fake_client_full,
                 repair=True, max_targets=2, notifier=spy_notifier, as_of=AS_OF, lock=fake_lock)
    assert "truncated" in spy_notifier.last_message


def test_argparse_contract():
    args = parse_args(["--repair", "--max-targets", "50"])
    assert args.repair is True and args.max_targets == 50
    assert parse_args([]).repair is False                    # default report-only


def test_provider_empty_within_ttl_not_in_targets(tmp_store_cov, fake_client_full, spy_notifier):
    # NOCFCO's provider_empty next_retry_at is in the future -> don't re-burn quota
    _, targets = run_reconcile(store=tmp_store_cov, client=fake_client_full,
                               repair=False, notifier=spy_notifier, as_of=AS_OF)
    assert "NOCFCO" not in targets


def test_provider_empty_ttl_expired_is_retryable(tmp_store_cov_ttl_expired, fake_client_full,
                                                 spy_notifier):
    # R2-P1-3: TTL expired (next_retry_at <= as_of) -> re-probe (new listing / vendor backfill)
    _, targets = run_reconcile(store=tmp_store_cov_ttl_expired, client=fake_client_full,
                               repair=False, notifier=spy_notifier, as_of=AS_OF)
    assert "NOCFCO" in targets


def test_identity_queue_reprobes_missing_profile_beyond_eligible(tmp_store_idq, fake_client_full,
                                                                  spy_notifier, fake_lock):
    # R2-P1-2: NEWCO is SM missing_profile (blocked) -> phase 0 re-probes it
    # regardless of current eligibility, not skipped for being non-eligible.
    run_reconcile(store=tmp_store_idq, client=fake_client_full,
                 repair=True, notifier=spy_notifier, as_of=AS_OF, lock=fake_lock)
    assert "NEWCO" in fake_client_full.profile_symbols_fetched
    assert tmp_store_idq.get_security_eligibility().get("NEWCO") is True   # upgraded


def test_identity_retry_settles_against_existing_same_cik_primary(tmp_store):
    """A recovered profile is not a singleton when SM already has its issuer.

    OLD is the established larger primary. NEW's retry must be classified as
    its secondary share class, never become a second eligible primary.
    """
    tmp_store.upsert_security_master([{
        "symbol": "OLD", "cik": "CIK-SAME", "company_name": "Same Co",
        "exchange": "NASDAQ", "is_etf": 0, "is_fund": 0, "is_adr": 0,
        "share_class_of": None, "eligible": 1, "reason": "ok",
        "updated_at": "2026-08-01T00:00:00Z",
    }])
    with tmp_store.transaction() as conn:
        tmp_store.write_company_profile_in_conn(
            conn, "OLD", {"symbol": "OLD", "cik": "CIK-SAME",
                           "companyName": "Same Co", "mktCap": 100},
            "2026-08-01T00:00:00Z")
    _seed_coverage(tmp_store, "NEW", "identity", "provider_empty",
                   next_retry_at="2026-08-01T00:00:00Z")

    class SameIssuerClient(_FakeClient):
        def get_dataset_with_status(self, kind, symbol, limit=None):
            self.calls += 1
            self.profile_symbols_fetched.append(symbol)
            return [{"symbol": symbol, "cik": "CIK-SAME",
                     "companyName": "Same Co", "exchangeShortName": "NASDAQ",
                     "mktCap": 90}], "ok"

    report = _run_identity_phase(
        tmp_store, SameIssuerClient(), "2026-08-24T00:00:00Z", {})
    rows = tmp_store._get_conn().execute(
        "SELECT symbol, eligible, reason, share_class_of FROM security_master "
        "WHERE cik = 'CIK-SAME' ORDER BY symbol"
    ).fetchall()

    assert report["upgraded"] == []
    assert [tuple(row) for row in rows] == [
        ("NEW", 0, "secondary_share_class", "OLD"),
        ("OLD", 1, "ok", None),
    ]


def test_repair_without_lock_raises(tmp_store_cov, fake_client_full, spy_notifier):
    # Ruling #13: lock is mandatory keyword-only under repair=True — mirrors T10's
    # run_backfill, which has no silent default either. A caller that forgets
    # lock= must fail loud, not write against market.db unlocked.
    with pytest.raises(ValueError):
        run_reconcile(store=tmp_store_cov, client=fake_client_full,
                      repair=True, notifier=spy_notifier, as_of=AS_OF)
