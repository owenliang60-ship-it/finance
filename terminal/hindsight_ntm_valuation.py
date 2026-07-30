"""Ex-post ("hindsight") NTM GAAP earnings for historical index baskets.

This module is deliberately **not** point-in-time. For a historical valuation
date ``t`` it sums the *next* four continuous fiscal quarters of GAAP net
income per member — data that did not exist at ``t``. It answers "what did the
basket earn over the year that followed?", never "what was the market
forecasting at ``t``?". Point-in-time consensus lives in the separate
``fmp_basket_valuation`` line and must keep its own ``fwd_*`` naming.

Two consequences of that choice are load bearing:

* actual quarters are selected **without** an ``accepted_date <= t`` visibility
  gate, unlike :func:`terminal.historical_basket_valuation
  .select_four_continuous_asof_quarters`;
* every public name here carries ``hindsight``, and the emitted row carries
  ``is_ex_post = 1``, so no consumer can mistake it for a tradeable forward P/E.

Recent valuation dates have no complete year of actuals yet. Those points are
completed with a **single latest consensus vintage** for the missing quarters
only. Using the snapshot that was current at ``t`` would manufacture a fake PIT
series, so the vintage is explicitly *not* gated on the valuation date.

Numerically this engine reuses the audited historical engine: plain ``float``
arithmetic and :func:`terminal.historical_basket_valuation.select_asof_fx` at
the valuation date, so TTM and hindsight NTM stay comparable.
"""
from datetime import date
from statistics import median
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence

from terminal.basket_pe_aggregate import (
    MINIMUM_MCAP_COVERAGE,
    compute_aggregate_basket_pe,
)
from terminal.historical_basket_valuation import (
    accepted_sort_key as _accepted_sort_key,
    select_asof_fx,
)


HINDSIGHT_METHODOLOGY_VERSION = "1.0"

QUALITY_TIER_ACTUAL_ONLY = "actual_only"
QUALITY_TIER_LATEST_CONSENSUS_TAIL = "latest_consensus_tail"
QUALITY_TIER_UNPUBLISHABLE = "unpublishable"

SOURCE_ACTUAL = "actual"
SOURCE_LATEST_CONSENSUS = "latest_consensus"

HINDSIGHT_QUARTERS = 4

# FMP consensus fiscal dates drift from the true period end (market.db: NVDA
# actual 2026-01-25 vs estimate 2026-01-26; TSM 2026-03-31 vs 2026-03-30; AXP
# up to +23 days). Only 82.5% of overlapping quarters share an exact date, so
# the fiscal key is proximity based: quarters sit ~91 days apart, therefore any
# pair inside this tolerance is the same fiscal quarter under a different
# labelling convention.
SAME_FISCAL_QUARTER_MAX_DRIFT_DAYS = 45

# Continuity band reused verbatim from the historical TTM engine so both
# metrics accept and reject the same fiscal calendars (52/53-week years, fiscal
# calendar changes).
MIN_CONTINUOUS_QUARTER_GAP_DAYS = 60
MAX_CONTINUOUS_QUARTER_GAP_DAYS = 120

# Internal continuity is not enough: four mutually continuous quarters can sit
# anywhere on the calendar. The window's first quarter must also end within one
# quarter of the valuation date, otherwise a hole in the source data slides the
# whole window forward and the basket's market cap at ``t`` gets divided by
# earnings from a later period. This is not hypothetical — income_quarterly
# begins in 2024 for 207 of its 216 symbols, so without this gate every
# valuation date in the first years of a five-year backfill would quietly
# borrow 2024 earnings.
MAX_WINDOW_START_LAG_DAYS = MAX_CONTINUOUS_QUARTER_GAP_DAYS

