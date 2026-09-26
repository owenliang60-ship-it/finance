"""Independent PIT correction evidence, without running the valuation builder."""
import json
import sqlite3
from pathlib import Path

import pytest

from src.data.market_store import MarketStore
from terminal.forward_source_verifier import verify_pit_security_corrections

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/baskets"
DAY = "2026-09-26"


@pytest.fixture
def case(tmp_path):
    source = json.loads((ROOT / "tests/fixtures/index_pe_source_errors_20260926.json").read_text())[0]
    raw = json.loads(source["raw_payload_json"])
    store = MarketStore(tmp_path / "market.db")
    store.replace_fund_disclosure_snapshot("SPY", DAY, "live", [source],
        rebalance_close_date=source["rebalance_close_date"],
        composition_effective_date=source["composition_effective_date"],
        composition_available_date=source["composition_available_date"], fetched_at=source["fetched_at"])
    conn = store._get_conn()
    holding = {"basket": "SPY", "snapshot_date": DAY, "raw_row_index": 0,
               "raw_asset": raw["asset"], "symbol": raw["asset"], "name": raw["name"],
               "weight_pct": raw["weightPercentage"], "market_value": raw["marketValue"],
               "updated_at": raw["updatedAt"], "included": 1}
    conn.execute("INSERT INTO fmp_etf_holdings_snapshot (" + ",".join(holding) +
                 ") VALUES (" + ",".join("?" for _ in holding) + ")", list(holding.values()))
    rows = [{"basket": "SPY", "snapshot_date": DAY, "members_json": {
        "members": [{"symbol": "AAA", "weight_pct": 99.0}],
        "non_equity_exclusions": [{"raw_asset": raw["asset"], "name": raw["name"],
            "weight_pct": raw["weightPercentage"], "valuation_filter_reason": "reviewed_cvr",
            "correction_id": "spy-hologic-cvr-20260926"}]}}]
    conn.commit()
    yield conn, source, rows
    store.close()


def check(case):
    return verify_pit_security_corrections(case[0], DAY, case[2], CONFIG)


def test_real_source_and_exclusion_pass_without_writes(case):
    before = case[0].total_changes
    assert check(case) == []
    assert case[0].total_changes == before
    case[2][0]["members_json"] = json.dumps(case[2][0]["members_json"])
    assert check(case) == []


@pytest.mark.parametrize("table", ["fmp_fund_disclosure_holdings", "fmp_etf_holdings_snapshot"])
@pytest.mark.parametrize("mutation", ["delete", "duplicate"])
def test_source_must_be_unique_and_present(case, table, mutation):
    conn = case[0]
    if mutation == "delete":
        conn.execute(f"DELETE FROM {table}")
    else:
        original = dict(conn.execute(f"SELECT * FROM {table}").fetchone())
        original["raw_row_index"] += 1
        conn.execute(f"INSERT INTO {table} (" + ",".join(original) + ") VALUES (" +
                     ",".join("?" for _ in original) + ")", list(original.values()))
    assert check(case)


@pytest.mark.parametrize("field,value", [
    ("raw_symbol", "WRONG"), ("name", "WRONG"), ("cusip", "WRONG"), ("isin", None),
    ("issuer_lei", "FORGED"), ("asset_category", "EC"), ("weight_pct", 0.0),
    ("market_value", 0.0), ("composition_available_date", "2026-09-27"),
    ("composition_available_date", "invalid"), ("fetched_at", "2026-09-27T00:00:00Z"),
    ("fetched_at", "invalid"),
    ("fetched_at", "2026-09-26T12:00:00"),
])
def test_physical_fields_and_pit_availability_are_verified(case, field, value):
    case[0].execute(f"UPDATE fmp_fund_disclosure_holdings SET {field}=?", [value])
    assert check(case)


