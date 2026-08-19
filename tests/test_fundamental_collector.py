"""Tests for the shared fundamentals collection kernel (T8, P1-3/P1-4).

The kernel is the single write path for backfill (T10), events (T11),
reconcile (T12) and `--scope core`. Its per-dataset transaction boundary is
the project's data-integrity core: one dataset = one atomic commit of
[current table + vintage + coverage + manifest job], all-or-nothing.
"""
import json
import sqlite3

import pytest

from src.data.fundamental_collector import (
    DATASETS,
    DEFAULT_PROFILES_PATH,
    collect_fundamentals_for_symbol,
    rebuild_profiles_json,
)
from src.data.market_store import MarketStore

OBSERVED_AT = "2026-08-24"          # pure date — kernel normalizes for vintage
OBSERVED_AT_TS = "2026-08-24T00:00:00Z"


# ---------------------------------------------------------------------------
# Fake provider payloads (raw FMP shape: camelCase, extra vendor fields)
# ---------------------------------------------------------------------------

def _income_rows():
    return [
        {"date": "2026-06-30", "symbol": "AAPL", "reportedCurrency": "USD",
         "cik": "0000320193", "filingDate": "2026-08-01",
         "acceptedDate": "2026-08-01 16:30:00", "fiscalYear": "2026",
         "period": "Q3", "revenue": 94036000000.0, "netIncome": 21448000000.0,
         "eps": 1.4, "vendorOnlyField": "not-a-column"},
        {"date": "2026-03-31", "symbol": "AAPL", "reportedCurrency": "USD",
         "filingDate": "2026-05-02", "acceptedDate": "2026-05-02 16:30:00",
         "fiscalYear": "2026", "period": "Q2", "revenue": 90753000000.0,
         "netIncome": 23636000000.0, "eps": 1.53},
    ]


def _balance_rows():
    return [
        {"date": "2026-06-30", "symbol": "AAPL", "filingDate": "2026-08-01",
         "totalAssets": 331612000000.0, "totalLiabilities": 264904000000.0},
    ]


def _cashflow_rows():
    return [
        {"date": "2026-06-30", "symbol": "AAPL", "filingDate": "2026-08-01",
         "operatingCashFlow": 28858000000.0, "freeCashFlow": 26708000000.0},
    ]


def _ratios_rows():
    return [
        {"date": "2025-09-30", "symbol": "AAPL", "period": "FY",
         "grossProfitMargin": 0.462, "netProfitMargin": 0.2397},
        {"date": "2024-09-30", "symbol": "AAPL", "period": "FY",
         "grossProfitMargin": 0.4521, "netProfitMargin": 0.2397},
        # Provider junk: no fiscal date. Legacy `_bulk_upsert` skips these;
        # the kernel must skip them identically (no vintage on ratios).
        {"symbol": "AAPL", "period": "FY", "grossProfitMargin": 0.9},
    ]


def _profile_rows(symbol="AAPL"):
    return [
        {"symbol": symbol, "companyName": "Apple Inc.", "sector": "Technology",
         "industry": "Consumer Electronics", "marketCap": 3.4e12,
         "description": "Designs and sells consumer electronics.",
         "isEtf": False, "isFund": False, "isAdr": False},
    ]


def _full_responses():
    return {
        "profile": (_profile_rows(), "ok"),
        "income": (_income_rows(), "ok"),
        "balance": (_balance_rows(), "ok"),
        "cashflow": (_cashflow_rows(), "ok"),
        "ratios": (_ratios_rows(), "ok"),
    }


