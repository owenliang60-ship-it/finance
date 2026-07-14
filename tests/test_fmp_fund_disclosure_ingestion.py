"""Pure normalization and date semantics for SOXX fund disclosures."""
import copy
import json
from pathlib import Path

import pytest

from src.data.fmp_forward_ingestion import (
    anchor_trading_date,
    infer_soxx_rebalance_close,
    load_soxx_symbol_aliases,
    next_trading_date,
    normalize_fund_disclosure_snapshot,
    resolve_disclosure_symbol,
)


ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
LISTING = {"NVMI.TA": "NVMI"}
GROUPS = {"GOOGL": ["GOOG"]}


def _raw_rows():
    return json.loads(
        (FIXTURES / "fmp_fund_disclosure_soxx_2025q3.json").read_text())


def _calendar():
    return [
        "2025-09-18", "2025-09-19", "2025-09-22", "2025-09-23",
        "2026-06-18", "2026-06-19", "2026-06-22", "2026-07-14",
    ]


def test_historical_disclosure_maps_into_existing_normalizer_contract():
    raw = _raw_rows()
    original = copy.deepcopy(raw)
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z", _calendar(),
        LISTING, GROUPS, {},
    )
    assert raw == original
    assert len(rows) == len(raw)
    assert [row["raw_row_index"] for row in rows] == [0, 1]
    assert rows[0]["raw_symbol"] == "ARM"
    assert rows[0]["symbol"] == "ARM"
    assert rows[0]["weight_pct"] == raw[0]["pctVal"]
    assert rows[0]["market_value"] == raw[0]["valUsd"]
    assert rows[0]["row_accepted_at"] == raw[0]["acceptedDate"]
    assert rows[0]["cik"] == raw[0]["cik"]
    assert meta["holding_date"] == "2025-09-30"
    assert meta["rebalance_close_date"] == "2025-09-19"
    assert meta["composition_effective_date"] == "2025-09-22"
    assert meta["composition_available_date"] == "2025-11-26"
    assert meta["anchor_trading_date"] == "2025-09-23"  # fixture calendar ends here
    assert meta["weight_basis"] == "fixed_rebalance_weight_proxy"
    assert meta["data_quality_tier"] == "historical_disclosure_fixed_proxy"


def test_adapter_reuses_cash_foreign_and_dual_class_rules():
    raw = [
        {"date": "2025-09-30", "acceptedDate": "2025-11-26 12:00:00",
         "symbol": "", "name": "USD CASH", "pctVal": 0.1, "valUsd": 1},
        {"date": "2025-09-30", "acceptedDate": "2025-11-26 12:00:00",
         "symbol": "NVMI.TA", "name": "NOVA", "pctVal": 1.0, "valUsd": 2},
        {"date": "2025-09-30", "acceptedDate": "2025-11-26 12:00:00",
         "symbol": "GOOG", "name": "ALPHABET C", "pctVal": 0.5, "valUsd": 3},
    ]
    rows, _ = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z", _calendar(),
        LISTING, GROUPS, {},
    )
    assert rows[0]["filter_reason"] == "cash_or_fund"
    assert rows[1]["symbol"] == "NVMI"
    assert rows[2]["included"] == 0
    assert rows[2]["covered_by"] == "GOOGL"
    assert rows[2]["weight_pct"] == 0.5


def test_disclosure_cash_fund_title_is_used_for_fail_closed_filtering():
    raw = [{
        "date": "2021-09-30", "acceptedDate": "2021-11-24 11:18:19",
        "symbol": "BISXX", "name": "BlackRock Funds III",
        "title": "BlackRock Cash Funds: Institutional, SL Agency Shares",
        "assetCat": "STIV", "isCashCollateral": "Y",
        "pctVal": 0.75, "valUsd": 54_421_973.41,
    }]
    rows, _ = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z",
        ["2021-09-17", "2021-09-20", "2021-09-30"],
        LISTING, GROUPS, {},
    )
    assert rows[0]["raw_symbol"] == "BISXX"
    assert rows[0]["symbol"] is None
    assert rows[0]["included"] == 0
    assert rows[0]["filter_reason"] == "cash_or_fund"


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("2025-03-31", "2025-03-21"),  # month starts Saturday
        ("2026-03-31", "2026-03-20"),  # month starts Sunday
        ("2026-06-30", "2026-06-19"),
        ("2026-07-14", "2026-06-19"),
        ("2026-01-02", "2025-12-19"),
    ],
)
def test_infer_soxx_rebalance_close(reference, expected):
    assert infer_soxx_rebalance_close(reference) == expected


def test_trading_date_helpers_are_calendar_driven():
    assert next_trading_date("2025-09-19", _calendar()) == "2025-09-22"
    assert anchor_trading_date("2025-09-30", _calendar()) == "2025-09-23"
    with pytest.raises(ValueError):
        next_trading_date("2026-07-14", _calendar())


def test_inconsistent_accepted_timestamps_use_max_and_warn():
    raw = _raw_rows()
    raw[0]["acceptedDate"] = "2025-11-25 10:00:00"
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z", _calendar(),
        LISTING, GROUPS, {},
    )
    assert rows[0]["row_accepted_at"] == "2025-11-25 10:00:00"
    assert meta["composition_available_date"] == "2025-11-26"
    assert "inconsistent_row_accepted_at" in meta["warnings"]