@pytest.mark.parametrize("field,value", [
    ("symbol", "SOXX"), ("asset", "WRONG"), ("name", "WRONG"), ("securityCusip", "WRONG"),
    ("cusip", "CONFLICT"), ("isin", None), ("weightPercentage", 0.0), ("marketValue", 0.0),
])
def test_raw_identity_and_numeric_fields_cannot_be_forged(case, field, value):
    raw = json.loads(case[1]["raw_payload_json"])
    raw[field] = value
    case[0].execute("UPDATE fmp_fund_disclosure_holdings SET raw_payload_json=?", [json.dumps(raw)])
    assert check(case)


@pytest.mark.parametrize("field,value", [
    ("raw_asset", "WRONG"), ("name", "WRONG"), ("weight_pct", 0.0),
    ("market_value", 0.0), ("updated_at", "2026-09-24 00:00:00"),
])
def test_identifierless_pit_row_requires_exact_source_join(case, field, value):
    case[0].execute(f"UPDATE fmp_etf_holdings_snapshot SET {field}=?", [value])
    assert check(case)


@pytest.mark.parametrize("mutation", ["delete", "duplicate", "wrong_id", "wrong_asset", "wrong_name", "wrong_weight", "wrong_reason", "forged_extra"])
def test_published_exclusion_matches_review_exactly_once(case, mutation):
    exclusions = case[2][0]["members_json"]["non_equity_exclusions"]
    if mutation == "delete":
        exclusions.clear()
    elif mutation == "duplicate":
        exclusions.append(dict(exclusions[0]))
    elif mutation == "forged_extra":
        exclusions.append({**exclusions[0], "correction_id": "forged", "raw_asset": "AAA"})
    else:
        field = {"wrong_id": "correction_id", "wrong_asset": "raw_asset", "wrong_name": "name",
                 "wrong_weight": "weight_pct", "wrong_reason": "valuation_filter_reason"}[mutation]
        exclusions[0][field] = 0.0 if field == "weight_pct" else "WRONG"
    assert check(case)


@pytest.mark.parametrize("member", [{"symbol": "2602335D"}, {"raw_asset": "2602335D", "symbol": "TPG"},
                                    {"symbol": "unmapped:0:2602335D"}])
def test_cvr_cannot_remain_an_equity_member(case, member):
    case[2][0]["members_json"]["members"].append(member)
    assert check(case)


def test_historical_no_review_does_not_require_evidence_tables(tmp_path):
    with sqlite3.connect(":memory:") as conn:
        rows = [{"basket": "SPY", "members_json": {"members": [{"symbol": "AAA"}]}}]
        assert verify_pit_security_corrections(conn, "2026-09-19", rows, CONFIG) == []
        rows[0]["members_json"]["non_equity_exclusions"] = [
            {"raw_asset": "AAA", "valuation_filter_reason": "reviewed_cvr", "correction_id": "forged"}]
        assert verify_pit_security_corrections(conn, "2026-09-19", rows, CONFIG)


@pytest.mark.parametrize("mutation", ["missing_product", "duplicate_product", "wrong_snapshot", "malformed_payload", "malformed_exclusion_id"])
def test_product_structure_cannot_hide_review(case, mutation):
    rows = case[2]
    if mutation == "missing_product":
        rows.clear()
    elif mutation == "duplicate_product":
        rows.append(dict(rows[0]))
    elif mutation == "wrong_snapshot":
        rows[0]["snapshot_date"] = "2026-09-25"
    elif mutation == "malformed_payload":
        rows[0]["members_json"] = "not-json"
    else:
        rows[0]["members_json"]["non_equity_exclusions"][0]["correction_id"] = []
    assert check(case)


def test_fetched_date_is_compared_in_utc(case):
    case[0].execute("UPDATE fmp_fund_disclosure_holdings SET fetched_at=?",
                    ["2026-09-27T01:00:00+08:00"])
    assert check(case) == []
    case[0].execute("UPDATE fmp_fund_disclosure_holdings SET fetched_at=?",
                    ["2026-09-26T01:00:00+08:00"])
    assert check(case)
