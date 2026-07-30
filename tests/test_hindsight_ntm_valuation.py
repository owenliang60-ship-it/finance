"""Ex-post ("hindsight") NTM GAAP basket earnings tests.

These tests pin the semantics that separate a hindsight NTM number from a
point-in-time forward P/E. The historical TTM engine
(``tests/test_historical_basket_valuation.py``) covers the opposite
convention: ``accepted_date <= valuation_date`` visibility gating. Here the
actuals are deliberately read ex-post, so a test that asserts "no
accepted-date gate" is asserting the contract, not a missing guard.
"""
from datetime import date, timedelta

import pytest

from terminal.historical_basket_valuation import (
    select_asof_fx,
    select_four_continuous_asof_quarters,
)
from terminal.hindsight_ntm_valuation import (
    QUALITY_TIER_ACTUAL_ONLY,
    QUALITY_TIER_LATEST_CONSENSUS_TAIL,
    QUALITY_TIER_UNPUBLISHABLE,
    compute_hindsight_basket_aggregate,
    compute_hindsight_ntm_valuation,
    compute_member_hindsight_ntm_income_usd,
    is_same_fiscal_quarter,
    select_latest_allowed_snapshot,
    select_next_four_hindsight_quarters,
)


LATEST_SNAPSHOT = "2026-07-25"
OLDER_SNAPSHOT = "2026-01-10"

COMPOSITION = {
    "composition_effective_date": "2025-12-19",
    "composition_available_date": "2025-12-22",
}


def _calendar(start="2024-01-01", end="2029-12-31"):
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    values = []
    while current <= final:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _actual(fiscal, income, *, period="Q1", currency="USD", accepted=None,
            symbol="AAA"):
    return {
        "symbol": symbol, "date": fiscal, "period": period,
        "reported_currency": currency, "net_income": income,
        "accepted_date": accepted or f"{fiscal} 16:00:00",
    }


def _actual_year(symbol="AAA", currency="USD", income=25.0, year=2026,
                 accepted_offset_days=25):
    """Four calendar quarters of a Dec-fiscal-year filer."""
    ends = [f"{year}-03-31", f"{year}-06-30", f"{year}-09-30", f"{year}-12-31"]
    rows = []
    for index, fiscal in enumerate(ends):
        accepted = (date.fromisoformat(fiscal)
                    + timedelta(days=accepted_offset_days)).isoformat()
        rows.append(_actual(
            fiscal, income, period=f"Q{index + 1}", currency=currency,
            accepted=f"{accepted} 16:00:00", symbol=symbol))
    return rows


def _estimate(fiscal, net_income, *, symbol="AAA",
              snapshot=LATEST_SNAPSHOT, kind="weekly", analysts=20):
    return {
        "symbol": symbol, "snapshot_date": snapshot, "fiscal_date": fiscal,
        "period_type": "Q", "snapshot_kind": kind,
        "net_income_avg": net_income, "num_analysts_eps": analysts,
    }


def _fx(currency, rate):
    return {
        currency: [{
            "date": "2026-01-30", "usd_per_unit": rate,
            "source_symbol": f"{currency}USD",
        }],
    }


def _member(symbol="AAA", market_cap=1000.0, weight_pct=10.0):
    return {"symbol": symbol, "market_cap": market_cap,
            "weight_pct": weight_pct}


# ---------------------------------------------------------------------------
# 1. next four consecutive fiscal quarters of actuals after the valuation date
# ---------------------------------------------------------------------------

