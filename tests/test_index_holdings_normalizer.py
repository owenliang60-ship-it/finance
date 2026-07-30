"""Generalized *historical disclosure* normalizer and date semantics.

Covers `normalize_fund_disclosure_snapshot` / `infer_basket_rebalance_close` /
`basket_history_gap` in `src/data/fmp_forward_ingestion.py` -- the SEC
N-PORT-style **quarterly fund disclosure** adapter (vendor rows carry `date`
+ `acceptedDate`) used to backfill five years of SPY / QQQ / SOXX history.

This is a *different* normalizer from `normalize_holdings`, which adapts the
**live ETF holdings snapshot** (vendor rows carry `asset`/`updatedAt`, no
disclosure lag) and is covered by `tests/test_fmp_forward_ingestion.py`.
`normalize_fund_disclosure_snapshot` internally reuses `normalize_holdings`
for the cash/foreign/dual-class filtering rules once it has adapted either
disclosure or live rows into that shared shape -- see
`tests/test_fmp_fund_disclosure_ingestion.py` for the original SOXX-only
coverage of that shared contract, still exercised unchanged by this branch.
"""
from pathlib import Path

import pytest

from src.data.fmp_forward_ingestion import (
    basket_history_gap,
    infer_basket_rebalance_close,
    infer_soxx_rebalance_close,
    load_index_pe_basket_configs,
    normalize_fund_disclosure_snapshot,
)


CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"
LISTING = {"NVMI.TA": "NVMI"}
GROUPS = {"GOOGL": ["GOOG"]}
CALENDAR = [
    "2025-09-18", "2025-09-19", "2025-09-22", "2025-09-23",
    "2025-12-18", "2025-12-19", "2025-12-22",
    "2026-06-18", "2026-06-19", "2026-06-22", "2026-07-14",
]


def _configs():
    return load_index_pe_basket_configs(CONFIG_DIR)


# ---------------------------------------------------------------------------
# RED 2: three-date-semantics round trip across all three baskets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("basket", ["SPY", "QQQ", "SOXX"])
def test_three_date_semantics_round_trip_across_baskets(basket):
    configs = _configs()
    entry = configs[basket]
    raw = [{"asset": "NVDA", "name": "NVIDIA", "weightPercentage": 9.0,
            "marketValue": 100, "updatedAt": "2026-07-13"}]
    rows, meta = normalize_fund_disclosure_snapshot(
        basket, raw, "live", "2026-07-14T02:00:00Z", CALENDAR,
        LISTING, GROUPS, {},
        rebalance_months=entry["rebalance_months"],
        expected_reconstitution_month=entry["expected_reconstitution_month"],
    )
    assert rows  # sanity: adapter produced a row
    # Ordering contract: close <= effective <= available.
    assert meta["rebalance_close_date"] <= meta["composition_effective_date"]
    assert meta["composition_effective_date"] <= meta["composition_available_date"]
    # composition_effective_date must itself be a real trading date.
    assert meta["composition_effective_date"] in CALENDAR
    # Round trip: re-deriving the rebalance close from the reference date
    # used to compute it (the holding date) is idempotent.
    again = infer_basket_rebalance_close(
        meta["holding_date"], entry["rebalance_months"])
    assert again == meta["rebalance_close_date"]


# ---------------------------------------------------------------------------
# RED 3: empty disclosure fails closed regardless of basket
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("basket", ["SPY", "QQQ", "SOXX"])
def test_empty_disclosure_fails_closed_for_any_basket(basket):
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_fund_disclosure_snapshot(
            basket, [], "disclosure", "2026-07-14T02:00:00Z", CALENDAR,
            LISTING, GROUPS, {})


# ---------------------------------------------------------------------------
# RED 4: SOXX pre-2021-09 returns a gap, never fabricated data
# ---------------------------------------------------------------------------

def test_soxx_pre_2021_09_reports_history_gap():
    configs = _configs()
    assert basket_history_gap("SOXX", "2021-08-31", configs) is True
    assert basket_history_gap("SOXX", "2021-09-01", configs) is False
    assert basket_history_gap("SOXX", "2025-01-01", configs) is False


