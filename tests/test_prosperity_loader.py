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


def test_load_history_reads_date_removing_repairs_as_tombstones(tmp_path):
    # Codex M4 review F2: repaired alias dates stay in the append-only vintage; the repair archive says when they left
    path = seed_db(tmp_path / "m.db")
    w = MarketStore(db_path=path)
    rows = [("equivalent_fiscal_alias", "income_quarterly", "2024-10-30", "2026-09-29T03:11:58.851349Z"),
            ("reviewed_fiscal_repair", "balance_sheet_quarterly", "2025-01-31", "2026-09-05T02:00:00Z"),
            ("reviewed_fiscal_period_label", "income_quarterly", "2025-03-31", "2026-09-05T02:00:00Z"),  # in place
            ("metrics_recomputed", "metrics_quarterly", "2025-03-31", "2026-09-05T02:00:00Z")]
    with w._get_conn() as conn:
        for reason, table, day, at in rows:
            conn.execute("INSERT INTO fundamental_current_archive (operation_id, archived_at, source_table, symbol, "
                         "original_date, reason, context, payload) VALUES (?,?,?,?,?,?,?,?)",
                         ("op", at, table, "AAA", day, reason, "{}", "{}"))
    h = load_history(MarketStore(db_path=path, read_only=True), "AAA", with_vintage=True)
    assert h.removed == {"income": [("2024-10-30", "2026-09-29T03:11:58.851349Z")],
                         "balance": [("2025-01-31", "2026-09-05T02:00:00Z")]}
    assert load_history(MarketStore(db_path=path, read_only=True), "AAA", with_vintage=False).removed == {}


def _archive(conn, symbol, reason, table, day, at, context):
    conn.execute("INSERT INTO fundamental_current_archive (operation_id, archived_at, source_table, symbol, "
                 "original_date, reason, context, payload) VALUES (?,?,?,?,?,?,?,?)",
                 ("op", at, table, symbol, day, reason, json.dumps(context), "{}"))


def test_strict_history_keeps_raw_vintage_dates_and_reads_timed_renames(tmp_path):
    # P1-1 + review fix 1, market.db AEM: vintage rows keep the provider date; the reviewed rename
    # and the alias flip are read with their archived_at so strict replay applies them only from then on
    path = seed_db(tmp_path / "m.db")
    w = MarketStore(db_path=path)
    flip, first = "2026-10-10T06:34:17.571734Z", "2026-09-29T03:11:58.851349Z"
    with w._get_conn() as conn:
        conn.execute(
            "INSERT INTO fundamental_vintage (symbol, statement, fiscal_date, observed_at, filing_date, "
            "accepted_date, content_hash, vintage_quality, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            ("AEM", "income", "2024-10-30", "2026-09-04T15:03:34Z", "2024-10-31", "2024-10-31 18:33:32", "h",
             "latest_known", json.dumps({"date": "2024-10-30", "symbol": "AEM", "cik": "0000002809",
                                         "fiscalYear": "2024", "period": "Q3", "reportedCurrency": "USD",
                                         "revenue": 2160.0})))
        _archive(conn, "AEM", "equivalent_fiscal_alias", "income_quarterly", "2024-10-30", first,
                 {"keep_date": "2024-09-30"})
        _archive(conn, "AEM", "equivalent_fiscal_alias", "income_quarterly", "2024-09-30", flip,
                 {"keep_date": "2024-10-30"})
        _archive(conn, "AEM", "reviewed_identity_update", "income_quarterly", "2024-10-30", "2026-10-10T08:23:31Z",
                 {"plan": {"changes": {"date": "2024-09-30"}, "date": "2024-10-30"}})
        _archive(conn, "AEM", "reviewed_identity_update", "balance_sheet_quarterly", "2024-10-30",
                 "2026-10-10T08:23:32Z", {"plan": {"changes": {"date": "2024-09-30"}, "date": "2024-10-30"}})
        _archive(conn, "AEM", "reviewed_identity_update", "income_quarterly", "2024-03-31", "2026-10-10T08:23:33Z",
                 {"plan": {"changes": {"period": "Q1"}, "date": "2024-03-31"}})     # CRH-type: period only
        _archive(conn, "AEM", "reviewed_fiscal_repair", "balance_sheet_quarterly", "2025-10-28", "2026-09-05T01:04:27Z",
                 {"after_hash": "x", "plan": {"keep_date": "2025-09-30", "period": "Q3"}})
        _archive(conn, "AEM", "reviewed_monthly_row_removed", "balance_sheet_quarterly", "2026-08-31",
                 "2026-10-25T14:32:16Z", {"plan": {"date": "2026-08-31"}})
    h = load_history(MarketStore(db_path=path, read_only=True), "AEM", with_vintage=True)
    (row,) = h.vintage["income"]
    assert row["date"] == "2024-10-30" and row["_observed_at"] == "2026-09-04T15:03:34Z"
    assert row["accepted_date"] == "2024-10-31 18:33:32"
    assert h.removed == {"income": [("2024-10-30", first), ("2024-09-30", flip)],
                         "balance": [("2025-10-28", "2026-09-05T01:04:27Z"), ("2026-08-31", "2026-10-25T14:32:16Z")]}
    assert h.restored == {"income": [("2024-09-30", first), ("2024-10-30", flip)],
                          "balance": [("2025-09-30", "2026-09-05T01:04:27Z")]}
    assert h.renamed == [("2024-10-30", "2024-09-30", "2026-10-10T08:23:31Z"),
                         ("2024-10-30", "2024-09-30", "2026-10-10T08:23:32Z")]
    plain = load_history(MarketStore(db_path=path, read_only=True), "AEM", with_vintage=False)
    assert plain.restored == {} and plain.renamed == []
