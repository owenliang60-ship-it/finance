"""Fiscal alias repair uses temporary databases and the real metrics engine."""
import json

import pytest

from src.data.fundamental_collector import collect_fundamentals_for_symbol
from src.data.market_store import MarketStore


TABLES = {
    "income_quarterly": ("income", "upsert_income", "revenue"),
    "balance_sheet_quarterly": ("balance", "upsert_balance_sheet", "totalAssets"),
    "cash_flow_quarterly": ("cashflow", "upsert_cash_flow", "freeCashFlow"),
}


@pytest.fixture
def store(tmp_path):
    result = MarketStore(tmp_path / "market.db")
    yield result
    result.close()


def row(date="2025-09-30", **extra):
    return dict(date=date, fiscalYear="2026", period="Q1",
                reportedCurrency="USD", cik="0001", **extra)


def seed(store, table, rows, symbol="TEST"):
    # Deliberately bypass the new writer to reproduce pre-fix production rows.
    prepared = store._prepare_upsert_rows(table, symbol, rows)
    with store.transaction() as conn:
        store._upsert_rows_in_conn(conn, table, prepared)


def read(store, table, symbol="TEST"):
    return store._get_rows(table, symbol, limit=0)


class Client:
    def __init__(self, rows):
        self.rows = rows

    def get_dataset_with_status(self, *args, **kwargs):
        return self.rows, "ok"


def write(store, table, rows, entry):
    dataset, legacy, _ = TABLES[table]
    if entry == "legacy":
        return getattr(store, legacy)("TEST", rows)
    return collect_fundamentals_for_symbol(
        "TEST", client=Client(rows), store=store,
        observed_at="2026-09-05T01:02:03Z", dataset_keys=[dataset])


def mapping(store, table="income_quarterly", keep="2025-10-03"):
    from src.data.fiscal_repair import fiscal_group_hash, fiscal_group_rows
    return dict(table=table, symbol="TEST", fiscal_year="2026", period="Q1",
                keep_date=keep, group_hash=fiscal_group_hash(
                    fiscal_group_rows(store, table, "TEST", "2026", "Q1")))


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("entry", ["legacy", "collector"])
def test_equivalent_alias_replaces_only_covered_quarter(store, table, entry):
    _, _, field = TABLES[table]
    seed(store, table, [row(**{field: 100})])
    old = dict(row("2024-09-30", **{field: 50}), fiscalYear="2025")
    seed(store, table, [old])
    seed(store, table, [row(**{field: 99})], symbol="OTHER")
    result = write(store, table, [row("2025-10-03", filingDate="2025-11-01",
                                     **{field: 100})], entry)
    assert result == (1 if entry == "legacy" else {TABLES[table][0]: "ok"})
    assert [r["date"] for r in read(store, table)] == ["2025-10-03", "2024-09-30"]
    assert read(store, table, "OTHER")[0]["date"] == "2025-09-30"
    archived = store._get_conn().execute(
        "SELECT payload FROM fundamental_current_archive WHERE source_table=?", (table,)).fetchall()
    assert len(archived) == 1
    assert json.loads(archived[0][0])["date"] == "2025-09-30"


@pytest.mark.parametrize("entry", ["legacy", "collector"])
@pytest.mark.parametrize("change", [{"revenue": 101}, {"reportedCurrency": "EUR"},
                                     {"cik": "0002"}, {"netIncome": 7}])
def test_conflicting_existing_alias_cannot_be_silently_overwritten(store, entry, change):
    seed(store, "income_quarterly", [row(revenue=100)])
    incoming = dict(row("2025-10-03", revenue=100), **change)
    if entry == "legacy":
        with pytest.raises(ValueError, match="conflict"):
            write(store, "income_quarterly", [incoming], entry)
    else:
        assert write(store, "income_quarterly", [incoming], entry) == {"income": "fetch_failed"}
    assert read(store, "income_quarterly")[0]["revenue"] == 100
    assert len(read(store, "income_quarterly")) == 1


@pytest.mark.parametrize("rows", [
    [row(revenue=100), row("2025-10-03", revenue=100)],
    [row(revenue=100), row(revenue=101)],
    [dict(row(revenue=100), fiscalYear="2026.5")],
])
def test_ambiguous_incoming_batch_rejected_before_any_mutation(store, rows):
    with pytest.raises(ValueError):
        write(store, "income_quarterly", rows, "legacy")
    assert read(store, "income_quarterly") == []


