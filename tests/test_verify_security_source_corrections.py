"""Independent verifier reconstruction of evidence-scoped live corrections."""
import json
import sqlite3

import pytest

from scripts.verify_index_pe_history import _company_identities, _disclosed_membership
from tests.index_pe_identity_helpers import synthetic_lei

DAY = "2026-09-26"
KEY = (DAY, "live")


def live_row(symbol="BAD", cusip="WRONG", isin="US1234567890", weight=5.0):
    raw = {"asset": symbol, "name": "Reviewed security", "securityCusip": cusip,
           "isin": isin, "weightPercentage": weight}
    return {"basket_symbol": "SPY", "holding_date": DAY, "source_kind": "live",
            "raw_row_index": 0, "composition_effective_date": DAY,
            "composition_available_date": DAY, "raw_symbol": symbol,
            "symbol": symbol, "covered_by": None, "included": 1, "name": raw["name"],
            "cusip": cusip, "isin": isin, "issuer_lei": None, "asset_category": None,
            "cik": "FUND", "alias_mode": None, "alias_symbol": None,
            "weight_pct": weight, "raw_payload_json": json.dumps(raw)}


def correction(row, action="correct_cusip", **updates):
    return {"id": "review", "basket": row["basket_symbol"], "source_kind": "live",
            "raw_symbol": row["raw_symbol"], "raw_name": row["name"],
            "raw_cusip": row["cusip"], "raw_isin": row["isin"],
            "valid_from": DAY, "valid_to": DAY, "reviewed_at": DAY,
            "required_snapshot_dates": [DAY], "action": action, "effective_cusip": "CORRECT", "evidence": [], **updates}