def test_next_four_actual_quarters_after_valuation_date_are_summed():
    income = _actual_year(income=25.0, year=2026)
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["exclusion_reason"] is None
    assert [row["date"] for row in window["quarters"]] == [
        "2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]
    assert window["actual_count"] == 4
    assert window["estimate_count"] == 0
    assert all(row["source"] == "actual" for row in window["quarters"])

    result = compute_member_hindsight_ntm_income_usd(
        window["quarters"], _fx("USD", 1.0), "2025-12-31")
    assert result["hindsight_ntm_net_income_usd"] == pytest.approx(100.0)


def test_quarter_ending_on_the_valuation_date_belongs_to_ttm_not_hindsight():
    income = _actual_year(income=25.0, year=2026)
    window = select_next_four_hindsight_quarters(
        income, [], "2026-03-31", consensus_snapshot_date=None)
    # 2026-03-31 ends on the valuation date, so only three quarters remain
    # after it; without a consensus tail the member is unpublishable.
    assert window["quarters"] is None
    assert window["actual_count"] == 3


def test_restated_quarter_keeps_the_latest_acceptance_across_timestamp_formats():
    # A Z-suffixed and a naive acceptance timestamp must stay comparable; the
    # later restatement wins regardless of which format it arrived in.
    income = _actual_year(income=25.0, year=2026)
    income.append(_actual("2026-03-31", 30.0, period="Q1",
                          accepted="2026-05-01 16:00:00"))
    income[0]["accepted_date"] = "2026-04-25T16:00:00Z"
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["exclusion_reason"] is None
    assert window["quarters"][0]["net_income"] == pytest.approx(30.0)

    # Reversed arrival order resolves to the same restatement.
    reversed_rows = list(reversed(income))
    reversed_window = select_next_four_hindsight_quarters(
        reversed_rows, [], "2025-12-31", consensus_snapshot_date=None)
    assert reversed_window["quarters"][0]["net_income"] == pytest.approx(30.0)

    # A non-UTC offset is compared at its true instant, not its wall clock:
    # 2026-05-01T02:00+09:00 is 2026-04-30 17:00 UTC, so the naive 20:00 row is
    # the later restatement and wins.
    offset = _actual_year(income=25.0, year=2026)
    offset[0]["accepted_date"] = "2026-05-01T02:00:00+09:00"
    offset.append(_actual("2026-03-31", 30.0, period="Q1",
                          accepted="2026-04-30 20:00:00"))
    offset_window = select_next_four_hindsight_quarters(
        offset, [], "2025-12-31", consensus_snapshot_date=None)
    assert offset_window["quarters"][0]["net_income"] == pytest.approx(30.0)

    # A row with no usable acceptance timestamp never outranks a dated one.
    undated = _actual_year(income=25.0, year=2026)
    undated[0]["accepted_date"] = None
    undated.append(_actual("2026-03-31", 30.0, period="Q1",
                           accepted="2026-04-25 16:00:00"))
    undated_window = select_next_four_hindsight_quarters(
        undated, [], "2025-12-31", consensus_snapshot_date=None)
    assert undated_window["quarters"][0]["net_income"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# 2. hindsight actuals are NOT accepted-date gated, but are labelled ex-post
# ---------------------------------------------------------------------------

def test_hindsight_actuals_ignore_accepted_date_gate_and_label_actual_only():
    # Every statement is accepted long after the valuation date. A PIT engine
    # must refuse these rows; the hindsight engine must use them.
    income = _actual_year(income=25.0, year=2026, accepted_offset_days=30)
    assert select_four_continuous_asof_quarters(
        income, "2025-12-31", _calendar()) is None

    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["exclusion_reason"] is None
    assert window["actual_count"] == 4

    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member()],
        income_by_symbol={"AAA": income},
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
        basket_symbol="SOXX",
    )
    assert valuation["quality_tier"] == QUALITY_TIER_ACTUAL_ONLY
    assert valuation["hindsight_ntm_pe_gaap"] == pytest.approx(10.0)
    assert valuation["hindsight_actual_quarters"] == 4
    assert valuation["hindsight_estimate_quarters"] == 0


# ---------------------------------------------------------------------------
# 3. recent tail: two actual + two estimate quarters
# ---------------------------------------------------------------------------

def test_recent_tail_merges_two_actual_and_two_estimate_quarters():
    income = _actual_year(income=25.0, year=2026)[:2]
    estimates = [
        _estimate("2026-09-30", 30.0),
        _estimate("2026-12-31", 40.0),
    ]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["exclusion_reason"] is None
    assert window["actual_count"] == 2
    assert window["estimate_count"] == 2
    assert [row["source"] for row in window["quarters"]] == [
        "actual", "actual", "latest_consensus", "latest_consensus"]

    result = compute_member_hindsight_ntm_income_usd(
        window["quarters"], _fx("USD", 1.0), "2025-12-31")
    assert result["hindsight_ntm_net_income_usd"] == pytest.approx(120.0)

    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member()],
        income_by_symbol={"AAA": income},
        estimates_by_symbol={"AAA": estimates},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["quality_tier"] == QUALITY_TIER_LATEST_CONSENSUS_TAIL
    assert valuation["hindsight_actual_quarters"] == 2
    assert valuation["hindsight_estimate_quarters"] == 2


# ---------------------------------------------------------------------------
# 4. an actual-covered fiscal quarter is never double counted with an estimate
# ---------------------------------------------------------------------------