# ``fmp_estimates`` carries no currency column, so an estimate quarter inherits
# the member's reported currency. That inference is right for every non-USD
# basket member checked against market.db (ASML EUR, TSM/ASX/UMC TWD, PDD CNY)
# but wrong for a few ADR lines where FMP quotes consensus in USD against
# local-currency filings (PAYP in JPY, SKHY in KRW). Converting such a row
# would be a 150x-1400x error, so a consensus whose magnitude cannot be
# reconciled with the member's own reporting scale excludes the member instead.
#
# The guard is deliberately narrow in two ways:
#
# * it never applies to a USD reporter, which cannot have a currency mismatch
#   in the first place — 42 of market.db's 193 USD reporters swing more than
#   25x internally (BA 2055x, AXP 666x, UNH 629x, CRDO 395x, INTC 132x, the
#   last two SOXX members), so applying it there is all false positives;
# * it anchors on the median of recent quarters rather than the all-time peak,
#   so one extraordinary quarter cannot set the scale for years afterwards.
#
# The factor sits far above genuine consensus misses (worst observed ~3.9x, and
# the non-USD members' recent quarters stay inside 2.1x of their own median)
# and below the offending FX rates (TWD ~32x, JPY ~150x, KRW ~1400x). EUR and
# CNY mismatches are inherently invisible to a scale test and are not claimed.
ESTIMATE_SCALE_GUARD_FACTOR = 25.0
ESTIMATE_SCALE_ANCHOR_QUARTERS = 8

# A backfill snapshot must never become the implicit "latest" consensus
# vintage; this mirrors ``MarketStore.get_fmp_estimates``.
ALLOWED_SNAPSHOT_KINDS = frozenset({"weekly"})


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def is_same_fiscal_quarter(left: str, right: str) -> bool:
    """Return whether two period-end dates label the same fiscal quarter.

    This is the single fiscal key applied to every pairing — actual/actual,
    actual/estimate and estimate/estimate — so overlap detection is structural
    rather than a matter of two sources agreeing on a date string.
    """
    left_date = _parse_date(left)
    right_date = _parse_date(right)
    if left_date is None or right_date is None:
        return False
    return abs((right_date - left_date).days) <= SAME_FISCAL_QUARTER_MAX_DRIFT_DAYS


def _is_continuous(fiscal_dates: Sequence[date]) -> bool:
    return all(
        MIN_CONTINUOUS_QUARTER_GAP_DAYS
        <= (later - earlier).days
        <= MAX_CONTINUOUS_QUARTER_GAP_DAYS
        for earlier, later in zip(fiscal_dates, fiscal_dates[1:])
    )


def select_latest_allowed_snapshot(
    estimates_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    max_snapshot_date: Optional[str] = None,
    allowed_kinds: FrozenSet[str] = ALLOWED_SNAPSHOT_KINDS,
) -> Optional[str]:
    """Return one consensus vintage for the whole basket.

    A single snapshot date is picked across every member so the tail cannot be
    assembled from per-symbol vintages, which would silently mix consensus
    ages inside one basket point.
    """
    latest = None
    for rows in estimates_by_symbol.values():
        for row in rows:
            snapshot = str(row.get("snapshot_date") or "")
            if not snapshot:
                continue
            kind = str(row.get("snapshot_kind") or "weekly")
            if kind not in allowed_kinds:
                continue
            if max_snapshot_date is not None and snapshot > max_snapshot_date:
                continue
            if latest is None or snapshot > latest:
                latest = snapshot
    return latest


