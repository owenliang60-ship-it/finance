"""Exact reviewed source quarantine; real SQLite, raw vintage and PE inputs."""
import json
from argparse import Namespace
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.data import fiscal_repair as repair
from src.data.fundamental_collector import collect_fundamentals_for_symbol
from src.data.market_store import MarketStore


RULES = json.loads((Path(__file__).parents[1] / "config/reviewed_statement_quarantines.json").read_text())
DATASETS = {"income_quarterly": "income", "balance_sheet_quarterly": "balance",
            "cash_flow_quarterly": "cashflow"}


@pytest.fixture
def store(tmp_path):
    value = MarketStore(tmp_path / "market.db")
    yield value
    value.close()


def bad(rule, period=None):
    return {**rule["expected"], "period": period or rule["allowed_periods"][-1]}


def seed(store):
    plans = []
    with store.transaction() as conn:
        for rule in RULES:
            row = bad(rule)
            store._upsert_rows_in_conn(conn, rule["table"], [row])
            store.record_vintage_in_conn(conn, "CRH", DATASETS[rule["table"]], [row],
                                        "2026-10-24T04:00:00Z", "latest_known")
            plans.append({"table": rule["table"], "symbol": "CRH", "date": rule["date"],
                          "row_hash": repair.fiscal_group_hash([row])})
    return plans


def good():
    return dict(symbol="CRH", cik="0000849395", date="2026-06-30", fiscal_year="2026",
                period="Q2", reported_currency="USD", revenue=10_000, net_income=1_000)


@pytest.mark.parametrize("rule", RULES, ids=lambda r:r["id"])
def test_exact_quarantine_accepts_only_frozen_variants(rule):
    for period in rule["allowed_periods"]:
        row = bad(rule, period)
        original = deepcopy(row)
        assert repair.reviewed_statement_quarantine(rule["table"], row) == rule["id"]
        assert row == original
    changed = bad(rule)
    changed["accepted_date"] = "2024-04-11"
    with pytest.raises(ValueError, match="quarantine source changed"):
        repair.reviewed_statement_quarantine(rule["table"], changed)
    changed["symbol"] = "OTHER"
    assert repair.reviewed_statement_quarantine(rule["table"], changed) is None


def test_quarantine_archives_exact_rows_keeps_vintage_and_recomputes(store):
    plans = seed(store)
    before = list(store._get_conn().execute("SELECT * FROM fundamental_vintage"))
    result = repair.apply_reviewed_statement_quarantine(store, plans)
    assert result["rows_quarantined"] == 5
    assert list(store._get_conn().execute("SELECT * FROM fundamental_vintage")) == before
    for rule in RULES:
        assert not store._get_conn().execute(
            "SELECT 1 FROM " + rule["table"] + " WHERE symbol='CRH' AND date=?",
            (rule["date"],)).fetchall()
    archives = store._get_conn().execute(
        "SELECT payload,context FROM fundamental_current_archive WHERE reason='reviewed_source_quarantine'").fetchall()
    assert len(archives) == 5
    assert {json.loads(r[1])["rule_id"] for r in archives} == {r["id"] for r in RULES}
    assert all(json.loads(r[0])["symbol"] == "CRH" for r in archives)
    with pytest.raises(ValueError, match="hash differs"):
        repair.apply_reviewed_statement_quarantine(store, plans)


@pytest.mark.parametrize("failure", ["stale", "duplicate", "unreviewed", "metrics"])
def test_quarantine_plan_is_atomic(store, monkeypatch, failure):
    plans = seed(store)
    if failure == "stale":
        plans[-1]["row_hash"] = "stale"
    elif failure == "duplicate":
        plans.append(plans[-1])
    elif failure == "unreviewed":
        with store.transaction() as conn:
            store._upsert_rows_in_conn(conn, "income_quarterly", [good()])
        plans.append({"table":"income_quarterly", "symbol":"CRH", "date":"2026-06-30",
                      "row_hash":repair.fiscal_group_hash(store.get_income("CRH", limit=1))})
    else:
        monkeypatch.setattr(repair, "_recompute_metrics_in_conn", Mock(side_effect=RuntimeError("metrics failed")))
    with pytest.raises((ValueError, RuntimeError)):
        repair.apply_reviewed_statement_quarantine(store, plans)
    assert store._get_conn().execute("SELECT count(*) FROM fundamental_current_archive").fetchone()[0] == 0
    assert store._get_conn().execute("SELECT count(*) FROM income_quarterly WHERE symbol='CRH' AND date<'2024'").fetchone()[0] == 3


def test_collect_preserves_raw_quarantine_and_records_warning(store):
    plans = seed(store)
    repair.apply_reviewed_statement_quarantine(store, plans)
    raw = [good(), bad(RULES[0], "Q3")]
    client = Mock()
    client.get_dataset_with_status.return_value = (raw, "ok")
    result = collect_fundamentals_for_symbol("CRH", client=client, store=store,
        observed_at="2026-10-24T08:00:00Z", dataset_keys=["income"])
    assert result == {"income":"ok"}
    assert [r["date"] for r in store.get_income("CRH", limit=0)] == ["2026-06-30"]
    vintage = store.known_as_of("CRH", "income", "2026-10-24")
    assert next(r for r in vintage if r["date"] == "2023-09-30")["net_income"] == raw[1]["net_income"]
    detail = store._get_conn().execute("SELECT detail FROM coverage_status WHERE symbol='CRH' AND dataset='income_quarterly'").fetchone()[0]
    assert "crh-income-2023-09-30" in detail


def test_all_quarantined_is_not_success(store):
    client = Mock()
    client.get_dataset_with_status.return_value = ([bad(RULES[0], "Q3")], "ok")
    assert collect_fundamentals_for_symbol("CRH", client=client, store=store,
        observed_at="2026-10-24T08:00:00Z", dataset_keys=["income"]) == {"income":"fetch_failed"}


def test_backfill_filters_memory_and_reports_exact_raw_rows(store):
    from scripts.backfill_soxx_historical_pe import BackfillState, _run_fundamentals
    state = BackfillState()
    client = Mock()
    raw = [good(), bad(RULES[0], "Q3")]
    client.get_income_statement.return_value = raw
    report = {"stages":{}}
    _run_fundamentals(Namespace(from_date="2021-10-24", to_date="2026-10-24"),
                      state, ["CRH"], client, store, report, fuse_on_incompleteness=False)
    assert state.income_by_symbol["CRH"] == [good()]
    assert report["stages"]["fundamentals"]["reviewed_quarantines"][0]["row"] == raw[1]
    assert report["stages"]["fundamentals"]["reviewed_quarantines"][0]["rule_id"] == RULES[0]["id"]


def test_offline_backfill_refuses_unrepaired_current_rows():
    from scripts.backfill_soxx_historical_pe import BackfillState, _run_fundamentals
    state = BackfillState()
    state.income_by_symbol["CRH"] = [bad(RULES[0])]
    with pytest.raises(ValueError, match="requires reviewed quarantine"):
        _run_fundamentals(Namespace(from_date="2021-10-24", to_date="2026-10-24"),
                          state, ["CRH"], None, None, {"stages":{},"planned_network_calls":[]})
