"""Tests for scripts/bootstrap_security_master.py (T6, R1/R2-P1-1/R3-P1-2/R4-P1-1).

No network, no data/ dependency: everything goes through `_FakeClient`
(stand-in for FMPClient.get_dataset_with_status) and a temp-file MarketStore.
"""
import sqlite3

import pytest

from scripts.bootstrap_security_master import run_bootstrap
from src.data.market_store import MarketStore


@pytest.fixture
def tmp_store(tmp_path):
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


def _seed_hmcap(store, symbol, date, market_cap):
    """直插 historical_market_cap（生产列名 symbol/date/market_cap，见 T1 DDL）。"""
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO historical_market_cap (symbol, date, market_cap) "
        "VALUES (?, ?, ?)",
        (symbol, date, market_cap),
    )
    conn.commit()


def _synthetic_profile(symbol, is_etf=False, is_fund=False):
    """Unique-per-symbol FMP-shaped payload — distinct cik/company_name so
    unrelated symbols never accidentally group as share classes in tests.
    """
    return {
        "symbol": symbol,
        "cik": "CIK-" + symbol,
        "companyName": symbol + " Inc.",
        "exchangeShortName": "NASDAQ",
        "isEtf": is_etf,
        "isFund": is_fund,
        "isAdr": False,
        "marketCap": 1000,
    }


class _FakeClient:
    """Minimal stand-in for FMPClient.get_dataset_with_status("profile", sym).

    profiles: symbol -> explicit payload (returned as a 1-item "ok" list)
    fetch_failed / provider_empty: symbols forced to those statuses
    default_ok: any other requested symbol gets a synthesized unique-cik
      "ok" profile (so union/denominator tests don't need to enumerate
      every source symbol by hand) when True, else provider_empty.
    """

    def __init__(self, profiles=None, fetch_failed=None, provider_empty=None,
                 default_ok=True):
        self.profiles = dict(profiles or {})
        self.fetch_failed = set(fetch_failed or ())
        self.provider_empty = set(provider_empty or ())
        self.default_ok = default_ok
        self.symbols_fetched = []

    def get_dataset_with_status(self, kind, symbol, limit=8):
        assert kind == "profile"
        self.symbols_fetched.append(symbol)
        if symbol in self.fetch_failed:
            return [], "fetch_failed"
        if symbol in self.provider_empty:
            return [], "provider_empty"
        if symbol in self.profiles:
            return [self.profiles[symbol]], "ok"
        if self.default_ok:
            return [_synthetic_profile(symbol)], "ok"
        return [], "provider_empty"


@pytest.fixture
def raw_list_3():
    return ["AAPL", "MSFT", "SOXX"]


@pytest.fixture
def fake_client():
    return _FakeClient(profiles={"SOXX": _synthetic_profile("SOXX", is_etf=True)})


@pytest.fixture
def fake_client_all_etf():
    return _FakeClient(profiles={"SOXX": _synthetic_profile("SOXX", is_etf=True)})


@pytest.fixture
def fake_client_one_500():
    return _FakeClient(fetch_failed={"BADNET"})


@pytest.fixture
def fake_client_mixed():
    return _FakeClient(profiles={"SOXX": _synthetic_profile("SOXX", is_etf=True)})


@pytest.fixture
def fake_client_full():
    return _FakeClient()


def test_bootstrap_rerun_partial_fetch_preserves_same_cik_primary(tmp_store):
    """A rerun where OLD fails but NEW succeeds must still close the CIK group
    over the incumbent SM row instead of minting a second eligible primary."""
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
    client = _FakeClient(
        profiles={"NEW": {"symbol": "NEW", "cik": "CIK-SAME",
                           "companyName": "Same Co", "exchangeShortName": "NASDAQ",
                           "mktCap": 90}},
        fetch_failed={"OLD"},
    )

    rc = run_bootstrap(
        store=tmp_store, client=client, raw_loader=lambda: ["OLD", "NEW"],
        current_only=True, as_of="2026-08-24")
    rows = tmp_store._get_conn().execute(
        "SELECT symbol, eligible, reason, share_class_of FROM security_master "
        "WHERE cik = 'CIK-SAME' ORDER BY symbol"
    ).fetchall()

    assert rc == 0
    assert [tuple(row) for row in rows] == [
        ("NEW", 0, "secondary_share_class", "OLD"),
        ("OLD", 1, "ok", None),
    ]