def _actual_candidates(
    income_rows: Sequence[Mapping[str, Any]],
    target: date,
) -> Dict[str, Any]:
    """Split post-``target`` quarterly actuals into usable and malformed keys."""
    usable: Dict[str, List[Dict[str, Any]]] = {}
    malformed = set()
    currency_timeline: List[Any] = []
    all_fiscal_keys = set()
    scale_samples: List[Any] = []
    for raw in income_rows:
        period = str(raw.get("period") or "").upper()
        if not period.startswith("Q"):
            continue
        fiscal = _parse_date(raw.get("date"))
        if fiscal is None:
            continue
        currency = str(raw.get(
            "reported_currency", raw.get("reportedCurrency", ""))).upper()
        income = raw.get("net_income", raw.get("netIncome"))
        try:
            numeric_income = float(income)
        except (TypeError, ValueError):
            numeric_income = None
        # The currency timeline and the scale anchor read the member's whole
        # reporting history, including quarters at or before the valuation
        # date, because an estimate quarter inherits both.
        if currency:
            currency_timeline.append((fiscal.isoformat(), currency))
        if numeric_income is not None:
            scale_samples.append((fiscal.isoformat(), abs(numeric_income)))
        # Every reported fiscal quarter is remembered, including those at or
        # before the valuation date. A consensus row a day or two past the
        # valuation date can otherwise re-import the quarter that already
        # belongs to TTM, overlapping the two metrics.
        all_fiscal_keys.add(fiscal.isoformat())
        if fiscal <= target:
            continue
        fiscal_text = fiscal.isoformat()
        if numeric_income is None or not currency:
            malformed.add(fiscal_text)
            continue
        accepted_raw = raw.get("accepted_date", raw.get("acceptedDate"))
        usable.setdefault(fiscal_text, []).append({
            "date": fiscal_text,
            "period": period,
            "source": SOURCE_ACTUAL,
            "accepted_date": accepted_raw,
            "snapshot_date": None,
            "num_analysts_eps": None,
            "reported_currency": currency,
            "net_income": numeric_income,
        })
    # A restated quarter keeps the latest acceptance, matching the historical
    # engine, and is resolved independently of input row order.
    resolved = {}
    for fiscal_text, rows in usable.items():
        resolved[fiscal_text] = max(
            rows, key=lambda row: _accepted_sort_key(row["accepted_date"]))
    recent = [value for _, value in
              sorted(scale_samples, reverse=True)[:ESTIMATE_SCALE_ANCHOR_QUARTERS]]
    return {
        "quarters": resolved,
        "malformed": malformed,
        "all_fiscal_keys": all_fiscal_keys,
        "currency_timeline": sorted(currency_timeline),
        "scale_anchor": median(recent) if recent else 0.0,
    }


def _currency_asof(
    currency_timeline: Sequence[Any],
    fiscal_text: str,
) -> Optional[str]:
    """Reported currency in force for a fiscal period end."""
    if not currency_timeline:
        return None
    prior = [currency for fiscal, currency in currency_timeline
             if fiscal <= fiscal_text]
    if prior:
        return prior[-1]
    return currency_timeline[0][1]


def _failure(
    reason: str,
    actual_count: int,
    estimate_count: int,
    warnings: List[str],
) -> Dict[str, Any]:
    return {
        "quarters": None,
        "actual_count": actual_count,
        "estimate_count": estimate_count,
        "exclusion_reason": reason,
        "warnings": warnings,
    }


