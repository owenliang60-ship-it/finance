"""Canonical issuer keys must not confuse filer/issuer or parent/subsidiary."""
import copy
import json

import pytest

from src.data.fund_issuer_identity import load_issuer_overrides, resolve_issuer_identity, audit_snapshot_identities
from tests.test_index_pe_source_identity import EVIDENCE, normalize, reviewed_override


def cik_record(**patch):
    row = reviewed_override()
    row.pop("issuer_lei")
    row.update(canonical_issuer_key="sec-cik:0000320193", equivalent_leis=[],
               lei_search_reviewed=True, sec_role="issuer",
               sec_issuer_url="https://www.sec.gov/Archives/edgar/data/320193/test.htm")
    row.update(patch)
    return row


def load(tmp_path, records):
    (tmp_path / "issuer_identity_overrides.json").write_text(json.dumps(records))
    return load_issuer_overrides(tmp_path)


def source():
    rows, _ = normalize(["spy_AAPL"])
    row = rows[0]
    row["issuer_lei"] = row["raw_payload_json"]["lei"] = "N/A"
    return row


def test_verified_sec_issuer_cik_can_fill_no_lei(tmp_path):
    records = load(tmp_path, [cik_record()])
    row = source()
    assert resolve_issuer_identity(row, records) == ("sec-cik:0000320193", "reviewed_security")
    assert row["cik"] == "0000884394" and row["issuer_lei"] == "N/A"


@pytest.mark.parametrize("change", [
    {"canonical_issuer_key": "sec-cik:0000000000"},
    {"canonical_issuer_key": "sec-cik:320193"},
    {"sec_role": "filer"}, {"sec_issuer_url": "https://example.com/sec"},
    {"equivalent_leis": None}, {"lei_search_reviewed": False},
])
def test_cik_exception_requires_exact_typed_issuer_proof(tmp_path, change):
    with pytest.raises(ValueError):
        load(tmp_path, [cik_record(**change)])


def test_fund_filer_cik_is_never_used_as_company_fallback(tmp_path):
    rows = load(tmp_path, [cik_record(canonical_issuer_key="sec-cik:0000884394")])
    assert resolve_issuer_identity(source(), rows)[0] is None


def test_global_lei_equivalence_cannot_sneak_in_a_fund_filer_key(tmp_path):
    lei = EVIDENCE["spy_GOOG"]["raw"]["lei"]
    rows = load(tmp_path, [cik_record(canonical_issuer_key="sec-cik:0000884394", equivalent_leis=[lei])])
    other, _ = normalize(["spy_GOOG"])
    assert resolve_issuer_identity(other[0], rows)[0] is None


@pytest.mark.parametrize("missing", [None, "N/A", "000000000"])
def test_explicit_isin_match_accepts_only_reviewed_missing_cusip_values(tmp_path, missing):
    rows = load(tmp_path, [cik_record(match_mode="isin_allow_missing_cusip", missing_cusip_values=[missing])])
    row = source()
    row["cusip"] = row["raw_payload_json"]["cusip"] = missing
    assert resolve_issuer_identity(row, rows)[0] == "sec-cik:0000320193"
    row["cusip"] = row["raw_payload_json"]["cusip"] = "111111111"
    assert resolve_issuer_identity(row, rows)[0] is None


def test_isin_only_mode_cannot_create_a_cusip_wildcard(tmp_path):
    with pytest.raises(ValueError):
        load(tmp_path, [cik_record(match_mode="isin_allow_missing_cusip", missing_cusip_values=["111111111"])])


def test_same_issuer_two_id_types_have_one_key_and_still_trip_duplicate_gate(tmp_path):
    lei = EVIDENCE["spy_GOOG"]["raw"]["lei"]
    rows, _ = normalize(["spy_GOOG", "spy_GOOGL"])
    original = rows[0]
    entry = cik_record(canonical_issuer_key="sec-cik:0001652044", equivalent_leis=[lei],
                       cusip=original["cusip"], isin=original["isin"])
    records = load(tmp_path, [entry])
    original["issuer_lei"] = original["raw_payload_json"]["lei"] = "N/A"
    assert resolve_issuer_identity(rows[0], records)[0] == resolve_issuer_identity(rows[1], records)[0]
    assert any("duplicate_issuer" in e for e in audit_snapshot_identities(rows, records)["errors"])


def test_scoped_wrong_lei_correction_does_not_alias_a_different_entity(tmp_path):
    child_lei = EVIDENCE["spy_GOOG"]["raw"]["lei"]
    records = load(tmp_path, [cik_record(expected_raw_leis=[child_lei])])
    row = source()
    row["issuer_lei"] = row["raw_payload_json"]["lei"] = child_lei
    assert resolve_issuer_identity(row, records) == ("sec-cik:0000320193", "reviewed_security_correction")
    other, _ = normalize(["spy_GOOG"])
    assert resolve_issuer_identity(other[0], records)[0] == "lei:" + child_lei
    row["issuer_lei"] = row["raw_payload_json"]["lei"] = EVIDENCE["spy_DISCA"]["raw"]["lei"]
    assert resolve_issuer_identity(row, records)[0] is None
    row["holding_date"] = "2022-01-01"
    assert resolve_issuer_identity(row, records)[0] != "sec-cik:0000320193"


def test_same_lei_cannot_be_asserted_as_two_companies_in_overlapping_ranges(tmp_path):
    lei = EVIDENCE["spy_AAPL"]["raw"]["lei"]
    a = cik_record(equivalent_leis=[lei])
    b = {**a, "canonical_issuer_key": "sec-cik:0001652044"}
    with pytest.raises(ValueError):
        load(tmp_path, [a, b])


def test_subsidiary_lei_cannot_be_both_equivalent_and_wrong(tmp_path):
    lei = EVIDENCE["spy_GOOG"]["raw"]["lei"]
    with pytest.raises(ValueError):
        load(tmp_path, [cik_record(equivalent_leis=[lei], expected_raw_leis=[lei])])


@pytest.mark.parametrize("mode", ["missing_lei", "missing_cusip", "wrong_subsidiary"])
def test_independent_verifier_rebuilds_canonical_key_for_all_evidence_modes(tmp_path, mode):
    from src.data.market_store import MarketStore
    from scripts.verify_index_pe_history import _company_identities
    rows, meta = normalize(["spy_AAPL"])
    row = rows[0]
    row["issuer_lei"] = row["raw_payload_json"]["lei"] = "N/A"
    record = cik_record()
    if mode == "missing_cusip":
        row["cusip"] = row["raw_payload_json"]["cusip"] = "000000000"
        record.update(match_mode="isin_allow_missing_cusip", missing_cusip_values=["000000000"])
    elif mode == "wrong_subsidiary":
        wrong = EVIDENCE["spy_GOOG"]["raw"]["lei"]
        row["issuer_lei"] = row["raw_payload_json"]["lei"] = wrong
        record["expected_raw_leis"] = [wrong]
    records = load(tmp_path, [record])
    store = MarketStore(tmp_path / "market.db")
    try:
        store.replace_fund_disclosure_snapshot("SPY", meta["holding_date"], "disclosure", rows,
            **{k: meta[k] for k in ("rebalance_close_date", "composition_effective_date",
                                    "composition_available_date", "fetched_at")})
        found = _company_identities(store._get_conn(), "SPY", records)
        assert found[(meta["holding_date"], "disclosure")]["AAPL"] == "sec-cik:0000320193"
        assert resolve_issuer_identity(row, records)[0] == "sec-cik:0000320193"
    finally:
        store.close()