@pytest.fixture
def fake_client_deadco_empty():
    return _FakeClient(provider_empty={"DEADCO"})


# ---------------------------------------------------------------------------
# Brief Step 1 (verbatim)
# ---------------------------------------------------------------------------

def test_bootstrap_happy_path(tmp_store, fake_client, raw_list_3):
    rc = run_bootstrap(store=tmp_store, client=fake_client, raw_loader=lambda: raw_list_3)
    assert rc == 0
    elig = tmp_store.get_security_eligibility()
    assert sum(elig.values()) >= 1


def test_bootstrap_empty_raw_list_fails_loud(tmp_store, fake_client):
    assert run_bootstrap(store=tmp_store, client=fake_client, raw_loader=lambda: []) == 2


def test_bootstrap_zero_eligible_fails_loud(tmp_store, fake_client_all_etf):
    rc = run_bootstrap(store=tmp_store, client=fake_client_all_etf, raw_loader=lambda: ["SOXX"])
    assert rc == 2


def test_fetch_failed_symbol_not_written_as_blocked(tmp_store, fake_client_one_500):
    run_bootstrap(store=tmp_store, client=fake_client_one_500, raw_loader=lambda: ["AAPL", "BADNET"])
    assert "BADNET" not in tmp_store.get_security_eligibility()   # 待重跑，不是永久 blocked


def test_network_failure_still_visible_in_identity_queue(tmp_store, fake_client_one_500):
    # R3-P1-2：不写 SM 但必写 coverage(identity)=fetch_failed —— repair queue 不丢
    run_bootstrap(store=tmp_store, client=fake_client_one_500, raw_loader=lambda: ["AAPL", "BADNET"])
    assert tmp_store.get_coverage("identity").get("BADNET") == "fetch_failed"


def test_report_counts_by_reason(tmp_store, fake_client_mixed, capsys):
    run_bootstrap(store=tmp_store, client=fake_client_mixed, raw_loader=lambda: ["AAPL", "SOXX"])
    out = capsys.readouterr().out
    assert "eligible" in out and "etf" in out


def test_denominator_includes_historical_and_delisted_sources(tmp_store, fake_client_full):
    # R2-P1-1：OLDCO 不在当前 Extended，但历史 hmcap $12B → 必须进 bootstrap 分母
    _seed_hmcap(tmp_store, "OLDCO", "2025-06-30", 12e9)
    run_bootstrap(store=tmp_store, client=fake_client_full,
                  raw_loader=lambda: ["AAPL"],
                  delisted_loader=lambda: ["DEADCO"])
    fetched = fake_client_full.symbols_fetched
    assert {"AAPL", "OLDCO", "DEADCO"} <= set(fetched)


def test_delisted_profile_empty_recorded_not_dropped(tmp_store, fake_client_deadco_empty):
    run_bootstrap(store=tmp_store, client=fake_client_deadco_empty,
                  raw_loader=lambda: ["AAPL"], delisted_loader=lambda: ["DEADCO"])
    conn = sqlite3.connect(tmp_store.db_path)
    row = conn.execute("select reason from security_master where symbol='DEADCO'").fetchone()
    assert row[0] == "missing_profile"        # 在册可见，不是消失


# ---------------------------------------------------------------------------
# Supplementary tests (beyond the brief's mandatory 8)
# ---------------------------------------------------------------------------