def test_actual_covered_fiscal_quarter_is_not_double_counted_with_estimate():
    # FMP consensus fiscal dates drift a day or two from the true period end
    # (verified in market.db: NVDA actual 2026-01-25 vs estimate 2026-01-26,
    # TSM actual 2026-03-31 vs estimate 2026-03-30). Exact string keying would
    # silently double count, so the key must be proximity based.
    assert is_same_fiscal_quarter("2026-03-31", "2026-03-30")
    assert is_same_fiscal_quarter("2026-06-30", "2026-07-23")  # AXP style drift
    assert not is_same_fiscal_quarter("2026-03-31", "2026-06-30")

    income = _actual_year(income=25.0, year=2026)[:2]
    estimates = [
        _estimate("2026-03-30", 999.0),   # same quarter as actual 2026-03-31
        _estimate("2026-06-29", 999.0),   # same quarter as actual 2026-06-30
        _estimate("2026-09-30", 30.0),
        _estimate("2026-12-31", 40.0),
    ]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert [row["date"] for row in window["quarters"]] == [
        "2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]
    assert window["actual_count"] == 2
    assert window["estimate_count"] == 2
    result = compute_member_hindsight_ntm_income_usd(
        window["quarters"], _fx("USD", 1.0), "2025-12-31")
    # 25 + 25 + 30 + 40, never the 999 duplicates
    assert result["hindsight_ntm_net_income_usd"] == pytest.approx(120.0)


def test_drifted_consensus_cannot_reimport_the_final_ttm_quarter():
    # The valuation date falls exactly on the member's period end, so that
    # quarter belongs to TTM and nothing has reported after it — the whole
    # window is consensus. FMP labels the already-reported quarter 2026-01-02,
    # two days later, which would otherwise become the window's first quarter
    # and both overlap TTM and shift the window a quarter early.
    income = _actual_year(income=25.0, year=2025)
    estimates = [
        _estimate("2026-01-02", 999.0),   # relabelled 2025-12-31, already TTM
        _estimate("2026-03-31", 10.0),
        _estimate("2026-06-30", 20.0),
        _estimate("2026-09-30", 30.0),
        _estimate("2026-12-31", 40.0),
    ]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["exclusion_reason"] is None
    assert window["actual_count"] == 0
    assert window["estimate_count"] == 4
    assert [row["date"] for row in window["quarters"]] == [
        "2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]
    result = compute_member_hindsight_ntm_income_usd(
        window["quarters"], _fx("USD", 1.0), "2025-12-31")
    # 10 + 20 + 30 + 40, never the 999 that duplicates the TTM quarter
    assert result["hindsight_ntm_net_income_usd"] == pytest.approx(100.0)


def test_far_dated_consensus_growth_does_not_veto_a_member():
    # Estimates run years past the window; a hypergrowth 2029 figure must not
    # trip the currency scale guard, because it never enters the window.
    income = _actual_year(income=25.0, year=2026)[:2]
    estimates = [
        _estimate("2026-09-30", 30.0),
        _estimate("2026-12-31", 40.0),
        _estimate("2029-12-31", 25.0 * 400),
    ]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["exclusion_reason"] is None
    assert window["estimate_count"] == 2


# ---------------------------------------------------------------------------
# 5. only the latest allowed snapshot may feed the tail
# ---------------------------------------------------------------------------

def test_estimate_tail_uses_one_latest_allowed_snapshot_for_every_member():
    estimates_by_symbol = {
        "AAA": [
            _estimate("2026-09-30", 30.0, snapshot=OLDER_SNAPSHOT),
            _estimate("2026-12-31", 40.0, snapshot=OLDER_SNAPSHOT),
            _estimate("2026-09-30", 60.0, snapshot=LATEST_SNAPSHOT),
            _estimate("2026-12-31", 80.0, snapshot=LATEST_SNAPSHOT),
        ],
        # BBB only ever reported into the older snapshot; it must NOT be
        # spliced in at its own private vintage.
        "BBB": [
            _estimate("2026-09-30", 30.0, symbol="BBB", snapshot=OLDER_SNAPSHOT),
            _estimate("2026-12-31", 40.0, symbol="BBB", snapshot=OLDER_SNAPSHOT),
        ],
    }
    assert select_latest_allowed_snapshot(estimates_by_symbol) == LATEST_SNAPSHOT
    assert select_latest_allowed_snapshot(
        estimates_by_symbol, max_snapshot_date="2026-02-01") == OLDER_SNAPSHOT

    # A backfill-kind snapshot must never become the implicit latest vintage.
    assert select_latest_allowed_snapshot({"AAA": [
        _estimate("2026-09-30", 30.0, snapshot=LATEST_SNAPSHOT),
        _estimate("2026-09-30", 30.0, snapshot="2026-08-01", kind="backfill"),
    ]}) == LATEST_SNAPSHOT

    income = {
        "AAA": _actual_year(income=25.0, year=2026)[:2],
        "BBB": _actual_year(symbol="BBB", income=25.0, year=2026)[:2],
    }
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA"), _member("BBB")],
        income_by_symbol=income,
        estimates_by_symbol=estimates_by_symbol,
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["consensus_snapshot_date"] == LATEST_SNAPSHOT
    evidence = {row["symbol"]: row for row in valuation["members_json"]}
    # AAA used the latest vintage only: 25 + 25 + 60 + 80
    assert evidence["AAA"]["hindsight_ntm_net_income_usd"] == pytest.approx(190.0)
    assert all(quarter["snapshot_date"] in (None, LATEST_SNAPSHOT)
               for quarter in evidence["AAA"]["quarter_evidence"])
    # BBB has no rows at the allowed vintage, so it is excluded outright.
    assert evidence["BBB"]["hindsight_ntm_net_income_usd"] is None
    assert evidence["BBB"]["exclusion_reason"] == (
        "hindsight_quarters_insufficient")
    assert any(warning.startswith("estimate_snapshot_missing:BBB")
               for warning in valuation["warnings_json"])


# ---------------------------------------------------------------------------
# 6. gap / duplicate / non-continuous fiscal quarters are unpublishable
# ---------------------------------------------------------------------------

def test_fiscal_quarter_gap_is_unpublishable():
    income = _actual_year(income=25.0, year=2026)
    del income[2]                      # 2026-09-30 missing
    income.append(_actual("2027-03-31", 25.0, period="Q1"))
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_quarters_not_continuous"


def test_duplicate_fiscal_quarter_is_unpublishable():
    income = _actual_year(income=25.0, year=2026)
    # A second, near-duplicate period end for the same fiscal quarter.
    income.append(_actual("2026-09-20", 25.0, period="Q3"))
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_quarters_duplicate"


def test_fifty_two_week_filer_duplicate_period_end_is_unpublishable():
    # market.db shape: HD carries both 2025-01-31 and 2025-02-02, SNDK both
    # 2024-12-27 and 2024-12-31, for one fiscal quarter.
    income = [
        _actual("2026-05-02", 25.0, period="Q1"),
        _actual("2026-08-01", 25.0, period="Q2"),
        _actual("2026-10-31", 25.0, period="Q3"),
        _actual("2027-01-30", 25.0, period="Q4"),
        _actual("2027-02-01", 25.0, period="Q4"),   # duplicate of 2027-01-30
    ]
    window = select_next_four_hindsight_quarters(
        income, [], "2026-01-31", consensus_snapshot_date=None)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_quarters_duplicate"


def test_fifty_two_and_fifty_three_week_calendars_stay_continuous():
    # A 13-week filer, including a 53-week year whose extra week stretches one
    # gap to 98 days, must remain publishable.
    income = [
        _actual("2026-05-02", 25.0, period="Q1"),
        _actual("2026-08-01", 25.0, period="Q2"),
        _actual("2026-10-31", 25.0, period="Q3"),
        _actual("2027-02-06", 25.0, period="Q4"),   # 98 days: 53rd week
    ]
    window = select_next_four_hindsight_quarters(
        income, [], "2026-01-31", consensus_snapshot_date=None)
    assert window["exclusion_reason"] is None
    assert window["actual_count"] == 4


def test_fiscal_calendar_change_transition_quarter_is_unpublishable():
    # A filer moving its year end emits a long transition period; that is not a
    # comparable fiscal quarter, so the member must not be published.
    income = [
        _actual("2026-03-31", 25.0, period="Q1"),
        _actual("2026-06-30", 25.0, period="Q2"),
        _actual("2026-11-30", 25.0, period="Q3"),   # 153-day transition period
        _actual("2027-02-28", 25.0, period="Q4"),
    ]
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_quarters_not_continuous"


def test_near_duplicate_consensus_quarters_are_unpublishable():
    income = _actual_year(income=25.0, year=2026)[:2]
    estimates = [
        _estimate("2026-09-30", 30.0),
        _estimate("2026-10-02", 31.0),   # same fiscal quarter, relabelled
    ]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_quarters_duplicate"


def test_estimate_tail_gap_is_unpublishable():
    income = _actual_year(income=25.0, year=2026)[:2]
    estimates = [
        _estimate("2026-09-30", 30.0),
        _estimate("2027-06-30", 40.0),   # skips 2026-12-31 and 2027-03-31
    ]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_quarters_not_continuous"


def test_malformed_actual_quarter_inside_window_is_never_filled_by_estimate():
    income = _actual_year(income=25.0, year=2026)
    income[2]["net_income"] = None      # 2026-09-30 reported but unusable
    estimates = [_estimate("2026-09-30", 30.0)]
    window = select_next_four_hindsight_quarters(
        income, estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "hindsight_actual_quarter_malformed"


def test_member_without_reported_income_history_cannot_be_built_from_consensus():
    # Consensus alone cannot establish the member's currency or reporting
    # scale, so a member with no income_quarterly rows is excluded even when a
    # full four quarters of estimates exist. This is the dominant exclusion for
    # broad SPY membership, where income_quarterly covers only the core pool.
    estimates = [
        _estimate("2026-03-31", 25.0), _estimate("2026-06-30", 25.0),
        _estimate("2026-09-30", 25.0), _estimate("2026-12-31", 25.0),
    ]
    window = select_next_four_hindsight_quarters(
        [], estimates, "2025-12-31",
        consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "income_history_missing"

    # The same member with no consensus either is reported as missing income
    # history, not as a bare shortage of quarters, so the backfill's diagnostics
    # point at the real gap.
    bare = select_next_four_hindsight_quarters(
        [], [], "2025-12-31", consensus_snapshot_date=LATEST_SNAPSHOT)
    assert bare["exclusion_reason"] == "income_history_missing"


def test_missing_tail_without_consensus_snapshot_is_unpublishable():
    income = _actual_year(income=25.0, year=2026)[:2]
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "consensus_snapshot_unavailable"


# ---------------------------------------------------------------------------
# 7. loss makers retained; non-positive basket denominator unpublishable
# ---------------------------------------------------------------------------

def test_loss_making_member_is_retained_in_the_aggregate():
    income = {
        "AAA": _actual_year(income=25.0, year=2026),
        "BBB": _actual_year(symbol="BBB", income=-5.0, year=2026),
    }
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA", market_cap=1000.0),
                 _member("BBB", market_cap=200.0)],
        income_by_symbol=income,
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["n_covered_hindsight"] == 2
    # 100 + (-20) = 80 of net income, both members kept
    assert valuation["hindsight_ntm_net_income"] == pytest.approx(80.0)
    assert valuation["hindsight_total_mcap"] == pytest.approx(1200.0)
    assert valuation["hindsight_ntm_pe_gaap"] == pytest.approx(1200.0 / 80.0)


def test_non_positive_basket_denominator_is_unpublishable_with_warning():
    income = {"AAA": _actual_year(income=-25.0, year=2026)}
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA")],
        income_by_symbol=income,
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["hindsight_ntm_pe_gaap"] is None
    assert valuation["quality_tier"] == QUALITY_TIER_UNPUBLISHABLE
    assert valuation["hindsight_ntm_net_income"] == pytest.approx(-100.0)
    assert any(warning.startswith("hindsight_ntm_net_income_not_positive")
               for warning in valuation["warnings_json"])


# ---------------------------------------------------------------------------
# 8. numerator and denominator share one member set
# ---------------------------------------------------------------------------

def test_numerator_and_denominator_use_a_symmetric_member_set():
    income = {"AAA": _actual_year(income=25.0, year=2026)}
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA", market_cap=1000.0, weight_pct=95.0),
                 # BBB has a market cap but no earnings window at all.
                 _member("BBB", market_cap=40.0, weight_pct=5.0)],
        income_by_symbol=income,
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["n_members"] == 2
    assert valuation["n_covered_hindsight"] == 1
    # BBB's market cap is dropped from the numerator because its earnings are
    # absent from the denominator.
    assert valuation["hindsight_total_mcap"] == pytest.approx(1000.0)
    assert valuation["hindsight_ntm_pe_gaap"] == pytest.approx(10.0)
    assert valuation["hindsight_observed_mcap"] == pytest.approx(1040.0)


def test_member_missing_market_cap_is_excluded_from_both_sides():
    income = {
        "AAA": _actual_year(income=25.0, year=2026),
        "BBB": _actual_year(symbol="BBB", income=10.0, year=2026),
    }
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA", market_cap=1000.0),
                 {"symbol": "BBB", "market_cap": None, "weight_pct": 5.0}],
        income_by_symbol=income,
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["hindsight_total_mcap"] == pytest.approx(1000.0)
    assert valuation["hindsight_ntm_net_income"] == pytest.approx(100.0)
    evidence = {row["symbol"]: row for row in valuation["members_json"]}
    assert evidence["BBB"]["exclusion_reason"] == (
        "market_cap_missing_stale_or_quarantined")


# ---------------------------------------------------------------------------
# 9. EUR / TWD conversion reuses the historical engine's valuation-date FX
# ---------------------------------------------------------------------------

def test_eur_and_twd_conversion_reuses_the_historical_engine_fx():
    fx_by_currency = {
        "EUR": [{"date": "2025-12-30", "usd_per_unit": 1.08,
                 "source_symbol": "EURUSD"}],
        "TWD": [{"date": "2025-12-30", "usd_per_unit": 0.032,
                 "source_symbol": "TWDUSD"}],
    }
    eur_income = _actual_year(symbol="ASML", currency="EUR", income=2_500.0)
    twd_income = _actual_year(symbol="TSM", currency="TWD",
                              income=600_000.0, year=2026)

    eur_fx = select_asof_fx("EUR", fx_by_currency["EUR"], "2025-12-31")
    twd_fx = select_asof_fx("TWD", fx_by_currency["TWD"], "2025-12-31")
    assert eur_fx["usd_per_unit"] == 1.08
    assert twd_fx["usd_per_unit"] == 0.032

    eur_window = select_next_four_hindsight_quarters(
        eur_income, [], "2025-12-31", consensus_snapshot_date=None)
    eur_result = compute_member_hindsight_ntm_income_usd(
        eur_window["quarters"], fx_by_currency, "2025-12-31")
    assert eur_result["hindsight_ntm_net_income_usd"] == pytest.approx(
        4 * 2_500.0 * 1.08)
    assert all(quarter["fx_date"] == "2025-12-30"
               for quarter in eur_result["quarters"])

    twd_window = select_next_four_hindsight_quarters(
        twd_income, [], "2025-12-31", consensus_snapshot_date=None)
    twd_result = compute_member_hindsight_ntm_income_usd(
        twd_window["quarters"], fx_by_currency, "2025-12-31")
    assert twd_result["hindsight_ntm_net_income_usd"] == pytest.approx(
        4 * 600_000.0 * 0.032)

    # An estimate tail quarter inherits the member's reported currency and the
    # same valuation-date FX, so TTM and NTM stay comparable.
    tail_income = twd_income[:2]
    tail_window = select_next_four_hindsight_quarters(
        tail_income,
        [_estimate("2026-09-30", 600_000.0, symbol="TSM"),
         _estimate("2026-12-31", 600_000.0, symbol="TSM")],
        "2025-12-31", consensus_snapshot_date=LATEST_SNAPSHOT)
    tail_result = compute_member_hindsight_ntm_income_usd(
        tail_window["quarters"], fx_by_currency, "2025-12-31")
    assert all(quarter["currency"] == "TWD"
               for quarter in tail_result["quarters"])
    assert tail_result["hindsight_ntm_net_income_usd"] == pytest.approx(
        4 * 600_000.0 * 0.032)


def test_unallowlisted_currency_fails_closed():
    # CNY has no reviewed USD-per-unit band yet, so a CNY reporter must be
    # excluded rather than converted with an unvalidated rate.
    income = _actual_year(symbol="PDD", currency="CNY", income=100.0)
    window = select_next_four_hindsight_quarters(
        income, [], "2025-12-31", consensus_snapshot_date=None)
    assert window["quarters"] is not None
    assert compute_member_hindsight_ntm_income_usd(
        window["quarters"],
        {"CNY": [{"date": "2025-12-30", "usd_per_unit": 0.14,
                  "source_symbol": "CNYUSD"}]},
        "2025-12-31") is None


# ---------------------------------------------------------------------------
# 10. market-cap coverage gate boundary
# ---------------------------------------------------------------------------

def test_mcap_coverage_gate_boundary_at_exactly_ninety_percent():
    covered = {"symbol": "AAA", "market_cap": 90.0,
               "hindsight_ntm_net_income_usd": 9.0}
    uncovered = {"symbol": "BBB", "market_cap": 10.0,
                 "hindsight_ntm_net_income_usd": None}
    at_gate = compute_hindsight_basket_aggregate([covered, uncovered])
    assert at_gate["mcap_coverage"] == pytest.approx(0.90)
    assert at_gate["pe"] == pytest.approx(10.0)
    assert at_gate["is_publishable"] is True

    below_gate = compute_hindsight_basket_aggregate([
        {**covered, "market_cap": 89.99},
        {**uncovered, "market_cap": 10.01},
    ])
    assert below_gate["mcap_coverage"] < 0.90
    assert below_gate["pe"] is None
    assert below_gate["is_publishable"] is False


def test_coverage_gate_failure_yields_an_unpublishable_point_without_a_pe():
    income = {"AAA": _actual_year(income=25.0, year=2026)}
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA", market_cap=89.99, weight_pct=90.0),
                 _member("BBB", market_cap=10.01, weight_pct=10.0)],
        income_by_symbol=income,
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["mcap_coverage_hindsight"] < 0.90
    assert valuation["hindsight_ntm_pe_gaap"] is None
    assert valuation["quality_tier"] == QUALITY_TIER_UNPUBLISHABLE
    assert any(warning.startswith("mcap_coverage_hindsight_below_gate")
               for warning in valuation["warnings_json"])


