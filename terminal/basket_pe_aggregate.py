"""Shared aggregate ``Σmcap / ΣNI`` kernel for index basket P/E metrics.

Plan 2026-07-19 §3.1 (R1) fixes one aggregate caliber for all three published
lines — historical TTM, ex-post hindsight NTM and point-in-time consensus —
so a single chart panel can compare them. This module is that caliber, and
the only place the 90% coverage gate is expressed.

R3: producer and verifier both import from here. Before this module the
formula existed twice in hand-copied form (``scripts/verify_basket_ttm_pe.py``
against ``terminal/historical_basket_valuation.py``), each with its own copy
of the threshold constant, so a change to one silently diverged from the
other. Verifier independence is not supposed to come from a second
implementation of the formula — it comes from heterogeneous spot-check
reconciliation against materialised evidence (see issue 048).
"""
from typing import Any, Dict, Mapping, Sequence


# The gate the plan applies to every published metric. It is deliberately a
# *market-cap* coverage gate: the metric is a ratio of sums, so the question
# is how much of the basket's observable market cap the numerator actually
# represents, not how much disclosure weight it carries.
MINIMUM_MCAP_COVERAGE = 0.90


def compute_aggregate_basket_pe(
    members: Sequence[Mapping[str, Any]],
    *,
    income_key: str,
    minimum_mcap_coverage: float = MINIMUM_MCAP_COVERAGE,
) -> Dict[str, Any]:
    """Aggregate ``Σmcap / ΣNI`` over one symmetric member set.

    ``covered_market_cap`` and the income total are summed over exactly the
    same members, so a member missing either side is dropped from both.
    ``mcap_coverage`` measures how much of the observable basket market cap
    that set represents, and gates publication.

    ``weight_coverage`` is reported alongside but does **not** gate. A member
    with no market cap at all enters neither side of the ratio, so a sparse
    market-cap history can clear the 90% gate on a small slice of the basket;
    disclosure weight is the independent measure that exposes it, and the
    backfill verifier asserts on it.

    Loss-making members are kept — a negative quarter is real earnings
    information. Publication fails closed when the summed denominator is not
    strictly positive, because a basket P/E through zero has no meaning.
    """
    observed_market_cap = 0.0
    total_weight = 0.0
    covered_weight = 0.0
    covered = []
    for member in members:
        weight = member.get("weight_pct")
        weight = float(weight) if weight is not None else 0.0
        if weight > 0:
            total_weight += weight
        market_cap = member.get("market_cap")
        if market_cap is None:
            continue
        market_cap = float(market_cap)
        if market_cap <= 0:
            continue
        observed_market_cap += market_cap
        income = member.get(income_key)
        if income is None:
            continue
        if weight > 0:
            covered_weight += weight
        covered.append({
            **dict(member),
            "market_cap": market_cap,
            income_key: float(income),
        })
    covered_market_cap = sum(row["market_cap"] for row in covered)
    income_total = sum(row[income_key] for row in covered)
    coverage = (covered_market_cap / observed_market_cap
                if observed_market_cap > 0 else 0.0)
    is_publishable = bool(
        covered and coverage >= minimum_mcap_coverage and income_total > 0)
    return {
        "pe": covered_market_cap / income_total if is_publishable else None,
        "is_publishable": is_publishable,
        "covered_market_cap": covered_market_cap,
        "observed_market_cap": observed_market_cap,
        "net_income_total": income_total,
        "mcap_coverage": coverage,
        "weight_coverage": (covered_weight / total_weight
                            if total_weight > 0 else 0.0),
        "covered_weight": covered_weight,
        "eligible_weight": total_weight,
        "covered_members": covered,
    }
