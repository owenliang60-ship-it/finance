"""Tests for MarketStore universe storage tables + transaction boundary (T1)."""
import pytest, sqlite3

from src.data.market_store import MarketStore


@pytest.fixture
def tmp_store(tmp_path):
    """Create a fresh MarketStore backed by a temp DB."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


NEW_TABLES = ["security_master", "extended_membership", "coverage_status",
              "company_profile", "fundamental_vintage",
              "fundamental_backfill_runs", "fundamental_backfill_jobs"]

def test_new_tables_created(tmp_store):
    conn = sqlite3.connect(tmp_store.db_path)
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert set(NEW_TABLES) <= names

def test_transaction_rolls_back_multi_table_on_error(tmp_store):
    with pytest.raises(RuntimeError):
        with tmp_store.transaction() as conn:
            conn.execute("insert into coverage_status(symbol, dataset, status, detail, updated_at) "
                         "values ('A','income_quarterly','ok',NULL,'2026-08-20')")   # 具名列（表共 9 列）
            conn.execute("insert into company_profile(symbol, payload, updated_at) "
                         "values ('A','{}','2026-08-20')")
            raise RuntimeError("boom")
    conn = sqlite3.connect(tmp_store.db_path)
    assert conn.execute("select count(*) from coverage_status").fetchone()[0] == 0
    assert conn.execute("select count(*) from company_profile").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# T3: Store 方法（SM 落库 / membership 双接口 / coverage）
# ---------------------------------------------------------------------------

def _sm_row(symbol="AAPL", cik="0000320193", company_name="Apple Inc.",
            exchange="NASDAQ", is_etf=0, is_fund=0, is_adr=0,
            share_class_of=None, eligible=1, reason="ok",
            updated_at="2026-08-16"):
    """完整字段 dict 工厂，沿用 T2 风格：每次调用产出 security_master 全部列。"""
    return dict(symbol=symbol, cik=cik, company_name=company_name,
                exchange=exchange, is_etf=is_etf, is_fund=is_fund, is_adr=is_adr,
                share_class_of=share_class_of, eligible=eligible, reason=reason,
                updated_at=updated_at)


def _seed_hmcap(store, symbol, date, market_cap):
    """直插 historical_market_cap（生产列名 symbol/date/market_cap，见 T1 DDL）。"""
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO historical_market_cap (symbol, date, market_cap) "
        "VALUES (?, ?, ?)",
        (symbol, date, market_cap),
    )
    conn.commit()


def test_eligibility_fail_loud_on_empty_sm(tmp_store):
    with pytest.raises(RuntimeError, match="bootstrap"):
        tmp_store.get_security_eligibility()

def test_membership_scd2_enter_exit(tmp_store):
    r1 = tmp_store.record_membership_snapshot(["AAPL", "MSFT"], as_of="2026-08-20")
    assert set(r1["entered"]) == {"AAPL", "MSFT"}
    r2 = tmp_store.record_membership_snapshot(["AAPL", "NVDA"], as_of="2026-08-27")
    assert r2["entered"] == ["NVDA"] and r2["exited"] == ["MSFT"]
    assert set(tmp_store.get_members_as_of("2026-08-21")) == {"AAPL", "MSFT"}

def test_strict_members_raises_before_first_snapshot(tmp_store):
    tmp_store.record_membership_snapshot(["AAPL"], as_of="2026-08-20")
    with pytest.raises(ValueError):
        tmp_store.get_members_as_of("2026-01-01")

def test_approximate_includes_former_member_not_in_current_extended(tmp_store):
    # R2-P1-1 核心用例：OLDCO 历史 $12B，今天既不在 Extended 也已 exited membership
    tmp_store.upsert_security_master([_sm_row(symbol="OLDCO")])
    tmp_store.record_membership_snapshot(["OLDCO"], as_of="2025-01-04")
    tmp_store.record_membership_snapshot(["AAPL"], as_of="2026-08-16")   # OLDCO 已 exited
    _seed_hmcap(tmp_store, "OLDCO", "2025-06-30", 12e9)   # helper: 直插 historical_market_cap
    out = tmp_store.approximate_members_as_of("2025-07-01")
    assert out["approximate"] is True and "OLDCO" in out["symbols"]

def test_approximate_keeps_unknown_symbol_as_unverified(tmp_store):
    _seed_hmcap(tmp_store, "GHOSTCO", "2025-06-30", 15e9)   # SM 完全无记录（如已退市未入池者）
    out = tmp_store.approximate_members_as_of("2025-07-01")
    assert "GHOSTCO" in out["symbols"] and "GHOSTCO" in out["unverified"]

def test_approximate_excludes_identity_blocked(tmp_store):
    tmp_store.upsert_security_master([_sm_row(symbol="SOXX", eligible=0, reason="etf")])
    _seed_hmcap(tmp_store, "SOXX", "2025-06-30", 11e9)
    assert "SOXX" not in tmp_store.approximate_members_as_of("2025-07-01")["symbols"]

def test_security_master_rejects_bad_row_atomically(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.upsert_security_master([_sm_row(), _sm_row(symbol="", eligible=None)])
    with pytest.raises(RuntimeError):
        tmp_store.get_security_eligibility()   # 整批拒绝 → 表仍空

def test_coverage_six_states_only(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.upsert_coverage_status([{"symbol": "A", "dataset": "income_quarterly",
                                           "status": "kinda_ok", "detail": None,
                                           "updated_at": "2026-08-20"}])


# ---------------------------------------------------------------------------
# Beyond the brief: get_active_members (controller ruling #1), get_needs_review_symbols,
# upsert_coverage_status/get_coverage happy path + retry bookkeeping, and
# record_membership_snapshot idempotency — none of these are in the brief's Step 1
# block but are part of this task's Produces list, so they need direct TDD coverage.
# ---------------------------------------------------------------------------

def test_get_active_members_empty_and_after_snapshot(tmp_store):
    assert tmp_store.get_active_members() == []
    tmp_store.record_membership_snapshot(["MSFT", "AAPL"], as_of="2026-08-20")
    assert tmp_store.get_active_members() == ["AAPL", "MSFT"]   # sorted, deduped
    tmp_store.record_membership_snapshot(["AAPL"], as_of="2026-08-27")   # MSFT exits
    assert tmp_store.get_active_members() == ["AAPL"]

def test_membership_snapshot_idempotent_same_input(tmp_store):
    tmp_store.record_membership_snapshot(["AAPL", "MSFT"], as_of="2026-08-20")
    r2 = tmp_store.record_membership_snapshot(["AAPL", "MSFT"], as_of="2026-08-20")
    assert r2 == {"entered": [], "exited": []}
    assert tmp_store.get_active_members() == ["AAPL", "MSFT"]

def test_get_needs_review_symbols(tmp_store):
    tmp_store.upsert_security_master([
        _sm_row(symbol="AAPL", reason="ok"),
        _sm_row(symbol="XX-A", cik="9", reason="needs_review_primary", eligible=0),
    ])
    assert tmp_store.get_needs_review_symbols() == ["XX-A"]


def test_non_common_instrument_reason_is_valid_and_excluded_from_asof(tmp_store):
    tmp_store.upsert_security_master([
        _sm_row(symbol="MER-PK", eligible=0, reason="non_common_instrument"),
    ])
    tmp_store.upsert_historical_market_cap(
        "MER-PK", [{"date": "2026-08-20", "market_cap": 50_000_000_000}],
    )

    result = tmp_store.approximate_members_as_of("2026-08-20")
    assert "MER-PK" not in result["symbols"]

def test_coverage_ok_resets_failures_and_clears_retry(tmp_store):
    tmp_store.upsert_coverage_status([{"symbol": "AAPL", "dataset": "income_quarterly",
                                       "status": "fetch_failed", "detail": "timeout",
                                       "updated_at": "2026-08-20"}])
    tmp_store.upsert_coverage_status([{"symbol": "AAPL", "dataset": "income_quarterly",
                                       "status": "fetch_failed", "detail": "timeout",
                                       "updated_at": "2026-08-20"}])
    conn = tmp_store._get_conn()
    row = conn.execute(
        "SELECT consecutive_failures, next_retry_at, last_success_at FROM coverage_status "
        "WHERE symbol='AAPL' AND dataset='income_quarterly'"
    ).fetchone()
    assert row["consecutive_failures"] == 2 and row["next_retry_at"] is not None
    assert row["last_success_at"] is None

    tmp_store.upsert_coverage_status([{"symbol": "AAPL", "dataset": "income_quarterly",
                                       "status": "ok", "detail": None,
                                       "updated_at": "2026-08-21"}])
    row = conn.execute(
        "SELECT consecutive_failures, next_retry_at, last_success_at FROM coverage_status "
        "WHERE symbol='AAPL' AND dataset='income_quarterly'"
    ).fetchone()
    assert row["consecutive_failures"] == 0
    assert row["next_retry_at"] is None
    assert row["last_success_at"] is not None
    assert tmp_store.get_coverage("income_quarterly") == {"AAPL": "ok"}

def test_coverage_provider_empty_sets_ttl_retry(tmp_store):
    tmp_store.upsert_coverage_status([{"symbol": "ETFX", "dataset": "income_quarterly",
                                       "status": "provider_empty", "detail": None,
                                       "updated_at": "2026-08-20"}])
    conn = tmp_store._get_conn()
    row = conn.execute(
        "SELECT next_retry_at FROM coverage_status "
        "WHERE symbol='ETFX' AND dataset='income_quarterly'"
    ).fetchone()
    assert row["next_retry_at"] is not None
    assert tmp_store.get_coverage("income_quarterly") == {"ETFX": "provider_empty"}


# ---------------------------------------------------------------------------
# Fix round 1 (controller ruling): not_applicable/stale must PRESERVE a pending
# next_retry_at (pure status annotations, never touch the retry timer);
# identity_blocked must EXPLICITLY CLEAR it (terminal, manual override per T12).
# ---------------------------------------------------------------------------

def _pending_retry_row(tmp_store, symbol="AAPL", dataset="income_quarterly"):
    tmp_store.upsert_coverage_status([{"symbol": symbol, "dataset": dataset,
                                       "status": "fetch_failed", "detail": "timeout",
                                       "updated_at": "2026-08-20"}])
    conn = tmp_store._get_conn()
    return conn.execute(
        "SELECT next_retry_at FROM coverage_status WHERE symbol=? AND dataset=?",
        (symbol, dataset),
    ).fetchone()["next_retry_at"]

def test_coverage_stale_preserves_pending_next_retry_at(tmp_store):
    pending = _pending_retry_row(tmp_store)
    assert pending is not None
    tmp_store.upsert_coverage_status([{"symbol": "AAPL", "dataset": "income_quarterly",
                                       "status": "stale", "detail": None,
                                       "updated_at": "2026-08-21"}])
    conn = tmp_store._get_conn()
    row = conn.execute(
        "SELECT next_retry_at, consecutive_failures FROM coverage_status "
        "WHERE symbol='AAPL' AND dataset='income_quarterly'"
    ).fetchone()
    assert row["next_retry_at"] == pending
    assert row["consecutive_failures"] == 1

def test_coverage_not_applicable_preserves_pending_next_retry_at(tmp_store):
    pending = _pending_retry_row(tmp_store)
    assert pending is not None
    tmp_store.upsert_coverage_status([{"symbol": "AAPL", "dataset": "income_quarterly",
                                       "status": "not_applicable", "detail": None,
                                       "updated_at": "2026-08-21"}])
    conn = tmp_store._get_conn()
    row = conn.execute(
        "SELECT next_retry_at FROM coverage_status "
        "WHERE symbol='AAPL' AND dataset='income_quarterly'"
    ).fetchone()
    assert row["next_retry_at"] == pending

def test_coverage_identity_blocked_clears_next_retry_at(tmp_store):
    pending = _pending_retry_row(tmp_store)
    assert pending is not None
    tmp_store.upsert_coverage_status([{"symbol": "AAPL", "dataset": "income_quarterly",
                                       "status": "identity_blocked", "detail": None,
                                       "updated_at": "2026-08-21"}])
    conn = tmp_store._get_conn()
    row = conn.execute(
        "SELECT next_retry_at FROM coverage_status "
        "WHERE symbol='AAPL' AND dataset='income_quarterly'"
    ).fetchone()
    assert row["next_retry_at"] is None
    assert tmp_store.get_coverage("income_quarterly") == {"AAPL": "identity_blocked"}