def select_next_four_hindsight_quarters(
    income_rows: Sequence[Mapping[str, Any]],
    estimate_rows: Sequence[Mapping[str, Any]],
    valuation_date: str,
    *,
    consensus_snapshot_date: Optional[str],
    allowed_kinds: FrozenSet[str] = ALLOWED_SNAPSHOT_KINDS,
) -> Dict[str, Any]:
    """Build the four fiscal quarters that follow ``valuation_date``.

    Actual quarters come first and are used ex-post — no ``accepted_date``
    gate. Only the quarters the actuals do not cover are completed from
    ``consensus_snapshot_date``, and a member is publishable only with exactly
    four continuous quarters.
    """
    target = date.fromisoformat(valuation_date)
    warnings: List[str] = []
    candidates = _actual_candidates(income_rows, target)
    actual_quarters = candidates["quarters"]
    malformed = candidates["malformed"]

    # An estimate quarter inherits its currency and its scale anchor from the
    # member's own filings, so a member with no usable reported income history
    # can never be completed from consensus alone. ``income_quarterly`` only
    # covers the core pool today, so this is the dominant exclusion for the
    # broad SPY membership rather than an exotic edge case.
    if not candidates["currency_timeline"]:
        return _failure("income_history_missing", 0, 0, warnings)

    actual_keys = sorted(actual_quarters)
    selected = [actual_quarters[key] for key in actual_keys[:HINDSIGHT_QUARTERS]]

    # A quarter that was filed but is unusable leaves a hole that must never be
    # papered over with consensus. If any malformed quarter falls inside the
    # first four fiscal positions after the valuation date, fail closed.
    ordered_positions = sorted(set(actual_keys) | malformed)
    if any(key in malformed
           for key in ordered_positions[:HINDSIGHT_QUARTERS]):
        return _failure("hindsight_actual_quarter_malformed",
                        len(selected), 0, warnings)

    tail_needed = HINDSIGHT_QUARTERS - len(selected)
    tail: List[Dict[str, Any]] = []
    eligible_estimate_keys: List[str] = []
    if tail_needed > 0:
        if not consensus_snapshot_date:
            return _failure("consensus_snapshot_unavailable",
                            len(selected), 0, warnings)
        last_actual = _parse_date(selected[-1]["date"]) if selected else None
        scale_anchor = float(candidates["scale_anchor"])
        eligible: Dict[str, Dict[str, Any]] = {}
        for raw in estimate_rows:
            if str(raw.get("snapshot_date") or "") != consensus_snapshot_date:
                continue
            if str(raw.get("snapshot_kind") or "weekly") not in allowed_kinds:
                continue
            if str(raw.get("period_type") or "").upper() != "Q":
                continue
            fiscal = _parse_date(raw.get("fiscal_date", raw.get("date")))
            if fiscal is None or fiscal <= target:
                continue
            try:
                numeric_income = float(raw.get("net_income_avg"))
            except (TypeError, ValueError):
                continue
            fiscal_text = fiscal.isoformat()
            # A fiscal quarter the member has already reported is never
            # re-added as consensus, whichever side of the valuation date the
            # actual sits on.
            if any(is_same_fiscal_quarter(fiscal_text, key)
                   for key in candidates["all_fiscal_keys"]):
                continue
            if last_actual is not None and fiscal <= last_actual:
                continue
            currency = _currency_asof(
                candidates["currency_timeline"], fiscal_text)
            if not currency:
                return _failure("income_history_missing",
                                len(selected), 0, warnings)
            if fiscal_text in eligible:
                continue
            eligible[fiscal_text] = {
                "date": fiscal_text,
                "period": None,
                "source": SOURCE_LATEST_CONSENSUS,
                "accepted_date": None,
                "snapshot_date": consensus_snapshot_date,
                "num_analysts_eps": raw.get("num_analysts_eps"),
                "reported_currency": currency,
                "net_income": numeric_income,
            }
        eligible_estimate_keys = sorted(eligible)
        tail = [eligible[key] for key in eligible_estimate_keys[:tail_needed]]
        # A consensus quoted in a different currency than the filings is a
        # silent order-of-magnitude error; reject the member rather than pick
        # around the suspect rows. Only the quarters that actually enter the
        # window are judged, so a far-dated estimate cannot veto a member, and
        # only non-USD reporters are judged at all.
        if scale_anchor > 0:
            for row in tail:
                if str(row["reported_currency"]).upper() == "USD":
                    continue
                magnitude = abs(float(row["net_income"]))
                if magnitude == 0:
                    continue
                if (magnitude > ESTIMATE_SCALE_GUARD_FACTOR * scale_anchor
                        or magnitude * ESTIMATE_SCALE_GUARD_FACTOR < scale_anchor):
                    return _failure("estimate_currency_scale_implausible",
                                    len(selected), len(tail), warnings)

    quarters = selected + tail
    if len(quarters) != HINDSIGHT_QUARTERS:
        return _failure("hindsight_quarters_insufficient",
                        len(selected), len(tail), warnings)
    # Anchor the window to the valuation date before judging it internally.
    window_start_lag = (date.fromisoformat(quarters[0]["date"]) - target).days
    if not 0 < window_start_lag <= MAX_WINDOW_START_LAG_DAYS:
        return _failure("hindsight_window_not_anchored",
                        len(selected), len(tail), warnings)
    # Two rows labelling one fiscal quarter under different period ends are a
    # real FMP pattern for 52/53-week filers (market.db: HD 2025-01-31 and
    # 2025-02-02; SNDK 2024-12-27 and 2024-12-31). The same fiscal key that
    # de-duplicates consensus against actuals rejects them here, so the member
    # is excluded rather than double counted. A duplicate of the final quarter
    # can sit just past the window, where taking the first four rows would have
    # silently resolved the ambiguity, so the check looks one quarter ahead.
    window_keys = [row["date"] for row in quarters]
    window_end = window_keys[-1]
    relevant_keys = sorted(
        key for key in set(actual_quarters) | set(eligible_estimate_keys)
        if key <= window_end or is_same_fiscal_quarter(key, window_end))
    if any(is_same_fiscal_quarter(earlier, later)
           for earlier, later in zip(relevant_keys, relevant_keys[1:])):
        return _failure("hindsight_quarters_duplicate",
                        len(selected), len(tail), warnings)
    fiscal_dates = [date.fromisoformat(row["date"]) for row in quarters]
    if not _is_continuous(fiscal_dates):
        return _failure("hindsight_quarters_not_continuous",
                        len(selected), len(tail), warnings)
    return {
        "quarters": quarters,
        "actual_count": len(selected),
        "estimate_count": len(tail),
        "exclusion_reason": None,
        "warnings": warnings,
    }


