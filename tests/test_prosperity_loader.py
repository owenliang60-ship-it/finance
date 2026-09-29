import json

from src.data.market_store import MarketStore
from terminal.prosperity.loader import load_history, resolve_members
from tests.prosperity_fixtures import seed_db


def test_resolve_members_live_basis_filters_ineligible(tmp_path):
    store = MarketStore(db_path=seed_db(tmp_path / "m.db"), read_only=True)
    symbols, basis, unverified = resolve_members(store, "2026-09-26")
    assert (symbols, basis, unverified) == (["AAA", "BBB"], "extended_membership", frozenset())


def test_resolve_members_historical_uses_mcap_basis(tmp_path):
    store = MarketStore(db_path=seed_db(tmp_path / "m.db"), read_only=True)
    symbols, basis, _ = resolve_members(store, "2021-12-31")
    assert (symbols, basis) == (["AAA"], "approximate_mcap")


def test_load_history_reads_weekly_estimates_only_and_profile(tmp_path):
    store = MarketStore(db_path=seed_db(tmp_path / "m.db"), read_only=True)
    h = load_history(store, "AAA", with_vintage=False)
    assert [r["snapshot_kind"] for r in h.estimates] == ["weekly"]
    assert h.profile["sector"] == "Technology" and h.is_adr is False
    assert h.income[0]["revenue"] == 100.0 and h.vintage == {}


def test_load_history_converts_vintage_payload_to_snake_case(tmp_path):
    path = seed_db(tmp_path / "m.db")
    w = MarketStore(db_path=path)
    with w._get_conn() as conn:
        conn.execute(
            "INSERT INTO fundamental_vintage (symbol, statement, fiscal_date, observed_at, filing_date, "
            "accepted_date, content_hash, vintage_quality, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            ("AAA", "income", "2026-03-31", "2026-09-28T13:04:27Z", "2026-05-01", "2026-05-01 16:00:00",
             "h", "latest_known", json.dumps({"date": "2026-03-31", "revenue": 99.0, "reportedCurrency": "USD",
                                              "fiscalYear": "2026", "period": "Q1",
                                              "acceptedDate": "2026-05-01 16:00:00"})))
    h = load_history(MarketStore(db_path=path, read_only=True), "AAA", with_vintage=True)
    row = h.vintage["income"][0]
    assert row["revenue"] == 99.0 and row["reported_currency"] == "USD"
    assert row["_observed_at"] == "2026-09-28T13:04:27Z"