def test_current_only_skips_historical_and_delisted_sources(tmp_store, fake_client_full):
    _seed_hmcap(tmp_store, "OLDCO", "2025-06-30", 12e9)
    run_bootstrap(store=tmp_store, client=fake_client_full,
                  raw_loader=lambda: ["AAPL"],
                  delisted_loader=lambda: ["DEADCO"],
                  current_only=True)
    assert set(fake_client_full.symbols_fetched) == {"AAPL"}


def test_membership_snapshot_written_on_success(tmp_store, fake_client, raw_list_3):
    run_bootstrap(store=tmp_store, client=fake_client, raw_loader=lambda: raw_list_3)
    members = set(tmp_store.get_active_members())
    assert members == {"AAPL", "MSFT"}   # SOXX blocked (etf), never entered membership


def test_zero_eligible_does_not_write_membership_snapshot(tmp_store, fake_client_all_etf):
    run_bootstrap(store=tmp_store, client=fake_client_all_etf, raw_loader=lambda: ["SOXX"])
    assert tmp_store.get_active_members() == []


def test_dry_run_writes_nothing(tmp_store, fake_client, raw_list_3):
    rc = run_bootstrap(store=tmp_store, client=fake_client, raw_loader=lambda: raw_list_3,
                        dry_run=True)
    assert rc == 0
    conn = sqlite3.connect(tmp_store.db_path)
    assert conn.execute("select count(*) from security_master").fetchone()[0] == 0
    assert conn.execute("select count(*) from coverage_status").fetchone()[0] == 0
    assert conn.execute("select count(*) from extended_membership").fetchone()[0] == 0


def test_limit_caps_union_symbols_processed(tmp_store, fake_client_full):
    run_bootstrap(store=tmp_store, client=fake_client_full,
                  raw_loader=lambda: ["AAPL", "MSFT", "GOOG"], limit=1)
    assert len(fake_client_full.symbols_fetched) == 1


def test_report_path_none_by_default_no_real_data_dir_write(tmp_store, fake_client, raw_list_3):
    # run_bootstrap is a pure-ish function: without an explicit report_path
    # it must never write into the real repo's data/ directory (only the
    # CLI wrapper's main() supplies that path).
    from scripts.bootstrap_security_master import REPORT_PATH
    run_bootstrap(store=tmp_store, client=fake_client, raw_loader=lambda: raw_list_3)
    assert not REPORT_PATH.exists()


def test_report_written_atomically_when_path_given(tmp_store, fake_client, raw_list_3, tmp_path):
    import json
    report_path = tmp_path / "report.json"
    run_bootstrap(store=tmp_store, client=fake_client, raw_loader=lambda: raw_list_3,
                  report_path=report_path)
    assert report_path.exists()
    assert not report_path.with_name(report_path.name + ".tmp").exists()
    payload = json.loads(report_path.read_text())
    assert payload["result"] == "OK"
    assert payload["union_total"] == 3


def test_identity_conflict_writes_identity_blocked_coverage(tmp_store):
    # 同 cik 不同 company_name -> resolve_share_classes 判 identity_conflict
    conflict_client = _FakeClient(profiles={
        "AA": {"symbol": "AA", "cik": "SAME", "companyName": "Alpha Inc.",
               "exchangeShortName": "NYSE", "isEtf": False, "isFund": False},
        "BB": {"symbol": "BB", "cik": "SAME", "companyName": "Beta Corp.",
               "exchangeShortName": "NYSE", "isEtf": False, "isFund": False},
    }, default_ok=False)
    run_bootstrap(store=tmp_store, client=conflict_client, raw_loader=lambda: ["AA", "BB"])
    conn = sqlite3.connect(tmp_store.db_path)
    reasons = dict(conn.execute("select symbol, reason from security_master").fetchall())
    assert reasons == {"AA": "identity_conflict", "BB": "identity_conflict"}
    assert tmp_store.get_coverage("identity")["AA"] == "identity_blocked"
    assert tmp_store.get_coverage("identity")["BB"] == "identity_blocked"