class FakeFMPClient:
    """Stands in for FMPClient.get_dataset_with_status (T4 interface).

    `responses` maps kind -> (rows, status) or a callable(symbol, limit).
    Every call is recorded so tests can pin the per-dataset limit (RULING #10).
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_dataset_with_status(self, kind, symbol, limit=None):
        self.calls.append({"kind": kind, "symbol": symbol, "limit": limit})
        resp = self.responses[kind]
        if callable(resp):
            return resp(symbol, limit)
        rows, status = resp
        return [dict(r) for r in rows], status

    def limit_for(self, kind):
        for call in self.calls:
            if call["kind"] == kind:
                return call["limit"]
        raise AssertionError("kind never fetched: " + str(kind))


@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


@pytest.fixture
def fake_client_full():
    return FakeFMPClient(_full_responses())


@pytest.fixture
def fake_client_balance_500():
    responses = _full_responses()
    responses["balance"] = ([], "fetch_failed")
    return FakeFMPClient(responses)


@pytest.fixture
def fake_client_no_cashflow():
    responses = _full_responses()
    responses["cashflow"] = ([], "provider_empty")
    return FakeFMPClient(responses)


def _count(store, table, symbol=None):
    conn = sqlite3.connect(str(store.db_path))
    try:
        if symbol is None:
            sql = "select count(*) from " + table
            return conn.execute(sql).fetchone()[0]
        sql = "select count(*) from " + table + " where symbol = ?"
        return conn.execute(sql, (symbol,)).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Brief Step 1 — mandatory tests
# ---------------------------------------------------------------------------

def test_happy_path_writes_all_five_targets(tmp_store, fake_client_full):
    statuses = collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses == {d: "ok" for d in DATASETS}
    assert tmp_store.known_as_of("AAPL", "income", "2026-08-24")
    assert tmp_store.get_coverage("income_quarterly")["AAPL"] == "ok"


def test_atomic_rollback_on_midwrite_failure(tmp_store, fake_client_full, monkeypatch):
    monkeypatch.setattr(tmp_store, "record_vintage_in_conn",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    statuses = collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["income"] == "fetch_failed"
    assert _count(tmp_store, "income_quarterly") == 0
    assert tmp_store.get_coverage("income_quarterly")["AAPL"] == "fetch_failed"


def test_partial_dataset_states(tmp_store, fake_client_balance_500):
    statuses = collect_fundamentals_for_symbol("AAPL", client=fake_client_balance_500,
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["income"] == "ok" and statuses["balance"] == "fetch_failed"


def test_provider_empty_recorded_not_zeroed(tmp_store, fake_client_no_cashflow):
    statuses = collect_fundamentals_for_symbol("BANKCO", client=fake_client_no_cashflow,
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["cashflow"] == "provider_empty"
    assert _count(tmp_store, "cash_flow_quarterly", "BANKCO") == 0


# ---------------------------------------------------------------------------
# Atomic boundary detail
# ---------------------------------------------------------------------------

def test_rollback_leaves_no_vintage_and_other_datasets_survive(
        tmp_store, fake_client_full, monkeypatch):
    """Only the failing dataset rolls back; independent datasets still land."""
    real = tmp_store.record_vintage_in_conn

    def only_income_fails(conn, symbol, statement, rows, observed_at, quality):
        if statement == "income":
            raise RuntimeError("disk full")
        return real(conn, symbol, statement, rows, observed_at, quality)

    monkeypatch.setattr(tmp_store, "record_vintage_in_conn", only_income_fails)
    statuses = collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["income"] == "fetch_failed"
    assert statuses["balance"] == "ok" and statuses["cashflow"] == "ok"
    assert statuses["profile"] == "ok" and statuses["ratios"] == "ok"
    assert _count(tmp_store, "income_quarterly") == 0
    assert tmp_store.known_as_of("AAPL", "income", OBSERVED_AT) == []
    assert _count(tmp_store, "balance_sheet_quarterly", "AAPL") == 1
    assert tmp_store.known_as_of("AAPL", "balance", OBSERVED_AT)


def test_provider_empty_records_coverage_with_retry_ttl(tmp_store, fake_client_no_cashflow):
    collect_fundamentals_for_symbol("BANKCO", client=fake_client_no_cashflow,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    assert tmp_store.get_coverage("cash_flow_quarterly")["BANKCO"] == "provider_empty"
    conn = sqlite3.connect(str(tmp_store.db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "select next_retry_at, last_attempt_at, consecutive_failures "
        "from coverage_status where symbol=? and dataset=?",
        ("BANKCO", "cash_flow_quarterly"),
    ).fetchone()
    conn.close()
    assert row["next_retry_at"] is not None       # negative-cache TTL, not a permanent hole
    assert row["last_attempt_at"] is not None
    assert row["consecutive_failures"] == 0
    assert tmp_store.known_as_of("BANKCO", "cashflow", OBSERVED_AT) == []


def test_client_exception_is_recorded_as_fetch_failed(tmp_store):
    def boom(symbol, limit):
        raise ConnectionError("connection reset by peer")

    responses = _full_responses()
    responses["income"] = boom
    statuses = collect_fundamentals_for_symbol("AAPL", client=FakeFMPClient(responses),
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["income"] == "fetch_failed"
    assert tmp_store.get_coverage("income_quarterly")["AAPL"] == "fetch_failed"
    assert statuses["balance"] == "ok"


def test_keyboard_interrupt_is_not_swallowed(tmp_store):
    """T10 relies on a mid-run interrupt reaching its cleanup, so the kernel
    must not degrade it into a per-dataset failure."""
    def interrupted(symbol, limit):
        raise KeyboardInterrupt()

    responses = dict(_full_responses(), income=interrupted)
    with pytest.raises(KeyboardInterrupt):
        collect_fundamentals_for_symbol("AAPL", client=FakeFMPClient(responses),
                                        store=tmp_store, observed_at=OBSERVED_AT)


def test_rows_that_all_skip_current_write_fail_loud(tmp_store):
    """Provider returned rows but none carry a fiscal date -> nothing would
    land while coverage claimed `ok`. That silent hole must surface instead."""
    responses = _full_responses()
    responses["ratios"] = ([{"symbol": "AAPL", "grossProfitMargin": 0.5}], "ok")
    statuses = collect_fundamentals_for_symbol("AAPL", client=FakeFMPClient(responses),
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["ratios"] == "fetch_failed"
    assert _count(tmp_store, "ratios_annual") == 0
    assert tmp_store.get_coverage("ratios_annual")["AAPL"] == "fetch_failed"


# ---------------------------------------------------------------------------
# Row-prep parity with the legacy `_bulk_upsert` writer (T11 parity contract)
# ---------------------------------------------------------------------------

def test_current_rows_identical_to_legacy_bulk_upsert(tmp_store, fake_client_full):
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    tmp_store.upsert_income("LEGACY", _income_rows())
    tmp_store.upsert_balance_sheet("LEGACY", _balance_rows())
    tmp_store.upsert_cash_flow("LEGACY", _cashflow_rows())
    tmp_store.upsert_ratios("LEGACY", _ratios_rows())

    pairs = [
        (tmp_store.get_income, 8),
        (tmp_store.get_balance_sheet, 8),
        (tmp_store.get_cash_flow, 8),
        (tmp_store.get_ratios, 4),
    ]
    for getter, limit in pairs:
        kernel_rows = getter("AAPL", limit=limit)
        legacy_rows = getter("LEGACY", limit=limit)
        assert kernel_rows and len(kernel_rows) == len(legacy_rows)
        for kern, legacy in zip(kernel_rows, legacy_rows):
            kern, legacy = dict(kern), dict(legacy)
            assert kern.pop("symbol") == "AAPL"
            assert legacy.pop("symbol") == "LEGACY"
            assert kern == legacy


def test_undated_rows_skipped_like_legacy(tmp_store, fake_client_full):
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    assert _count(tmp_store, "ratios_annual", "AAPL") == 2   # 3 fetched, 1 undated


def test_vintage_payload_keeps_raw_provider_row(tmp_store, fake_client_full):
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    rows = tmp_store.known_as_of("AAPL", "income", OBSERVED_AT)
    latest = [r for r in rows if r["date"] == "2026-06-30"][0]
    assert latest["vendorOnlyField"] == "not-a-column"        # as-reported fidelity
    assert latest["_observed_at"] == OBSERVED_AT_TS           # pure date normalized
    assert latest["_vintage_quality"] == "latest_known"


def test_repeat_collection_is_idempotent(tmp_store, fake_client_full):
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    statuses = collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                               store=tmp_store,
                                               observed_at="2026-08-31T10:00:00Z")
    assert statuses == {d: "ok" for d in DATASETS}
    assert _count(tmp_store, "income_quarterly", "AAPL") == 2
    assert _count(tmp_store, "fundamental_vintage", "AAPL") == 4   # change-only: no re-append


def test_same_date_recollection_with_changed_content_fails_loud(tmp_store, fake_client_full):
    """Same pure DATE + changed content collides on the vintage PK. It must
    surface as a failure, never clobber append-only history — runners that
    re-touch a symbol within a day should pass a full timestamp instead."""
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    restated = _income_rows()
    restated[0]["revenue"] = 93000000000.0
    responses = dict(_full_responses(), income=(restated, "ok"))
    statuses = collect_fundamentals_for_symbol("AAPL", client=FakeFMPClient(responses),
                                               store=tmp_store, observed_at=OBSERVED_AT)
    assert statuses["income"] == "fetch_failed"
    kept = [r for r in tmp_store.known_as_of("AAPL", "income", OBSERVED_AT)
            if r["date"] == "2026-06-30"][0]
    assert kept["revenue"] == 94036000000.0
    assert tmp_store.get_income("AAPL")[0]["revenue"] == 94036000000.0


def test_dataset_limits_follow_ruling_10(tmp_store, fake_client_full):
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full, store=tmp_store,
                                    limit_quarters=12, observed_at=OBSERVED_AT)
    assert fake_client_full.limit_for("income") == 12
    assert fake_client_full.limit_for("balance") == 12
    assert fake_client_full.limit_for("cashflow") == 12
    assert fake_client_full.limit_for("ratios") == 4      # legacy get_ratios default
    assert fake_client_full.limit_for("profile") is None  # endpoint takes no limit


# ---------------------------------------------------------------------------
# company_profile table (SSOT) + profiles.json mirror
# ---------------------------------------------------------------------------

def test_profile_lands_in_company_profile_table(tmp_store, fake_client_full):
    collect_fundamentals_for_symbol("aapl", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    conn = sqlite3.connect(str(tmp_store.db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("select * from company_profile where symbol='AAPL'").fetchone()
    conn.close()
    payload = json.loads(row["payload"])
    assert payload["sector"] == "Technology"
    assert row["updated_at"] == OBSERVED_AT_TS
    assert tmp_store.get_coverage("company_profile")["AAPL"] == "ok"


def test_kernel_does_not_write_profiles_json(tmp_store, fake_client_full, tmp_path):
    mirror = tmp_path / "fundamental" / "profiles.json"
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    assert not mirror.exists()          # R2-P2-3: table is the SSOT, mirror is on demand


def test_rebuild_profiles_json_mirrors_table(tmp_store, fake_client_full, tmp_path):
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    msft = FakeFMPClient(dict(_full_responses(), profile=(_profile_rows("MSFT"), "ok")))
    collect_fundamentals_for_symbol("MSFT", client=msft, store=tmp_store,
                                    observed_at=OBSERVED_AT)

    path = tmp_path / "fundamental" / "profiles.json"
    written = rebuild_profiles_json(tmp_store, path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert written == 2
    assert set(data) == {"AAPL", "MSFT", "_meta"}
    assert data["_meta"]["count"] == 2
    assert data["AAPL"]["symbol"] == "AAPL"
    assert data["AAPL"]["sector"] == "Technology"          # legacy readers' fields
    assert data["AAPL"]["companyName"] == "Apple Inc."
    assert data["AAPL"]["_updated_at"] == OBSERVED_AT_TS
    assert list(path.parent.glob("*.tmp")) == []           # atomic replace, no debris


def test_rebuild_profiles_json_is_a_rebuild_not_a_merge(tmp_store, fake_client_full, tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"STALE": {"symbol": "STALE"}, "_meta": {"count": 1}}),
                    encoding="utf-8")
    collect_fundamentals_for_symbol("AAPL", client=fake_client_full,
                                    store=tmp_store, observed_at=OBSERVED_AT)
    rebuild_profiles_json(tmp_store, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "STALE" not in data and "AAPL" in data


def test_default_mirror_path_is_the_legacy_location():
    from config.settings import FUNDAMENTAL_DIR
    assert DEFAULT_PROFILES_PATH == FUNDAMENTAL_DIR / "profiles.json"


def test_rebuild_profiles_json_refuses_to_clobber_with_empty_table(tmp_store, tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"AAPL": {"symbol": "AAPL"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        rebuild_profiles_json(tmp_store, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"AAPL": {"symbol": "AAPL"}}


# ---------------------------------------------------------------------------
# job_writer composition (T9/T10 manifest joins the same transaction)
# ---------------------------------------------------------------------------

def _job_rows(store):
    conn = sqlite3.connect(str(store.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return {r["dataset"]: r["status"] for r in conn.execute(
            "select dataset, status from fundamental_backfill_jobs").fetchall()}
    finally:
        conn.close()


def _recording_job_writer(seen, fail_on=None):
    """Manifest hook stand-in (T9 `complete_job_in_conn` shape, run_id/symbol
    already bound). `fail_on` makes the FIRST call for that dataset raise."""
    failed = set()

    def job_writer(conn, dataset, status, detail=None):
        seen.append((dataset, status))
        if dataset == fail_on and dataset not in failed:
            failed.add(dataset)
            raise RuntimeError("manifest write failed")
        conn.execute(
            "insert or replace into fundamental_backfill_jobs "
            "(run_id, symbol, dataset, status) values (?, ?, ?, ?)",
            ("r1", "AAPL", dataset, status),
        )

    return job_writer


def test_job_writer_records_every_dataset_status(tmp_store, fake_client_no_cashflow):
    seen = []
    collect_fundamentals_for_symbol("AAPL", client=fake_client_no_cashflow,
                                    store=tmp_store, observed_at=OBSERVED_AT,
                                    job_writer=_recording_job_writer(seen))
    assert _job_rows(tmp_store) == {
        "profile": "ok", "income": "ok", "balance": "ok",
        "cashflow": "provider_empty", "ratios": "ok",
    }
    assert [d for d, _ in seen] == list(DATASETS)     # dataset keys, not table names


def test_job_writer_failure_rolls_back_the_whole_dataset(tmp_store, fake_client_full):
    """The manifest write is inside the boundary in both directions: if it
    fails, the data it describes must not survive."""
    seen = []
    statuses = collect_fundamentals_for_symbol(
        "AAPL", client=fake_client_full, store=tmp_store, observed_at=OBSERVED_AT,
        job_writer=_recording_job_writer(seen, fail_on="income"))

    assert statuses["income"] == "fetch_failed" and statuses["balance"] == "ok"
    assert ("income", "ok") in seen and ("income", "fetch_failed") in seen
    assert _count(tmp_store, "income_quarterly") == 0
    assert tmp_store.known_as_of("AAPL", "income", OBSERVED_AT) == []
    assert tmp_store.get_coverage("income_quarterly")["AAPL"] == "fetch_failed"

    rows = _job_rows(tmp_store)
    assert rows["income"] == "fetch_failed"           # re-recorded in recovery txn
    assert rows["balance"] == "ok"
    assert len(rows) == len(DATASETS)