@pytest.mark.parametrize("accepted", [None, "bad-date"])
def test_missing_or_malformed_accepted_timestamp_fails_closed(accepted):
    raw = _raw_rows()
    raw[0]["acceptedDate"] = accepted
    with pytest.raises(ValueError):
        normalize_fund_disclosure_snapshot(
            "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z", _calendar(),
            LISTING, GROUPS, {},
        )


def test_live_snapshot_uses_fetch_semantics_and_weaker_quality_tier():
    raw = [{"asset": "NVDA", "name": "NVIDIA", "weightPercentage": 9.0,
            "marketValue": 100, "updatedAt": "2026-07-13"}]
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "live", "2026-07-14T02:00:00Z", _calendar(),
        LISTING, GROUPS, {},
    )
    assert rows[0]["row_accepted_at"] is None
    assert meta["holding_date"] == "2026-07-14"
    assert meta["composition_available_date"] == "2026-07-14"
    assert meta["rebalance_close_date"] == "2026-06-19"
    assert meta["composition_effective_date"] == "2026-06-22"
    assert meta["weight_basis"] == "live_snapshot_backcast_proxy"
    assert meta["data_quality_tier"] == "live_tail_weaker"


def test_non_september_membership_delta_warns_but_retains_snapshot():
    raw = [{"asset": "NVDA", "name": "NVIDIA", "weightPercentage": 9.0,
            "marketValue": 100, "updatedAt": "2026-07-13"}]
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "live", "2026-07-14T02:00:00Z", _calendar(),
        LISTING, GROUPS, {}, previous_symbols={"NVDA", "AMD"},
    )
    assert rows[0]["symbol"] == "NVDA"
    assert "non_reconstitution_membership_delta" in meta["warnings"]


def test_membership_drift_ignores_filtered_cash_but_keeps_raw_share_classes():
    raw = [
        {"asset": "GOOG", "name": "ALPHABET C", "weightPercentage": 4.0,
         "marketValue": 100, "updatedAt": "2026-07-13"},
        {"asset": "", "name": "USD CASH", "weightPercentage": 0.1,
         "marketValue": 1, "updatedAt": "2026-07-13"},
    ]
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "live", "2026-07-14T02:00:00Z", _calendar(),
        LISTING, GROUPS, {}, previous_symbols={"GOOG"},
    )
    assert rows[0]["covered_by"] == "GOOGL"
    assert "non_reconstitution_membership_delta" not in meta["warnings"]


def test_corporate_alias_requires_matching_cik_and_never_fuzzy_matches():
    aliases = load_soxx_symbol_aliases(ROOT / "config" / "soxx_symbol_aliases.json")
    # This is the FMP fund-disclosure identifier observed on both CREE and
    # WOLF rows. It is deliberately not treated as an authoritative SEC CIK.
    symbol, evidence = resolve_disclosure_symbol("CREE", "0001100663", aliases)
    assert symbol == "WOLF"
    assert evidence["raw_symbol"] == "CREE"
    assert evidence["mode"] == "fallback"
    assert evidence["reason"]
    with pytest.raises(ValueError):
        resolve_disclosure_symbol("CREE", "WRONG", aliases)
    symbol, evidence = resolve_disclosure_symbol("CREE INC", "0001100663", aliases)
    assert symbol == "CREE INC"
    assert evidence is None


def test_authoritative_vendor_symbol_correction_requires_security_identity():
    aliases = load_soxx_symbol_aliases(ROOT / "config" / "soxx_symbol_aliases.json")
    symbol, evidence = resolve_disclosure_symbol(
        "TERN", "0001100663", aliases,
        cusip="880770102", isin="US8807701029")
    assert symbol == "TER"
    assert evidence["mode"] == "authoritative"
    with pytest.raises(ValueError, match="CUSIP mismatch"):
        resolve_disclosure_symbol(
            "TERN", "0001100663", aliases,
            cusip="WRONG", isin="US8807701029")


def test_teradyne_disclosure_records_authoritative_tern_to_ter_correction():
    aliases = load_soxx_symbol_aliases(ROOT / "config" / "soxx_symbol_aliases.json")
    raw = [{
        "date": "2022-06-30", "acceptedDate": "2022-08-25 14:39:49",
        "symbol": "TERN", "name": "Teradyne Inc", "title": "Teradyne Inc",
        "pctVal": 2.07, "valUsd": 133_259_175.9, "cik": "0001100663",
        "cusip": "880770102", "isin": "US8807701029",
    }]
    rows, _ = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z",
        ["2022-06-17", "2022-06-21", "2022-06-30"],
        LISTING, GROUPS, aliases)
    assert rows[0]["symbol"] == "TERN"
    assert rows[0]["alias_symbol"] == "TER"
    assert rows[0]["alias_mode"] == "authoritative"


def test_disclosure_normalization_keeps_raw_symbol_and_records_alias_candidate():
    aliases = load_soxx_symbol_aliases(ROOT / "config" / "soxx_symbol_aliases.json")
    raw = [{
        "date": "2021-09-30", "acceptedDate": "2021-11-19 12:00:00",
        "symbol": "CREE", "name": "Cree Inc", "pctVal": 2.5,
        "valUsd": 100, "cik": "0001100663",
    }]
    rows, _ = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "disclosure", "2026-07-14T02:00:00Z",
        ["2021-09-17", "2021-09-20", "2021-09-30"],
        LISTING, GROUPS, aliases)
    assert rows[0]["symbol"] == "CREE"
    assert rows[0]["raw_symbol"] == "CREE"
    assert rows[0]["alias_symbol"] == "WOLF"
    assert "renamed" in rows[0]["alias_reason"]