def compute_member_hindsight_ntm_income_usd(
    quarters: Sequence[Mapping[str, Any]],
    fx_by_currency: Mapping[str, Sequence[Mapping[str, Any]]],
    valuation_date: str,
) -> Optional[Dict[str, Any]]:
    """Convert four hindsight quarters to USD at the valuation-date FX.

    Mirrors :func:`terminal.historical_basket_valuation
    .compute_member_ttm_income_usd`, including the valuation-date FX
    convention, so the TTM and hindsight NTM lines stay comparable.
    """
    if len(quarters) != HINDSIGHT_QUARTERS:
        return None
    total = 0.0
    evidence = []
    for row in quarters:
        currency = str(row.get(
            "reported_currency", row.get("reportedCurrency", ""))).upper()
        if not currency:
            return None
        fx = select_asof_fx(
            currency, fx_by_currency.get(currency, []), valuation_date)
        if fx is None:
            return None
        try:
            local_income = float(row["net_income"])
        except (KeyError, TypeError, ValueError):
            return None
        usd_income = local_income * float(fx["usd_per_unit"])
        total += usd_income
        evidence.append({
            "fiscal_date": row["date"],
            "source": row["source"],
            "accepted_date": row.get("accepted_date"),
            "snapshot_date": row.get("snapshot_date"),
            "num_analysts_eps": row.get("num_analysts_eps"),
            "currency": currency,
            "net_income_local": local_income,
            "fx_date": fx["date"],
            "usd_per_unit": fx["usd_per_unit"],
            "net_income_usd": usd_income,
        })
    return {"hindsight_ntm_net_income_usd": total, "quarters": evidence}


def compute_hindsight_basket_aggregate(
    members: Sequence[Mapping[str, Any]],
    *,
    minimum_mcap_coverage: float = MINIMUM_MCAP_COVERAGE,
) -> Dict[str, Any]:
    """Hindsight NTM view of the shared aggregate kernel (R1/R3).

    The formula, the symmetric member set and the 90% gate all live in
    :func:`terminal.basket_pe_aggregate.compute_aggregate_basket_pe`, which
    the historical TTM line and the backfill verifier import as well. Only the
    income key and the metric-specific field name are hindsight's.
    """
    aggregate = compute_aggregate_basket_pe(
        members, income_key="hindsight_ntm_net_income_usd",
        minimum_mcap_coverage=minimum_mcap_coverage)
    return {
        **{key: value for key, value in aggregate.items()
           if key != "net_income_total"},
        "hindsight_ntm_net_income_usd": aggregate["net_income_total"],
    }


