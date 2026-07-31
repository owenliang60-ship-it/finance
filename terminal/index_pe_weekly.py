"""Weekly index-basket P/E points: one valuation date, two comparable lines.

Plan 2026-07-19 §3.1-§3.3. This module turns the audited daily evidence into
the rows that land in ``basket_weekly_pe_history``:

* the published historical line is the **aggregate** caliber
  ``Σmcap / ΣNI`` from :mod:`terminal.basket_pe_aggregate`, with the 90%
  coverage gate applied to that aggregate. The daily engine's holding-weighted
  ``rebalance_weighted_ttm_pe_gaap_proxy`` stays where it is, as
  ``basket_ttm_valuation`` diagnostic evidence: it is a different caliber and
  putting it on the same panel would compare two different questions (R1);
* the ex-post line comes from :mod:`terminal.hindsight_ntm_valuation`
  unchanged, so both metrics share member resolution, FX convention and gate;
* dual-class companies are merged into a single company entry **here**, ahead
  of both engines, because the correct market-cap arithmetic is a per-pair
  empirical fact (see ``config/baskets/share_class_groups.json``) that a pure
  engine cannot infer -- it can only refuse, which the hindsight engine does
  as a backstop.

Weekly sampling picks the last *publishable* trading day of each ISO week
(§3.2). A week with nothing publishable keeps its own NULL row rather than
inheriting the previous week's number; a week with no composition at all --
SOXX before its first verifiable disclosure -- produces no row.
"""
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.data.fmp_forward_ingestion import parse_share_class_groups
from terminal.basket_pe_aggregate import (
    MINIMUM_MCAP_COVERAGE,
    compute_aggregate_basket_pe,
)
from terminal.hindsight_ntm_valuation import (
    QUALITY_TIER_UNPUBLISHABLE,
    compute_hindsight_ntm_valuation,
)
from terminal.historical_basket_valuation import (
    compute_daily_basket_valuation,
    select_asof_market_cap,
)
from terminal.historical_market_cap_sanity import accepted_market_cap_status


PROJECT_ROOT = Path(__file__).parent.parent

WEEKLY_METHODOLOGY_VERSION = "1.0"

# Two classes of one company whose quoted market caps sit within this band of
# each other are quoting the same full-company figure. Wider than a rounding
# difference, far narrower than a genuine class split (FOXA/FOX differ by
# ~12%, NWSA/NWS by ~13%).
SHARE_CLASS_IDENTITY_TOLERANCE = 0.01

# §3.1 gates each metric on *market-cap* coverage. That gate is blind by
# construction to a member with no usable market cap at all: such a member
# enters neither side of the ratio, so quarantining the basket's largest
# holding can leave a 40%-of-the-basket number clearing a 100% mcap coverage
# check. The weekly product layer therefore also requires that the covered
# members carry 90% of the disclosed weight before a number is published.
# Strictly stronger than §3.1, in the fail-closed direction, and the measure
# the Task 3 review asked the verifier to assert on.
MINIMUM_WEIGHT_COVERAGE = 0.90

_SHARE_CLASS_CACHE: Dict[str, Any] = {}