# ---------------------------------------------------------------------------
# 11. member evidence records actual / estimate quarter provenance
# ---------------------------------------------------------------------------

def test_member_evidence_records_actual_and_estimate_quarter_provenance():
    income = _actual_year(income=25.0, year=2026)[:2]
    estimates = [
        _estimate("2026-09-30", 30.0, analysts=18),
        _estimate("2026-12-31", 40.0, analysts=17),
    ]
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA")],
        income_by_symbol={"AAA": income},
        estimates_by_symbol={"AAA": estimates},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    member = valuation["members_json"][0]
    assert member["hindsight_actual_quarters"] == 2
    assert member["hindsight_estimate_quarters"] == 2
    quarters = member["quarter_evidence"]
    assert [row["fiscal_date"] for row in quarters] == [
        "2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]
    assert [row["source"] for row in quarters] == [
        "actual", "actual", "latest_consensus", "latest_consensus"]
    # actual provenance: the filing acceptance timestamp, ex-post by design
    assert quarters[0]["accepted_date"] == "2026-04-25 16:00:00"
    assert quarters[0]["snapshot_date"] is None
    # estimate provenance: the consensus vintage and analyst count
    assert quarters[2]["accepted_date"] is None
    assert quarters[2]["snapshot_date"] == LATEST_SNAPSHOT
    assert quarters[2]["num_analysts_eps"] == 18
    assert quarters[3]["net_income_usd"] == pytest.approx(40.0)
    assert valuation["methodology_version"]
    assert valuation["composition_effective_date"] == "2025-12-19"
    assert valuation["composition_available_date"] == "2025-12-22"


# ---------------------------------------------------------------------------
# 12. fail-closed guard against a consensus quoted in another currency
# ---------------------------------------------------------------------------

def test_consensus_in_a_foreign_scale_is_rejected_rather_than_converted():
    # market.db evidence: for a few ADR lines (PAYP in JPY, SKHY in KRW) FMP
    # quotes net_income_avg in USD while the filings are in local currency.
    # Converting such a row with the reported-currency rate would be a
    # ~150x-1400x error, so a scale mismatch must exclude the member.
    fx_by_currency = {"TWD": [{"date": "2025-12-30", "usd_per_unit": 0.032,
                              "source_symbol": "TWDUSD"}]}
    income = _actual_year(symbol="TSM", currency="TWD",
                          income=700_000_000_000.0)[:2]
    window = select_next_four_hindsight_quarters(
        income,
        [_estimate("2026-09-30", 22_000_000_000.0, symbol="TSM"),
         _estimate("2026-12-31", 22_000_000_000.0, symbol="TSM")],
        "2025-12-31", consensus_snapshot_date=LATEST_SNAPSHOT)
    assert window["quarters"] is None
    assert window["exclusion_reason"] == "estimate_currency_scale_implausible"

    # A normal consensus miss against the same anchor stays publishable.
    ok = select_next_four_hindsight_quarters(
        income,
        [_estimate("2026-09-30", 550_000_000_000.0, symbol="TSM"),
         _estimate("2026-12-31", 640_000_000_000.0, symbol="TSM")],
        "2025-12-31", consensus_snapshot_date=LATEST_SNAPSHOT)
    assert ok["exclusion_reason"] is None
    assert compute_member_hindsight_ntm_income_usd(
        ok["quarters"], fx_by_currency, "2025-12-31") is not None


def test_basket_quarter_counts_take_the_least_complete_covered_member():
    # AAA has a full year of actuals, BBB only two. The basket point must
    # report the worst covered member, so that R5's percentile filter
    # (actual_only points only) can never see estimate contamination.
    income = {
        "AAA": _actual_year(income=25.0, year=2026),
        "BBB": _actual_year(symbol="BBB", income=25.0, year=2026)[:2],
    }
    estimates = {"BBB": [
        _estimate("2026-09-30", 30.0, symbol="BBB"),
        _estimate("2026-12-31", 40.0, symbol="BBB"),
    ]}
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA"), _member("BBB", market_cap=500.0)],
        income_by_symbol=income,
        estimates_by_symbol=estimates,
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["n_covered_hindsight"] == 2
    evidence = {row["symbol"]: row for row in valuation["members_json"]}
    assert evidence["AAA"]["hindsight_actual_quarters"] == 4
    assert evidence["BBB"]["hindsight_actual_quarters"] == 2
    assert valuation["hindsight_actual_quarters"] == 2
    assert valuation["hindsight_estimate_quarters"] == 2
    assert valuation["quality_tier"] == QUALITY_TIER_LATEST_CONSENSUS_TAIL


def test_one_estimate_contaminated_member_downgrades_the_whole_point():
    income = {
        "AAA": _actual_year(income=25.0, year=2026),
        "BBB": _actual_year(symbol="BBB", income=25.0, year=2026)[:3],
    }
    estimates = {"BBB": [_estimate("2026-12-31", 40.0, symbol="BBB")]}
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA"), _member("BBB", market_cap=500.0)],
        income_by_symbol=income,
        estimates_by_symbol=estimates,
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    assert valuation["hindsight_actual_quarters"] == 3
    assert valuation["hindsight_estimate_quarters"] == 1
    assert valuation["quality_tier"] == QUALITY_TIER_LATEST_CONSENSUS_TAIL


@pytest.mark.parametrize("scenario", [
    "publishable_actual_only",
    "publishable_consensus_tail",
    "loss_making_basket",
    "coverage_below_gate",
    "no_covered_member",
    "quarter_gap",
])
def test_unpublishable_tier_and_null_pe_always_agree(scenario):
    """Task 2 carry-forward: the store does not enforce this, so the engine must.

    ``quality_tier == 'unpublishable'`` and a NULL P/E must be equivalent in
    both directions, and the publishable quarter counts must always sum to 4.
    """
    income = _actual_year(income=25.0, year=2026)
    estimates = {}
    members = [_member("AAA")]
    if scenario == "publishable_consensus_tail":
        income = income[:2]
        estimates = {"AAA": [_estimate("2026-09-30", 30.0),
                             _estimate("2026-12-31", 40.0)]}
    elif scenario == "loss_making_basket":
        income = _actual_year(income=-25.0, year=2026)
    elif scenario == "coverage_below_gate":
        members = [_member("AAA", market_cap=80.0), _member("BBB", market_cap=20.0)]
    elif scenario == "no_covered_member":
        income = []
    elif scenario == "quarter_gap":
        income = [row for row in income if row["date"] != "2026-09-30"]

    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=members,
        income_by_symbol={"AAA": income},
        estimates_by_symbol=estimates,
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    is_unpublishable = valuation["quality_tier"] == QUALITY_TIER_UNPUBLISHABLE
    assert is_unpublishable == (valuation["hindsight_ntm_pe_gaap"] is None)
    if not is_unpublishable:
        assert (valuation["hindsight_actual_quarters"]
                + valuation["hindsight_estimate_quarters"]) == 4
        assert valuation["hindsight_ntm_pe_gaap"] > 0
        assert valuation["hindsight_total_mcap"] > 0
        assert valuation["hindsight_ntm_net_income"] > 0
    assert 0.0 <= valuation["mcap_coverage_hindsight"] <= 1.0
    assert 0 <= valuation["hindsight_actual_quarters"] <= 4
    assert 0 <= valuation["hindsight_estimate_quarters"] <= 4


def test_hindsight_field_names_never_read_as_point_in_time_forward():
    income = {"AAA": _actual_year(income=25.0, year=2026)}
    valuation = compute_hindsight_ntm_valuation(
        valuation_date="2025-12-31",
        members=[_member("AAA")],
        income_by_symbol=income,
        estimates_by_symbol={},
        fx_by_currency=_fx("USD", 1.0),
        composition=COMPOSITION,
    )
    forward_named = [key for key in valuation
                     if "forward" in key.lower() or key.startswith("fwd_")]
    assert forward_named == []
    assert "hindsight_ntm_pe_gaap" in valuation
    assert valuation["is_ex_post"] == 1