def compute_hindsight_ntm_valuation(
    *,
    valuation_date: str,
    members: Sequence[Mapping[str, Any]],
    income_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    estimates_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    fx_by_currency: Mapping[str, Sequence[Mapping[str, Any]]],
    composition: Mapping[str, Any],
    basket_symbol: str = "SOXX",
    consensus_snapshot_date: Optional[str] = None,
    max_snapshot_date: Optional[str] = None,
    share_class_groups: Optional[Mapping[str, Sequence[str]]] = None,
    minimum_mcap_coverage: float = MINIMUM_MCAP_COVERAGE,
    methodology_version: str = HINDSIGHT_METHODOLOGY_VERSION,
) -> Dict[str, Any]:
    """Compute one ex-post hindsight NTM basket point.

    ``members`` carries the caller's already-resolved composition: symbol
    (the data key, after any alias resolution), ``weight_pct`` and the
    valuation-date ``market_cap``. This engine touches no database and no
    network; it is pure computation over the rows handed to it.

    ``share_class_groups`` maps a primary ticker to its secondary listings, in
    the shape of ``config/baskets/share_class_groups.json``. It exists so one
    company cannot be aggregated twice: FMP files full-company net income under
    **every** class (market.db carries GOOGL for 10 quarters and GOOG for 8),
    so counting both entries divides one company's market cap by twice its
    earnings.

    Such a pair is excluded rather than merged, deliberately. FMP's market-cap
    convention is not consistent across pairs — GOOGL and GOOG report an
    identical full-company figure, while FOXA/FOX and NWSA/NWS are split across
    the classes — so summing the two market caps would be right for Fox and
    double Alphabet, and taking one would be right for Alphabet and halve Fox.
    A pure engine cannot pick correctly, so it refuses and says so. Merging
    belongs upstream in the holdings normalizer, where the convention is known
    per source.
    """
    snapshot = consensus_snapshot_date or select_latest_allowed_snapshot(
        estimates_by_symbol, max_snapshot_date=max_snapshot_date)
    warnings: List[str] = [
        str(value) for value in composition.get("snapshot_warnings", [])]
    member_evidence: List[Dict[str, Any]] = []

    secondary_to_primary = {
        str(secondary).upper(): str(primary).upper()
        for primary, secondaries in (share_class_groups or {}).items()
        for secondary in secondaries
    }
    company_counts: Dict[str, int] = {}
    for member in members:
        symbol = str(member.get("symbol") or "").upper()
        company = secondary_to_primary.get(symbol, symbol)
        company_counts[company] = company_counts.get(company, 0) + 1
    duplicate_companies = {company for company, count in company_counts.items()
                           if count > 1}
    for company in sorted(duplicate_companies):
        warnings.append(f"duplicate_company_share_class:{company}")

    for member in sorted(members, key=lambda row: str(row.get("symbol"))):
        symbol = str(member.get("symbol") or "").upper()
        raw_symbol = str(member.get("raw_symbol") or symbol).upper()
        company = secondary_to_primary.get(symbol, symbol)
        market_cap = member.get("market_cap")
        weight_pct = member.get("weight_pct")
        estimate_rows = estimates_by_symbol.get(symbol, [])
        window = select_next_four_hindsight_quarters(
            income_by_symbol.get(symbol, []), estimate_rows, valuation_date,
            consensus_snapshot_date=snapshot)
        warnings.extend(window["warnings"])
        income_result = (
            compute_member_hindsight_ntm_income_usd(
                window["quarters"], fx_by_currency, valuation_date)
            if window["quarters"] is not None else None)

        exclusion_reason = None
        if company in duplicate_companies:
            exclusion_reason = "duplicate_company_share_class"
        elif market_cap is None:
            exclusion_reason = "market_cap_missing_stale_or_quarantined"
        elif window["quarters"] is None:
            exclusion_reason = window["exclusion_reason"]
        elif income_result is None:
            exclusion_reason = "fx_missing_or_stale"
        if exclusion_reason is not None:
            warnings.append(f"member_excluded:{symbol}:{exclusion_reason}")
        if (window["exclusion_reason"] == "hindsight_quarters_insufficient"
                and window["estimate_count"] == 0 and estimate_rows
                and snapshot is not None):
            warnings.append(f"estimate_snapshot_missing:{symbol}:{snapshot}")

        member_evidence.append({
            "symbol": symbol,
            "raw_symbol": raw_symbol,
            "weight_pct": float(weight_pct) if weight_pct is not None else None,
            "market_cap": float(market_cap) if market_cap is not None else None,
            "hindsight_actual_quarters": window["actual_count"],
            "hindsight_estimate_quarters": window["estimate_count"],
            "hindsight_ntm_net_income_usd": (
                income_result["hindsight_ntm_net_income_usd"]
                if income_result is not None and exclusion_reason is None
                else None),
            "quarter_evidence": (
                income_result["quarters"]
                if income_result is not None and exclusion_reason is None
                else []),
            "exclusion_reason": exclusion_reason,
        })

    aggregate = compute_hindsight_basket_aggregate(
        member_evidence, minimum_mcap_coverage=minimum_mcap_coverage)
    covered = aggregate["covered_members"]

    if covered:
        actual_quarters = min(
            int(row["hindsight_actual_quarters"]) for row in covered)
        estimate_quarters = HINDSIGHT_QUARTERS - actual_quarters
    else:
        actual_quarters = 0
        estimate_quarters = 0

    if not aggregate["is_publishable"]:
        quality_tier = QUALITY_TIER_UNPUBLISHABLE
    elif estimate_quarters == 0:
        quality_tier = QUALITY_TIER_ACTUAL_ONLY
    else:
        quality_tier = QUALITY_TIER_LATEST_CONSENSUS_TAIL

    if not covered:
        warnings.append("hindsight_no_covered_member")
    else:
        if aggregate["mcap_coverage"] < minimum_mcap_coverage:
            warnings.append(
                "mcap_coverage_hindsight_below_gate:"
                f"{aggregate['mcap_coverage']:.6f}")
        if aggregate["hindsight_ntm_net_income_usd"] <= 0:
            warnings.append(
                "hindsight_ntm_net_income_not_positive:"
                f"{aggregate['hindsight_ntm_net_income_usd']:.2f}")

    # The store does not enforce it, so the engine must: an unpublishable point
    # never carries a P/E value.
    pe = aggregate["pe"] if quality_tier != QUALITY_TIER_UNPUBLISHABLE else None

    return {
        "basket": basket_symbol.upper(),
        "valuation_date": valuation_date,
        "is_ex_post": 1,
        "consensus_snapshot_date": snapshot,
        "hindsight_ntm_pe_gaap": pe,
        "hindsight_total_mcap": aggregate["covered_market_cap"],
        "hindsight_observed_mcap": aggregate["observed_market_cap"],
        "hindsight_ntm_net_income": aggregate["hindsight_ntm_net_income_usd"],
        "n_members": len(member_evidence),
        "n_covered_hindsight": len(covered),
        "mcap_coverage_hindsight": aggregate["mcap_coverage"],
        "weight_coverage_hindsight": aggregate["weight_coverage"],
        "hindsight_actual_quarters": actual_quarters,
        "hindsight_estimate_quarters": estimate_quarters,
        "composition_effective_date": str(
            composition["composition_effective_date"]),
        "composition_available_date": str(
            composition["composition_available_date"]),
        "quality_tier": quality_tier,
        "members_json": member_evidence,
        "warnings_json": warnings,
        "methodology_version": methodology_version,
    }