def test_unknown_fiscal_key_cannot_delete_and_identical_duplicate_is_idempotent(store):
    seed(store, "income_quarterly", [row(revenue=100)])
    store.upsert_income("TEST", [{"date": "2025-10-03", "revenue": 100}])
    assert len(read(store, "income_quarterly")) == 2
    store.upsert_income("CLEAN", [row(revenue=50), row(revenue=50)])
    assert len(read(store, "income_quarterly", "CLEAN")) == 1


@pytest.mark.parametrize("failure", ["record_vintage_in_conn", "_upsert_coverage_status_in_conn"])
def test_collector_failure_restores_deleted_alias_and_metrics(store, monkeypatch, failure):
    seed(store, "income_quarterly", [row(revenue=100)])
    store.upsert_metrics("TEST", [{"date": "2025-09-30", "net_margin": 0.77}])
    before = read(store, "metrics_quarterly")
    real = getattr(store, failure)
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("injected failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, failure, fail_once)
    assert write(store, "income_quarterly", [row("2025-10-03", revenue=100)],
                 "collector") == {"income": "fetch_failed"}
    assert read(store, "income_quarterly")[0]["date"] == "2025-09-30"
    assert read(store, "metrics_quarterly") == before
    assert store._get_conn().execute("SELECT count(*) FROM fundamental_current_archive").fetchone()[0] == 0
    assert store.known_as_of("TEST", "income", "2026-09-05") == []


def test_automatic_alias_recomputes_metrics_without_legacy_nested_commit(store):
    seed(store, "income_quarterly", [row(revenue=100, netIncome=10),
        dict(row("2025-06-30", revenue=80, netIncome=8), fiscalYear="2025", period="Q4")])
    store.upsert_metrics("TEST", [{"date": "2025-09-30", "net_margin": 9}])
    write(store, "income_quarterly", [row("2025-10-03", revenue=100, netIncome=10)], "legacy")
    metrics = read(store, "metrics_quarterly")
    assert [r["date"] for r in metrics] == ["2025-10-03", "2025-06-30"]
    assert metrics[0]["revenue_growth_qoq"] == pytest.approx(0.25)
    assert metrics[0]["net_margin"] == pytest.approx(0.1)


def test_manual_repair_hash_guard_history_archive_vintage_and_replay(store):
    from src.data.fiscal_repair import apply_fiscal_repairs
    history = [dict(row(f"{year}-09-30", revenue=100 + year, netIncome=20),
                    fiscalYear=str(year), period="Q4") for year in range(2000, 2025)]
    seed(store, "income_quarterly", history + [row(revenue=100), row("2025-10-03", revenue=200)])
    store.upsert_metrics("TEST", [{"date": "2025-09-30", "net_margin": 0.99}])
    store.record_vintage("TEST", "income", [row(revenue=100)],
                         "2026-09-04T00:00:00Z", "latest_known")
    vintage = store.known_as_of("TEST", "income", "2026-09-05")
    plan = mapping(store)
    result = apply_fiscal_repairs(store, [plan])
    assert result["groups_repaired"] == result["rows_removed"] == 1
    assert len(read(store, "income_quarterly")) == 26
    assert len(read(store, "metrics_quarterly")) == 26  # No silent limit=20 truncation.
    assert "2025-09-30" not in {r["date"] for r in read(store, "metrics_quarterly")}
    assert store.known_as_of("TEST", "income", "2026-09-05") == vintage
    n = store._get_conn().execute("SELECT count(*) FROM fundamental_current_archive").fetchone()[0]
    assert n == 2  # Removed source row plus replaced metric row.
    assert apply_fiscal_repairs(store, [plan])["groups_unchanged"] == 1
    assert store._get_conn().execute("SELECT count(*) FROM fundamental_current_archive").fetchone()[0] == n
    with pytest.raises(ValueError, match="hash"):
        apply_fiscal_repairs(store, [dict(plan, group_hash="0" * 64)])


def test_manual_validates_every_mapping_before_modifying_any_group(store):
    from src.data.fiscal_repair import apply_fiscal_repairs
    for table in ("income_quarterly", "cash_flow_quarterly"):
        seed(store, table, [row(), row("2025-10-03")])
    before = read(store, "income_quarterly")
    with pytest.raises(ValueError, match="hash"):
        apply_fiscal_repairs(store, [mapping(store),
            dict(mapping(store, "cash_flow_quarterly"), group_hash="0" * 64)])
    assert read(store, "income_quarterly") == before


@pytest.mark.parametrize("entry", ["manual", "automatic"])
def test_metrics_failure_rolls_back_all_current_and_archive_changes(store, monkeypatch, entry):
    from src.data.fiscal_repair import apply_fiscal_repairs
    import src.data.metrics_calculator as mc
    rows = [row(revenue=100)]
    if entry == "manual":
        rows.append(row("2025-10-03", revenue=100))
    seed(store, "income_quarterly", rows)
    store.upsert_metrics("TEST", [{"date": "2025-09-30", "net_margin": 0.99}])
    before = read(store, "income_quarterly"), read(store, "metrics_quarterly")

    def broken(*args, **kwargs):
        raise RuntimeError("metrics failed")

    monkeypatch.setattr(mc, "compute_metrics", broken)
    with pytest.raises(RuntimeError, match="metrics failed"):
        if entry == "manual":
            apply_fiscal_repairs(store, [mapping(store)])
        else:
            write(store, "income_quarterly", [row("2025-10-03", revenue=100)], "legacy")
    assert (read(store, "income_quarterly"), read(store, "metrics_quarterly")) == before
    assert store._get_conn().execute("SELECT count(*) FROM fundamental_current_archive").fetchone()[0] == 0


@pytest.mark.parametrize("table", TABLES)
def test_manual_repair_supports_each_statement_with_different_financial_values(store, table):
    from src.data.fiscal_repair import apply_fiscal_repairs
    field = TABLES[table][2]
    seed(store, table, [row(**{field: 100}), row("2025-10-03", **{field: 200})])
    original = read(store, table)[1]
    result = apply_fiscal_repairs(store, [mapping(store, table)])
    assert result["rows_removed"] == 1
    assert [r["date"] for r in read(store, table)] == ["2025-10-03"]
    archived = store._get_conn().execute(
        "SELECT payload FROM fundamental_current_archive WHERE source_table=?", (table,)).fetchone()
    assert json.loads(archived[0]) == original


@pytest.mark.parametrize("defect", ["duplicate", "keep_date", "table"])
def test_invalid_repair_mappings_never_mutate_sources(store, defect):
    from src.data.fiscal_repair import apply_fiscal_repairs
    seed(store, "income_quarterly", [row(revenue=100), row("2025-10-03", revenue=101)])
    before = read(store, "income_quarterly")
    plan = mapping(store)
    if defect == "keep_date":
        plan["keep_date"] = "2025-10-04"
    elif defect == "table":
        plan["table"] = "daily_price"
    with pytest.raises(ValueError):
        apply_fiscal_repairs(store, [plan, plan] if defect == "duplicate" else [plan])
    assert read(store, "income_quarterly") == before


def test_replay_rejects_subsequently_changed_survivor(store):
    from src.data.fiscal_repair import apply_fiscal_repairs
    seed(store, "income_quarterly", [row(revenue=100), row("2025-10-03", revenue=101)])
    plan = mapping(store)
    apply_fiscal_repairs(store, [plan])
    seed(store, "income_quarterly", [row("2025-10-03", revenue=200)])
    with pytest.raises(ValueError, match="hash"):
        apply_fiscal_repairs(store, [plan])


def test_metrics_insert_failure_after_delete_rolls_back_every_mapping(store, monkeypatch):
    from src.data.fiscal_repair import apply_fiscal_repairs
    for table in ("income_quarterly", "balance_sheet_quarterly"):
        seed(store, table, [row(), row("2025-10-03")])
    store.upsert_metrics("TEST", [{"date": "2025-09-30", "net_margin": 0.99}])
    before = {table: read(store, table) for table in
              ("income_quarterly", "balance_sheet_quarterly", "metrics_quarterly")}
    real = store._upsert_rows_in_conn

    def broken(conn, table, rows):
        result = real(conn, table, rows)
        if table == "metrics_quarterly":
            raise RuntimeError("after metrics INSERT")
        return result

    monkeypatch.setattr(store, "_upsert_rows_in_conn", broken)
    with pytest.raises(RuntimeError, match="after metrics INSERT"):
        apply_fiscal_repairs(store, [mapping(store), mapping(store, "balance_sheet_quarterly")])
    assert {table: read(store, table) for table in before} == before
    assert store._get_conn().execute("SELECT count(*) FROM fundamental_current_archive").fetchone()[0] == 0


def test_same_date_restatement_keeps_current_and_both_vintages(store):
    for stamp, revenue in (("2026-09-04T12:00:00Z", 100), ("2026-09-05T12:00:00Z", 101)):
        assert collect_fundamentals_for_symbol(
            "TEST", client=Client([row(revenue=revenue)]), store=store,
            observed_at=stamp, dataset_keys=["income"]) == {"income": "ok"}
    assert read(store, "income_quarterly")[0]["revenue"] == 101
    assert store.known_as_of("TEST", "income", "2026-09-04")[0]["revenue"] == 100
    assert store.known_as_of("TEST", "income", "2026-09-05")[0]["revenue"] == 101
