"""Tests for src/data/entrant_bootstrap.py (T17, R2-P1-2 / R3-P1-2).

No network, no data/ dependency: a fake profile client + a temp-file
MarketStore. The Identity 状态契约 assertions here deliberately mirror the
rows T6 pins in tests/test_bootstrap_security_master.py — `bootstrap_entrants`
REUSES T6's per-symbol kernel, so the weekly entrant path must show exactly
the same behavior rather than a parallel one that can drift.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.entrant_bootstrap import bootstrap_entrants
from src.data.market_store import MarketStore


@pytest.fixture
def tmp_store(tmp_path):
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


def _profile(symbol, cik=None, name=None, mkt_cap=1000, is_etf=False, is_fund=False):
    return {
        "symbol": symbol,
        "cik": cik or ("CIK-" + symbol),
        "companyName": name or (symbol + " Inc."),
        "exchangeShortName": "NASDAQ",
        "isEtf": is_etf,
        "isFund": is_fund,
        "isAdr": False,
        "mktCap": mkt_cap,
    }


class _FakeClient:
    """Stand-in for FMPClient.get_dataset_with_status("profile", sym)."""

    def __init__(self, profiles=None, fetch_failed=None, provider_empty=None):
        self.profiles = dict(profiles or {})
        self.fetch_failed = set(fetch_failed or ())
        self.provider_empty = set(provider_empty or ())
        self.symbols_fetched = []

    def get_dataset_with_status(self, kind, symbol, limit=8):
        assert kind == "profile"
        self.symbols_fetched.append(symbol)
        if symbol in self.fetch_failed:
            return [], "fetch_failed"
        if symbol in self.provider_empty:
            return [], "provider_empty"
        return [self.profiles.get(symbol, _profile(symbol))], "ok"


def _seed_sm(store, symbol, cik, name, eligible=True, reason="ok", share_class_of=None):
    store.upsert_security_master([{
        "symbol": symbol, "cik": cik, "company_name": name, "exchange": "NASDAQ",
        "is_etf": 0, "is_fund": 0, "is_adr": 0, "share_class_of": share_class_of,
        "eligible": 1 if eligible else 0, "reason": reason,
        "updated_at": "2026-08-01T00:00:00Z",
    }])


def _seed_profile(store, symbol, payload):
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO company_profile (symbol, payload, updated_at) "
        "VALUES (?, ?, ?)",
        (symbol, json.dumps(payload), "2026-08-01T00:00:00Z"),
    )
    conn.commit()


def _sm_row(store, symbol):
    conn = sqlite3.connect(str(store.db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM security_master WHERE symbol = ?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


class TestIdentityContract:
    """The four frozen rows of the Identity 状态契约 (R3-P1-2)."""

    def test_resolved_ok_entrant_is_eligible_with_coverage_ok(self, tmp_store):
        client = _FakeClient()
        summary = bootstrap_entrants(["NEWCO"], client=client, store=tmp_store)

        row = _sm_row(tmp_store, "NEWCO")
        assert row["eligible"] == 1
        assert row["reason"] == "ok"
        assert tmp_store.get_coverage("identity")["NEWCO"] == "ok"
        assert summary["eligible"] == ["NEWCO"]

    def test_fetch_failure_writes_no_sm_row_and_queues_repair(self, tmp_store):
        client = _FakeClient(fetch_failed=["NEWCO"])
        summary = bootstrap_entrants(["NEWCO"], client=client, store=tmp_store)

        assert _sm_row(tmp_store, "NEWCO") is None, "network failure must not mint SM identity"
        assert tmp_store.get_coverage("identity")["NEWCO"] == "fetch_failed"
        assert summary["fetch_failed"] == ["NEWCO"]

    def test_provider_empty_writes_missing_profile_sm_row(self, tmp_store):
        client = _FakeClient(provider_empty=["NEWCO"])
        summary = bootstrap_entrants(["NEWCO"], client=client, store=tmp_store)

        row = _sm_row(tmp_store, "NEWCO")
        assert row["reason"] == "missing_profile"
        assert row["eligible"] == 0
        # missing_profile is an SM-only value, never a coverage(identity) status
        assert tmp_store.get_coverage("identity")["NEWCO"] == "provider_empty"
        assert summary["blocked"]["NEWCO"] == "missing_profile"

    def test_identity_conflict_within_batch_blocks_every_member(self, tmp_store):
        client = _FakeClient(profiles={
            "AAA": _profile("AAA", cik="CIK-SHARED", name="Alpha Inc."),
            "BBB": _profile("BBB", cik="CIK-SHARED", name="Beta Corp."),
        })
        summary = bootstrap_entrants(["AAA", "BBB"], client=client, store=tmp_store)

        for symbol in ("AAA", "BBB"):
            row = _sm_row(tmp_store, symbol)
            assert row["reason"] == "identity_conflict"
            assert row["eligible"] == 0
            assert tmp_store.get_coverage("identity")[symbol] == "identity_blocked"
        assert summary["eligible"] == []


class TestShareClassAgainstExistingMaster:
    """An entrant sharing a CIK with an ALREADY-mastered symbol must be graded
    against it — grouping only within the weekly batch would silently mint a
    second eligible primary for one company (double-counted membership).
    """

    def test_entrant_loses_to_existing_primary(self, tmp_store):
        _seed_sm(tmp_store, "GOOGL", "CIK-ALPHA", "Alphabet Inc.")
        _seed_profile(tmp_store, "GOOGL", _profile("GOOGL", cik="CIK-ALPHA",
                                                   name="Alphabet Inc.", mkt_cap=2000))
        client = _FakeClient(profiles={
            "GOOG": _profile("GOOG", cik="CIK-ALPHA",
                             name="Alphabet Inc. Class C", mkt_cap=1000),
        })

        summary = bootstrap_entrants(["GOOG"], client=client, store=tmp_store)

        entrant = _sm_row(tmp_store, "GOOG")
        assert entrant["reason"] == "secondary_share_class"
        assert entrant["eligible"] == 0
        assert entrant["share_class_of"] == "GOOGL"
        assert _sm_row(tmp_store, "GOOGL")["eligible"] == 1, "incumbent primary untouched"
        assert summary["eligible"] == []

    def test_entrant_wins_and_demotes_existing_primary(self, tmp_store):
        _seed_sm(tmp_store, "SMALL", "CIK-ALPHA", "Alphabet Inc.")
        _seed_profile(tmp_store, "SMALL", _profile("SMALL", cik="CIK-ALPHA",
                                                   name="Alphabet Inc.", mkt_cap=100))
        client = _FakeClient(profiles={
            "BIG": _profile("BIG", cik="CIK-ALPHA",
                            name="Alphabet Inc. Class A", mkt_cap=9000),
        })

        summary = bootstrap_entrants(["BIG"], client=client, store=tmp_store)

        assert _sm_row(tmp_store, "BIG")["eligible"] == 1
        demoted = _sm_row(tmp_store, "SMALL")
        assert demoted["reason"] == "secondary_share_class"
        assert demoted["eligible"] == 0
        assert demoted["share_class_of"] == "BIG"
        assert summary["reclassified"] == ["SMALL"]

    def test_unrelated_incumbent_is_not_rewritten(self, tmp_store):
        _seed_sm(tmp_store, "AAPL", "CIK-AAPL", "Apple Inc.")
        before = _sm_row(tmp_store, "AAPL")

        bootstrap_entrants(["NEWCO"], client=_FakeClient(), store=tmp_store)

        assert _sm_row(tmp_store, "AAPL") == before
        assert _sm_row(tmp_store, "NEWCO")["eligible"] == 1


class TestBatchMechanics:
    def test_empty_entrant_list_touches_nothing(self, tmp_store):
        client = _FakeClient()
        summary = bootstrap_entrants([], client=client, store=tmp_store)

        assert client.symbols_fetched == []
        assert summary["requested"] == 0
        conn = sqlite3.connect(str(tmp_store.db_path))
        assert conn.execute("SELECT count(*) FROM security_master").fetchone()[0] == 0
        conn.close()

    def test_symbols_are_deduped_uppercased_and_fetched_once_each(self, tmp_store):
        client = _FakeClient()
        bootstrap_entrants(["newco", "NEWCO", "ZZZ"], client=client, store=tmp_store)

        assert client.symbols_fetched == ["NEWCO", "ZZZ"]
        assert _sm_row(tmp_store, "NEWCO") is not None

    def test_resolved_profile_is_persisted_for_later_grouping(self, tmp_store):
        bootstrap_entrants(["NEWCO"], client=_FakeClient(), store=tmp_store)

        conn = sqlite3.connect(str(tmp_store.db_path))
        row = conn.execute(
            "SELECT payload FROM company_profile WHERE symbol = ?", ("NEWCO",)
        ).fetchone()
        conn.close()
        assert row is not None, "profile must persist — next week's grouping needs mktCap"
        assert json.loads(row[0])["symbol"] == "NEWCO"

    def test_summary_partitions_every_requested_symbol(self, tmp_store):
        client = _FakeClient(fetch_failed=["DOWN"], provider_empty=["GHOST"])
        summary = bootstrap_entrants(
            ["NEWCO", "DOWN", "GHOST"], client=client, store=tmp_store
        )

        assert summary["requested"] == 3
        assert summary["eligible"] == ["NEWCO"]
        assert summary["fetch_failed"] == ["DOWN"]
        assert sorted(summary["blocked"]) == ["GHOST"]