def default_share_class_config(
    config_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Repo share-class membership + market-cap convention, read once.

    Callers that already hold the parsed config should pass it in; this is the
    convenience default so a weekly point computed in isolation still honours
    the reviewed conventions instead of silently double counting.
    """
    directory = Path(config_dir or (PROJECT_ROOT / "config" / "baskets"))
    key = str(directory)
    if key not in _SHARE_CLASS_CACHE:
        with open(directory / "share_class_groups.json", encoding="utf-8") as f:
            groups, conventions = parse_share_class_groups(json.load(f))
        _SHARE_CLASS_CACHE[key] = {
            "groups": groups, "conventions": conventions}
    return _SHARE_CLASS_CACHE[key]


# The published surface of a weekly row: everything a consumer plots or ranks.
# members_json and warnings_json are excluded because they are reconciled
# member by member elsewhere; created_at/last_updated are storage bookkeeping.
RESULT_HASH_FIELDS = (
    "valuation_date", "ttm_pe_gaap", "hindsight_ntm_pe_gaap",
    "ttm_total_mcap", "ttm_net_income", "hindsight_total_mcap",
    "hindsight_ntm_net_income", "n_members", "n_covered_ttm",
    "n_covered_hindsight", "mcap_coverage_ttm", "mcap_coverage_hindsight",
    "hindsight_actual_quarters", "hindsight_estimate_quarters",
    "composition_effective_date", "composition_available_date",
    "quality_tier", "methodology_version",
)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   allow_nan=False, default=str).encode("utf-8")
    ).hexdigest()


def weekly_calendar_hash(trading_dates: Sequence[str]) -> str:
    """Content hash of the trading calendar a run measured itself against.

    Frozen at run start so a later verification can tell that the calendar
    changed underneath it, rather than silently recomputing a smaller
    expectation from whatever the table holds now.
    """
    return _sha256(sorted({str(value) for value in trading_dates}))


def weekly_result_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    """Content hash of a run's published output.

    Recomputable from the stored rows by anyone, so a deleted week or an edited
    P/E stops matching what the run said it wrote. Floats go in as Python
    floats: SQLite stores IEEE-754 doubles and round-trips them exactly, so a
    value that came back unchanged hashes unchanged.
    """
    return _sha256([
        [None if row.get(field) is None
         else (float(row[field]) if isinstance(row.get(field), (int, float))
               and not isinstance(row.get(field), bool) else str(row[field]))
         for field in RESULT_HASH_FIELDS]
        for row in sorted(rows, key=lambda item: str(item["valuation_date"]))
    ])


def expected_week_ends(
    trading_dates: Sequence[str], from_date: str, to_date: str,
    composition_floor: Optional[str],
) -> List[str]:
    """The weeks a run is accountable for: one date per ISO week.

    A week is expected once the basket had a composition that was already
    public by the end of that week -- the same availability rule the producer
    applies when it picks a composition, so the denominator and the data agree
    by construction.
    """
    if composition_floor is None:
        return []
    return [week[-1] for week in
            group_trading_dates_by_week(trading_dates, from_date, to_date)
            if week[-1] >= composition_floor]


# ---------------------------------------------------------------------------
# weekly sampling (§3.2)
# ---------------------------------------------------------------------------

def group_trading_dates_by_week(
    trading_dates: Sequence[str], from_date: str, to_date: str,
) -> List[List[str]]:
    """Split the window's trading days into ascending ISO-week buckets.

    ISO weeks, not "every 7th row" and not a Sunday resample: a short week
    (holiday, a basket's first listed week) is still one week and still
    contributes at most one point.
    """
    weeks: Dict[Any, List[str]] = {}
    for value in sorted(set(trading_dates)):
        if not from_date <= value <= to_date:
            continue
        key = date.fromisoformat(value).isocalendar()[:2]
        weeks.setdefault(key, []).append(value)
    return [weeks[key] for key in sorted(weeks)]


def _publishability_rank(row: Mapping[str, Any]) -> int:
    ttm = row.get("ttm_pe_gaap") is not None
    hindsight = row.get("hindsight_ntm_pe_gaap") is not None
    if ttm and hindsight:
        return 3
    if ttm:
        return 2
    if hindsight:
        return 1
    return 0


def select_weekly_rows(
    weeks: Sequence[Sequence[str]],
    compute: Callable[[str], Optional[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """One row per week: the latest day with the best publishability.

    Days are visited newest first and the walk stops as soon as a day
    publishes both lines, because no earlier day in the week can beat it. The
    historical line ranks above the ex-post line when neither day publishes
    both, since it is the product's primary series.
    """
    rows = []
    for week in weeks:
        best: Optional[Dict[str, Any]] = None
        best_rank = -1
        for day in sorted(week, reverse=True):
            row = compute(day)
            if row is None:
                continue
            rank = _publishability_rank(row)
            if rank > best_rank:
                best, best_rank = row, rank
            if best_rank == 3:
                break
        if best is not None:
            rows.append(best)
    return rows


# ---------------------------------------------------------------------------
# share-class merge, upstream of both engines
# ---------------------------------------------------------------------------

def resolve_company_market_cap(
    *,
    primary: str,
    base_market_cap: Optional[float],
    secondaries: Sequence[str],
    convention: Optional[str],
    valuation_date: str,
    market_cap_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    sanity_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    max_staleness_days: int = 7,
) -> Dict[str, Any]:
    """Full-company market cap for one share-class group.

    The metric divides a company's market cap by that company's net income,
    and FMP files net income for the whole company under the primary class.
    The numerator therefore has to be the whole company too -- which is the
    primary's quoted figure when FMP repeats the full company under every
    class, and the sum of the classes when it splits them.

    A group whose convention has not been reviewed, whose classes cannot all
    be valued, or whose data contradicts the declared convention returns no
    market cap: the company drops out of both metrics rather than entering one
    of them at half or double its size. Secondary classes are read through the
    same as-of selection and sanity quarantine as any other member, so a
    polluted class cannot sneak in.

    A contradiction is not a warning to publish through. If the config says
    both classes quote the whole company and they differ by more than
    ``SHARE_CLASS_IDENTITY_TOLERANCE`` -- or says they split and they are
    identical -- then one of the two is wrong and neither branch is safe.
    Splitting a period-dependent convention (a pair that changed conventions
    mid-history) is a config question to settle against the vendor data, not
    something to guess at per row.
    """
    warnings: List[str] = []
    if not secondaries:
        return {"market_cap": base_market_cap, "components": [],
                "convention": convention, "exclusion_reason": None,
                "warnings": warnings}
    if convention is None:
        return {
            "market_cap": None, "components": [], "convention": None,
            "exclusion_reason": "share_class_convention_undeclared",
            "warnings": [f"share_class_convention_undeclared:{primary}"],
        }

    components = []
    for secondary in secondaries:
        classifications = sanity_by_symbol.get(secondary, [])
        status_by_date = {str(row["date"]): str(row.get("status"))
                          for row in classifications}
        quarantine = {value for value, status in status_by_date.items()
                      if not accepted_market_cap_status(status)}
        observation = select_asof_market_cap(
            market_cap_by_symbol.get(secondary, []), valuation_date,
            status_by_date, quarantine, max_staleness_days)
        components.append({
            "symbol": secondary,
            "market_cap": observation["market_cap"] if observation else None,
            "market_cap_date": observation["date"] if observation else None,
        })

    observed = [row["market_cap"] for row in components
                if row["market_cap"] is not None]
    conflicted = False
    if base_market_cap is not None:
        for row in components:
            if row["market_cap"] is None:
                continue
            looks_identical = abs(
                row["market_cap"] / float(base_market_cap) - 1.0
            ) <= SHARE_CLASS_IDENTITY_TOLERANCE
            if looks_identical != (convention == "full_company_per_class"):
                conflicted = True
                warnings.append(
                    f"share_class_convention_mismatch:{primary}:{row['symbol']}"
                    f":{convention}")
    if conflicted:
        return {"market_cap": None, "components": components,
                "convention": convention,
                "exclusion_reason": "share_class_convention_conflict",
                "warnings": warnings}

    if convention == "full_company_per_class":
        return {"market_cap": base_market_cap, "components": components,
                "convention": convention, "exclusion_reason": None,
                "warnings": warnings}

    if base_market_cap is None or len(observed) != len(components):
        warnings.append(f"share_class_market_cap_incomplete:{primary}")
        return {"market_cap": None, "components": components,
                "convention": convention,
                "exclusion_reason": "share_class_market_cap_incomplete",
                "warnings": warnings}
    return {"market_cap": float(base_market_cap) + sum(observed),
            "components": components, "convention": convention,
            "exclusion_reason": None, "warnings": warnings}


def share_class_secondary_symbols(
    holding_rows: Sequence[Mapping[str, Any]],
    groups: Mapping[str, Sequence[str]],
) -> List[str]:
    """Secondary tickers whose market cap the merge will need.

    They are deliberately kept out of the fundamentals universe: net income is
    filed under the primary, so demanding four continuous quarters from a
    secondary listing would trip the completeness fuse on a company that is
    perfectly well covered.
    """
    primaries = {
        str(row.get("symbol") or "").upper()
        for row in holding_rows if row.get("included") == 1
    } | {
        str(row.get("covered_by") or "").upper()
        for row in holding_rows if row.get("covered_by")
    }
    needed = set()
    for primary, secondaries in groups.items():
        if primary.upper() in primaries:
            needed.update(str(value).upper() for value in secondaries)
    return sorted(needed)


# ---------------------------------------------------------------------------
# one weekly point
# ---------------------------------------------------------------------------

def _compact_member(
    ttm_member: Mapping[str, Any],
    hindsight_member: Optional[Mapping[str, Any]],
    market_cap: Optional[float],
    share_class: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Per-member evidence, trimmed to what a verifier can recompute from.

    Quarter-level FX evidence stays in the daily ``basket_ttm_valuation``
    table; repeating it here would multiply a 500-member basket's weekly row
    by an order of magnitude for evidence that is already materialised.
    """
    symbol = str(ttm_member.get("symbol") or "").upper()
    raw_symbol = str(ttm_member.get("raw_symbol") or symbol).upper()
    row = {
        "symbol": symbol,
        "weight_pct": ttm_member.get("weight_pct"),
        "market_cap": market_cap,
        # The as-of date of the primary class's observation. The verifier needs
        # it to reconcile the numerator against raw historical_market_cap and
        # to prove no observation crossed the valuation date.
        "market_cap_date": ttm_member.get("market_cap_date"),
        "ttm_net_income_usd": (
            ttm_member.get("ttm_net_income_usd")
            if market_cap is not None else None),
        "ttm_exclusion_reason": ttm_member.get("exclusion_reason"),
    }
    if raw_symbol != symbol:
        row["raw_symbol"] = raw_symbol
    if share_class is not None:
        row["share_class"] = {
            "convention": share_class.get("convention"),
            "components": [item["symbol"]
                           for item in share_class.get("components", [])],
        }
    if hindsight_member is not None:
        row.update({
            "hindsight_ntm_net_income_usd": hindsight_member.get(
                "hindsight_ntm_net_income_usd"),
            "hindsight_actual_quarters": hindsight_member.get(
                "hindsight_actual_quarters"),
            "hindsight_estimate_quarters": hindsight_member.get(
                "hindsight_estimate_quarters"),
            "hindsight_exclusion_reason": hindsight_member.get(
                "exclusion_reason"),
        })
        # Which four fiscal quarters this number covers, and the consensus
        # vintage any of them came from. Two dates and a snapshot are enough
        # for a verifier to rebuild the same sum out of income_quarterly and
        # fmp_estimates without re-implementing the window selection -- and
        # without carrying the full quarter-level evidence on every row.
        evidence = hindsight_member.get("quarter_evidence") or []
        if evidence:
            row["hindsight_window"] = [str(evidence[0]["fiscal_date"]),
                                       str(evidence[-1]["fiscal_date"])]
            snapshots = sorted({str(item["snapshot_date"]) for item in evidence
                                if item.get("snapshot_date")})
            if snapshots:
                row["hindsight_snapshot_date"] = snapshots[-1]
    return row


def _summarise_member_warnings(warnings: Sequence[str]) -> List[str]:
    """Collapse per-member exclusions into counts.

    A 500-member basket produces one ``member_excluded:SYM:reason`` line per
    excluded member on every weekly row. The per-member reason is already in
    ``members_json``; the row-level warning list keeps the basket-level story
    readable.
    """
    counts: Dict[str, int] = {}
    kept: List[str] = []
    for warning in warnings:
        text = str(warning)
        if text.startswith("member_excluded:"):
            parts = text.split(":")
            reason = parts[2] if len(parts) > 2 else "unknown"
            counts[reason] = counts.get(reason, 0) + 1
            continue
        if text not in kept:
            kept.append(text)
    for reason in sorted(counts):
        kept.append(f"members_excluded:{reason}:{counts[reason]}")
    return kept


def compute_weekly_point(
    *,
    basket_symbol: str,
    valuation_date: str,
    holding_rows: Sequence[Mapping[str, Any]],
    composition: Mapping[str, Any],
    trading_dates: Sequence[str],
    income_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    market_cap_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    estimates_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    fx_by_currency: Mapping[str, Sequence[Mapping[str, Any]]],
    sanity_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    share_class_groups: Optional[Mapping[str, Sequence[str]]] = None,
    share_class_conventions: Optional[Mapping[str, Optional[str]]] = None,
    consensus_snapshot_date: Optional[str] = None,
    max_snapshot_date: Optional[str] = None,
    max_staleness_days: int = 7,
    minimum_mcap_coverage: float = MINIMUM_MCAP_COVERAGE,
    minimum_weight_coverage: float = MINIMUM_WEIGHT_COVERAGE,
    methodology_version: str = WEEKLY_METHODOLOGY_VERSION,
) -> Dict[str, Any]:
    """Compute one weekly row: aggregate TTM plus ex-post hindsight NTM.

    Both metrics are built from one composition, one valuation date and one
    company member set, so the two lines on the chart differ only in which
    four quarters of earnings they use.
    """
    if share_class_groups is None or share_class_conventions is None:
        config = default_share_class_config()
        share_class_groups = (share_class_groups
                              if share_class_groups is not None
                              else config["groups"])
        share_class_conventions = (share_class_conventions
                                   if share_class_conventions is not None
                                   else config["conventions"])

    ttm_row = compute_daily_basket_valuation(
        valuation_date=valuation_date,
        holding_rows=holding_rows,
        income_by_symbol=income_by_symbol,
        market_cap_by_symbol=market_cap_by_symbol,
        fx_by_currency=fx_by_currency,
        sanity_by_symbol=sanity_by_symbol,
        trading_dates=trading_dates,
        composition=composition,
        basket_symbol=basket_symbol,
        methodology_version=methodology_version,
    )

    warnings: List[str] = [str(value) for value in ttm_row["warnings_json"]]
    share_class_by_symbol: Dict[str, Dict[str, Any]] = {}
    company_members: List[Dict[str, Any]] = []
    for member in ttm_row["members_json"]:
        raw_symbol = str(member.get("raw_symbol")
                         or member.get("symbol") or "").upper()
        secondaries = list(share_class_groups.get(raw_symbol, []))
        resolved = resolve_company_market_cap(
            primary=raw_symbol,
            base_market_cap=member.get("market_cap"),
            secondaries=secondaries,
            convention=share_class_conventions.get(raw_symbol),
            valuation_date=valuation_date,
            market_cap_by_symbol=market_cap_by_symbol,
            sanity_by_symbol=sanity_by_symbol,
            max_staleness_days=max_staleness_days,
        )
        warnings.extend(resolved["warnings"])
        if secondaries:
            share_class_by_symbol[raw_symbol] = resolved
        company_members.append({
            "symbol": str(member.get("symbol") or raw_symbol).upper(),
            "raw_symbol": raw_symbol,
            "weight_pct": member.get("weight_pct"),
            "market_cap": resolved["market_cap"],
            "ttm_net_income_usd": (
                member.get("ttm_net_income_usd")
                if resolved["market_cap"] is not None else None),
            "exclusion_reason": (member.get("exclusion_reason")
                                 or resolved["exclusion_reason"]),
            "_ttm_member": member,
            "_share_class": (share_class_by_symbol.get(raw_symbol)
                             if secondaries else None),
        })

    ttm_aggregate = compute_aggregate_basket_pe(
        company_members, income_key="ttm_net_income_usd",
        minimum_mcap_coverage=minimum_mcap_coverage)

    hindsight = compute_hindsight_ntm_valuation(
        valuation_date=valuation_date,
        members=[{"symbol": row["symbol"], "raw_symbol": row["raw_symbol"],
                  "weight_pct": row["weight_pct"],
                  "market_cap": row["market_cap"]}
                 for row in company_members],
        income_by_symbol=income_by_symbol,
        estimates_by_symbol=estimates_by_symbol,
        fx_by_currency=fx_by_currency,
        composition=composition,
        basket_symbol=basket_symbol,
        consensus_snapshot_date=consensus_snapshot_date,
        max_snapshot_date=max_snapshot_date,
        share_class_groups=share_class_groups,
        minimum_mcap_coverage=minimum_mcap_coverage,
        methodology_version=methodology_version,
    )
    warnings.extend(str(value) for value in hindsight["warnings_json"])

    if not ttm_aggregate["covered_members"]:
        warnings.append("ttm_no_covered_member")
    else:
        if ttm_aggregate["mcap_coverage"] < minimum_mcap_coverage:
            warnings.append(
                f"mcap_coverage_ttm_below_gate:"
                f"{ttm_aggregate['mcap_coverage']:.6f}")
        if ttm_aggregate["net_income_total"] <= 0:
            warnings.append(
                f"ttm_net_income_not_positive:"
                f"{ttm_aggregate['net_income_total']:.2f}")

    # The disclosure-weight gate, applied per metric on top of each engine's
    # own market-cap gate.
    ttm_weight_coverage = ttm_aggregate["weight_coverage"]
    ttm_pe = ttm_aggregate["pe"]
    if ttm_pe is not None and ttm_weight_coverage < minimum_weight_coverage:
        warnings.append(
            f"weight_coverage_ttm_below_gate:{ttm_weight_coverage:.6f}")
        ttm_pe = None

    hindsight_pe = hindsight["hindsight_ntm_pe_gaap"]
    quality_tier = hindsight["quality_tier"]
    hindsight_weight_coverage = hindsight["weight_coverage_hindsight"]
    if hindsight_pe is not None \
            and hindsight_weight_coverage < minimum_weight_coverage:
        warnings.append(
            "weight_coverage_hindsight_below_gate:"
            f"{hindsight_weight_coverage:.6f}")
        hindsight_pe = None
        # quality_tier and the hindsight P/E are one statement: a tier that
        # still claimed actual_only while the number was withheld would let a
        # downstream percentile treat a suppressed point as a complete one.
        quality_tier = QUALITY_TIER_UNPUBLISHABLE

    hindsight_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in hindsight["members_json"]
    }
    members_payload = {
        "consensus_snapshot_date": hindsight["consensus_snapshot_date"],
        "is_ex_post": hindsight["is_ex_post"],
        "weight_coverage_ttm": ttm_aggregate["weight_coverage"],
        "weight_coverage_hindsight": hindsight["weight_coverage_hindsight"],
        "eligible_weight": ttm_aggregate["eligible_weight"],
        "hindsight_observed_mcap": hindsight["hindsight_observed_mcap"],
        "ttm_observed_mcap": ttm_aggregate["observed_market_cap"],
        "holding_date": ttm_row["holding_date"],
        "weight_basis": ttm_row["weight_basis"],
        "composition_quality_tier": ttm_row["data_quality_tier"],
        "members": [
            _compact_member(
                row["_ttm_member"],
                hindsight_by_symbol.get(row["symbol"]),
                row["market_cap"], row["_share_class"])
            for row in company_members
        ],
    }

    return {
        "basket": basket_symbol.upper(),
        "valuation_date": valuation_date,
        "ttm_pe_gaap": ttm_pe,
        "hindsight_ntm_pe_gaap": hindsight_pe,
        "ttm_total_mcap": ttm_aggregate["covered_market_cap"],
        "ttm_net_income": ttm_aggregate["net_income_total"],
        "hindsight_total_mcap": hindsight["hindsight_total_mcap"],
        "hindsight_ntm_net_income": hindsight["hindsight_ntm_net_income"],
        "n_members": len(company_members),
        "n_covered_ttm": len(ttm_aggregate["covered_members"]),
        "n_covered_hindsight": hindsight["n_covered_hindsight"],
        "mcap_coverage_ttm": ttm_aggregate["mcap_coverage"],
        "mcap_coverage_hindsight": hindsight["mcap_coverage_hindsight"],
        "hindsight_actual_quarters": hindsight["hindsight_actual_quarters"],
        "hindsight_estimate_quarters": hindsight["hindsight_estimate_quarters"],
        "composition_effective_date": ttm_row["composition_effective_date"],
        "composition_available_date": ttm_row["composition_available_date"],
        "quality_tier": quality_tier,
        "members_json": members_payload,
        "warnings_json": _summarise_member_warnings(warnings),
        "methodology_version": methodology_version,
    }


def is_unpublishable(row: Mapping[str, Any]) -> bool:
    return row.get("quality_tier") == QUALITY_TIER_UNPUBLISHABLE