def database(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fields = list(rows[0])
    conn.execute("CREATE TABLE fmp_fund_disclosure_holdings (" + ",".join(
        field + (" INTEGER" if field in {"included", "raw_row_index"} else
                 " REAL" if field in {"weight_pct", "market_value"} else " TEXT") for field in fields) + ")")
    for row in rows:
        conn.execute("INSERT INTO fmp_fund_disclosure_holdings VALUES (" +
                     ",".join("?" for _ in fields) + ")", [row.get(k) for k in fields])
    return conn


def overrides():
    return [{"isin": "US1234567890", "cusip": "CORRECT", "valid_from": DAY,
             "valid_to": DAY, "canonical_issuer_key": "sec-cik:ISSUER"}]


def test_effective_cusip_reconstructs_override_without_rewriting_source():
    row = live_row()
    with database([row]) as conn:
        assert _company_identities(conn, "SPY", overrides())[KEY]["BAD"] is None
        assert _company_identities(conn, "SPY", overrides(), [correction(row)])[KEY]["BAD"] == "sec-cik:ISSUER"
        assert dict(conn.execute("SELECT * FROM fmp_fund_disclosure_holdings").fetchone()) == row


def test_effective_cusip_can_inherit_latest_disclosure():
    live = live_row()
    lei = synthetic_lei("ISSUER")
    raw = {"symbol": "OLD", "cusip": "CORRECT", "isin": live["isin"],
           "lei": lei, "assetCat": "EC"}
    disclosure = {**live, "source_kind": "disclosure", "holding_date": "2026-06-30",
                  "composition_available_date": "2026-08-01", "raw_symbol": "OLD",
                  "symbol": "OLD", "cusip": "CORRECT", "issuer_lei": lei,
                  "asset_category": "EC", "raw_payload_json": json.dumps(raw)}
    with database([live, disclosure]) as conn:
        assert _company_identities(conn, "SPY", (), [correction(live)])[KEY]["BAD"] == "lei:" + lei


@pytest.mark.parametrize("field,value", [
    ("basket", "SOXX"), ("source_kind", "disclosure"), ("raw_symbol", "OTHER"),
    ("raw_name", "Changed name"), ("raw_cusip", "OTHER"), ("raw_isin", "OTHER"),
    ("valid_from", "2026-09-27"), ("valid_to", "2026-09-25"),
])
def test_correction_is_exact_tuple_and_date_scoped(field, value):
    row = live_row()
    scoped = correction(row, **{field: value})
    with database([row]) as conn:
        assert _company_identities(conn, "SPY", overrides(), [scoped])[KEY]["BAD"] is None


@pytest.mark.parametrize("tamper", ["normalized_cusip", "normalized_name", "normalized_isin", "normalized_market_value", "dual_cusip"])
def test_correction_cannot_hide_raw_normalized_conflicts(tamper):
    row = live_row()
    reviewed = correction(row)
    if tamper == "dual_cusip":
        raw = json.loads(row["raw_payload_json"])
        raw["cusip"] = "ANOTHER"
        row["raw_payload_json"] = json.dumps(raw)
    else:
        row[tamper.removeprefix("normalized_")] = "ANOTHER"
    with database([row]) as conn:
        assert _company_identities(conn, "SPY", overrides(), [reviewed])[KEY]["BAD"] is None
        snapshot = _disclosed_membership(conn, "SPY", [reviewed])[0]
        assert any("source_correction" in item for item in snapshot["errors"])


def test_reviewed_cvr_is_excluded_but_raw_weight_is_retained_and_checked():
    cvr = live_row("CVR", "CVR-CUSIP", "", 0.0)
    equity = {**live_row("AAA"), "raw_row_index": 1}
    reviewed = correction(cvr, "classify_cvr")
    with database([cvr, equity]) as conn:
        snapshot = _disclosed_membership(conn, "SPY", [reviewed])[0]
        assert snapshot["weights"] == {"AAA": 5.0}
        assert snapshot["errors"] == []
        assert conn.execute("SELECT COUNT(*) FROM fmp_fund_disclosure_holdings").fetchone()[0] == 2
        # A zero weight deletion evades weight totals, but a single-day review
        # is independent evidence that this exact source row was present.
        conn.execute("DELETE FROM fmp_fund_disclosure_holdings WHERE raw_symbol='CVR'")
        snapshot = _disclosed_membership(conn, "SPY", [reviewed])[0]
        assert any("source_correction_row_missing" in item for item in snapshot["errors"])


def test_cvr_bad_weight_cannot_be_hidden_by_exclusion():
    row = live_row("CVR", "CVR-CUSIP", "", -1.0)
    with database([row]) as conn:
        snapshot = _disclosed_membership(conn, "SPY", [correction(row, "classify_cvr")])[0]
        assert snapshot["weights"] == {}
        assert snapshot["errors"]


def test_overlapping_matching_reviews_fail_closed():
    row = live_row()
    records = [correction(row), correction(row, id="second", effective_cusip="ANOTHER")]
    with database([row]) as conn:
        assert _company_identities(conn, "SPY", overrides(), records)[KEY]["BAD"] is None
        assert _disclosed_membership(conn, "SPY", records)[0]["errors"]


def test_verifier_ignores_injected_effective_security():
    row = live_row()
    row["effective_security"] = json.dumps({"cusip": "CORRECT"})
    with database([row]) as conn:
        assert _company_identities(conn, "SPY", overrides())[KEY]["BAD"] is None
    row["effective_security"] = json.dumps({"cusip": "ATTACKER"})
    with database([row]) as conn:
        assert _company_identities(conn, "SPY", overrides(), [correction(row)])[KEY]["BAD"] == "sec-cik:ISSUER"


@pytest.mark.parametrize("mutation", ["duplicate", "changed_raw_identity", "weight_only"])
def test_review_anchor_detects_duplicate_or_changed_source(mutation):
    row = live_row("CVR", "CVR-CUSIP", "", 0.00000299)
    records = [correction(row, "classify_cvr")]
    changed = dict(row)
    if mutation == "changed_raw_identity":
        raw = json.loads(changed["raw_payload_json"])
        raw["name"] = changed["name"] = "UNREVIEWED"
        changed["raw_payload_json"] = json.dumps(raw)
    elif mutation == "weight_only":
        changed["weight_pct"] = 0.0
    rows = [row, {**row, "raw_row_index": 1}] if mutation == "duplicate" else [changed]
    with database(rows) as conn:
        snapshot = _disclosed_membership(conn, "SPY", records)[0]
        assert any("source_correction_" in item for item in snapshot["errors"])


def test_range_does_not_invent_cvr_presence_on_unreviewed_day():
    equity = live_row("AAA")
    equity["holding_date"] = "2026-09-25"
    cvr = live_row("CVR", "CVR-CUSIP", "", 0.0)
    reviewed = correction(cvr, "classify_cvr", valid_from="2026-09-25")
    with database([equity]) as conn:
        assert _disclosed_membership(conn, "SPY", [reviewed])[0]["errors"] == []


def test_real_frozen_source_errors_reconstruct_independently():
    from pathlib import Path
    from src.data.fund_issuer_identity import load_issuer_overrides
    from src.data.security_source_corrections import load_security_source_corrections

    root = Path(__file__).resolve().parents[1]
    records = load_security_source_corrections(root / "config" / "baskets")
    physical = json.loads((root / "tests/fixtures/index_pe_source_errors_20260926.json").read_text())
    with database(physical) as conn:
        spy = _disclosed_membership(conn, "SPY", records)[0]
        assert spy["weights"] == {} and spy["errors"] == []
        soxx = _disclosed_membership(conn, "SOXX", records)[0]
        assert soxx["weights"] == {"NXPI": 2.87325022} and soxx["errors"] == []
        identities = _company_identities(conn, "SOXX", load_issuer_overrides(root / "config" / "baskets"), records)
        assert identities[KEY]["NXPI"] == "lei:724500M9BY5293JDF951"


def test_verify_database_checks_reviewed_snapshot_before_valuation_uses_it(tmp_path):
    from pathlib import Path
    from tests.test_verify_index_pe_history import _build, _verify, _failed

    root = Path(__file__).resolve().parents[1]
    db_path = _build(tmp_path)
    cvr = json.loads((root / "tests/fixtures/index_pe_source_errors_20260926.json").read_text())[0]
    equity = {**cvr, "raw_row_index": 1, "raw_symbol": "AAA", "symbol": "AAA",
              "name": "AAA", "cusip": "AAA", "weight_pct": 100.0,
              "raw_payload_json": json.dumps({"asset": "AAA", "name": "AAA", "securityCusip": "AAA",
                                                "isin": "", "weightPercentage": 100.0})}
    with sqlite3.connect(db_path) as conn:
        for row in (cvr, equity):
            conn.execute("INSERT INTO fmp_fund_disclosure_holdings (" + ",".join(row) +
                         ") VALUES (" + ",".join("?" for _ in row) + ")", list(row.values()))
    assert _verify(db_path, root / "config/baskets")["passed"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM fmp_fund_disclosure_holdings WHERE raw_symbol='2602335D'")
    report = _verify(db_path, root / "config/baskets")
    assert not report["passed"]
    assert "source_correction_row_missing" in json.dumps(_failed(report))


def test_verify_database_rejects_expired_physical_cvr_marker_before_snapshot_use(tmp_path):
    from pathlib import Path
    from tests.test_verify_index_pe_history import _build, _verify, _failed

    root = Path(__file__).resolve().parents[1]
    db_path = _build(tmp_path)
    row = json.loads((root / "tests/fixtures/index_pe_source_errors_20260926.json").read_text())[0]
    row.update(holding_date="2026-09-27", included=0, symbol=None,
               covered_by=None, filter_reason="reviewed_cvr")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO fmp_fund_disclosure_holdings (" + ",".join(row) +
                     ") VALUES (" + ",".join("?" for _ in row) + ")", list(row.values()))
    report = _verify(db_path, root / "config/baskets")
    assert not report["passed"]
    assert "source_correction_" in json.dumps(_failed(report))
