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


def test_corporate_alias_requires_matching_cik_and_never_fuzzy_matches():
    aliases = load_soxx_symbol_aliases(ROOT / "config" / "soxx_symbol_aliases.json")
    # This is the FMP fund-disclosure identifier observed on both CREE and
    # WOLF rows. It is deliberately not treated as an authoritative SEC CIK.
    symbol, evidence = resolve_disclosure_symbol("CREE", "0001100663", aliases)
    assert symbol == "WOLF"
    assert evidence["raw_symbol"] == "CREE"
    assert evidence["reason"]
    with pytest.raises(ValueError):
        resolve_disclosure_symbol("CREE", "WRONG", aliases)
    symbol, evidence = resolve_disclosure_symbol("CREE INC", "0001100663", aliases)
    assert symbol == "CREE INC"
    assert evidence is None