def test_baskets_without_a_declared_boundary_never_report_a_gap():
    # SPY/QQQ have no audited disclosure-availability boundary yet (that is
    # an empirical finding for Task 4 to make against real FMP responses,
    # not something to assume here) -- absence of a fact is not evidence of
    # a gap.
    configs = _configs()
    assert basket_history_gap("SPY", "2001-01-01", configs) is False
    assert basket_history_gap("QQQ", "2001-01-01", configs) is False


def test_basket_history_gap_rejects_unknown_basket():
    configs = _configs()
    with pytest.raises(ValueError, match="unknown basket"):
        basket_history_gap("DIA", "2020-01-01", configs)


# ---------------------------------------------------------------------------
# RED 5: SPY off-cycle member delta writes a warning
# ---------------------------------------------------------------------------

def test_spy_has_no_exempt_month_so_any_member_delta_warns():
    configs = _configs()
    entry = configs["SPY"]
    raw = [{"asset": "NVDA", "name": "NVIDIA", "weightPercentage": 9.0,
            "marketValue": 100, "updatedAt": "2026-07-13"}]
    rows, meta = normalize_fund_disclosure_snapshot(
        "SPY", raw, "live", "2026-07-14T02:00:00Z", CALENDAR,
        LISTING, GROUPS, {}, previous_symbols={"NVDA", "AMD"},
        rebalance_months=entry["rebalance_months"],
        expected_reconstitution_month=entry["expected_reconstitution_month"],
    )
    assert rows[0]["symbol"] == "NVDA"
    assert "non_reconstitution_membership_delta" in meta["warnings"]


def test_soxx_still_exempts_september_after_generalization():
    # Regression guard: the SOXX-specific exemption must survive the
    # generalization unchanged.
    configs = _configs()
    entry = configs["SOXX"]
    raw = [{"asset": "NVDA", "name": "NVIDIA", "weightPercentage": 9.0,
            "marketValue": 100, "updatedAt": "2025-09-20"}]
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "live", "2025-09-20T02:00:00Z", CALENDAR,
        LISTING, GROUPS, {}, previous_symbols={"NVDA", "AMD"},
        rebalance_months=entry["rebalance_months"],
        expected_reconstitution_month=entry["expected_reconstitution_month"],
    )
    assert meta["rebalance_close_date"].endswith("-09-19")
    assert "non_reconstitution_membership_delta" not in meta["warnings"]


# ---------------------------------------------------------------------------
# RED 6: old SOXX CLI/wrapper still calls the now-general logic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "reference",
    ["2025-03-31", "2026-03-31", "2026-06-30", "2026-07-14", "2026-01-02"],
)
def test_infer_soxx_rebalance_close_wrapper_delegates_to_general_function(
        reference):
    configs = _configs()
    months = configs["SOXX"]["rebalance_months"]
    assert infer_soxx_rebalance_close(reference) == (
        infer_basket_rebalance_close(reference, months))


def test_normalize_fund_disclosure_snapshot_soxx_defaults_are_unchanged():
    # Calling with no rebalance_months / expected_reconstitution_month
    # keyword args (i.e. exactly how the existing SOXX backfill script and
    # tests/test_fmp_fund_disclosure_ingestion.py already call this
    # function) must still resolve to SOXX's exact historical behavior.
    raw = [{"asset": "NVDA", "name": "NVIDIA", "weightPercentage": 9.0,
            "marketValue": 100, "updatedAt": "2026-07-13"}]
    rows, meta = normalize_fund_disclosure_snapshot(
        "SOXX", raw, "live", "2026-07-14T02:00:00Z", CALENDAR,
        LISTING, GROUPS, {}, previous_symbols={"NVDA", "AMD"})
    assert meta["rebalance_close_date"] == "2026-06-19"
    # July is not September -> still warns under the untouched default.
    assert "non_reconstitution_membership_delta" in meta["warnings"]
