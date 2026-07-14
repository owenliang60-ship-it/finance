"""Pure as-of GAAP TTM valuation for fixed-weight historical baskets."""
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from src.data.fmp_forward_ingestion import next_trading_date
from src.data.fx_validation import is_plausible_usd_per_unit
from terminal.historical_market_cap_sanity import accepted_market_cap_status


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def select_asof_market_cap(
    rows: Sequence[Mapping[str, Any]],
    valuation_date: str,
    sanity_by_date: Mapping[str, str],
    quarantined_dates: Optional[Set[str]] = None,
    max_staleness_days: int = 7,
) -> Optional[Dict[str, Any]]:
    """Return the latest observation and its date; never cross quarantine."""
    target = date.fromisoformat(valuation_date)
    eligible = []
    for row in rows:
        row_date = _parse_date(row.get("date"))
        value = row.get("market_cap", row.get("marketCap"))
        if row_date is not None and row_date <= target and value is not None:
            eligible.append((row_date, row, float(value)))
    if not eligible:
        return None
    selected_date, selected_row, market_cap = max(eligible, key=lambda item: item[0])
    status = sanity_by_date.get(selected_date.isoformat())
    if market_cap <= 0 or not accepted_market_cap_status(str(status)):
        return None
    quarantine = quarantined_dates or set()
    if any(selected_date <= date.fromisoformat(value) <= target
           for value in quarantine):
        return None
    staleness = (target - selected_date).days
    if staleness > max_staleness_days:
        return None
    return {
        **dict(selected_row),
        "date": selected_date.isoformat(),
        "market_cap": market_cap,
        "sanity_status": status,
        "staleness_days": staleness,
    }


def select_four_continuous_asof_quarters(
    rows: Sequence[Mapping[str, Any]],
    valuation_date: str,
    trading_dates: Iterable[str],
) -> Optional[List[Dict[str, Any]]]:
    """Select four latest visible GAAP quarters, conservatively.

    FMP does not document the timezone of ``acceptedDate``. A statement is
    therefore visible from the next SOXX trading day after its acceptance
    calendar date, never from that same close.
    """
    target = date.fromisoformat(valuation_date)
    candidates: Dict[str, Dict[str, Any]] = {}
    accepted_by_fiscal: Dict[str, datetime] = {}
    malformed_fiscal_dates = set()
    for raw in rows:
        period = str(raw.get("period") or "").upper()
        if not period.startswith("Q"):
            continue
        fiscal = _parse_date(raw.get("date"))
        if fiscal is None or fiscal > target:
            continue
        accepted = _parse_timestamp(
            raw.get("accepted_date", raw.get("acceptedDate")))
        if accepted is None:
            malformed_fiscal_dates.add(fiscal.isoformat())
            continue
        try:
            visible = next_trading_date(accepted.date().isoformat(), trading_dates)
        except ValueError:
            continue
        if date.fromisoformat(visible) > target:
            continue
        income = raw.get("net_income", raw.get("netIncome"))
        if income is None:
            malformed_fiscal_dates.add(fiscal.isoformat())
            continue
        try:
            numeric_income = float(income)
        except (TypeError, ValueError):
            malformed_fiscal_dates.add(fiscal.isoformat())
            continue
        fiscal_text = fiscal.isoformat()
        if (fiscal_text not in candidates
                or accepted > accepted_by_fiscal[fiscal_text]):
            candidates[fiscal_text] = {
                **dict(raw),
                "date": fiscal_text,
                "accepted_date": raw.get(
                    "accepted_date", raw.get("acceptedDate")),
                "net_income": numeric_income,
                "reported_currency": str(raw.get(
                    "reported_currency", raw.get("reportedCurrency", ""))).upper(),
                "visibility_date": visible,
            }
            accepted_by_fiscal[fiscal_text] = accepted
    if len(candidates) < 4:
        return None
    selected = [candidates[key] for key in sorted(candidates)[-4:]]
    if any(value >= selected[0]["date"] for value in malformed_fiscal_dates):
        return None
    fiscal_dates = [date.fromisoformat(row["date"]) for row in selected]
    if any(not 60 <= (later - earlier).days <= 120
           for earlier, later in zip(fiscal_dates, fiscal_dates[1:])):
        return None
    return selected


def select_asof_fx(
    currency: str,
    rows: Sequence[Mapping[str, Any]],
    valuation_date: str,
    max_staleness_days: int = 7,
) -> Optional[Dict[str, Any]]:
    normalized = str(currency).upper()
    if normalized == "USD":
        return {
            "currency": "USD", "date": valuation_date,
            "usd_per_unit": 1.0, "source_symbol": "USD", "staleness_days": 0,
        }
    target = date.fromisoformat(valuation_date)
    eligible = []
    for row in rows:
        row_date = _parse_date(row.get("date"))
        rate = row.get("usd_per_unit")
        if row_date is not None and row_date <= target and rate is not None:
            eligible.append((row_date, row, float(rate)))
    if not eligible:
        return None
    selected_date, selected_row, rate = max(eligible, key=lambda item: item[0])
    staleness = (target - selected_date).days
    if (not is_plausible_usd_per_unit(
            normalized, rate, selected_row.get("source_symbol"))
            or staleness > max_staleness_days):
        return None
    return {
        **dict(selected_row), "currency": normalized,
        "date": selected_date.isoformat(), "usd_per_unit": rate,
        "staleness_days": staleness,
    }


def compute_member_ttm_income_usd(
    quarters: Sequence[Mapping[str, Any]],
    fx_by_currency: Mapping[str, Sequence[Mapping[str, Any]]],
    valuation_date: str,
) -> Optional[Dict[str, Any]]:
    if len(quarters) != 4:
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
            "accepted_date": row["accepted_date"],
            "visibility_date": row.get("visibility_date"),
            "currency": currency,
            "net_income_local": local_income,
            "fx_date": fx["date"],
            "usd_per_unit": fx["usd_per_unit"],
            "net_income_usd": usd_income,
        })
    return {"ttm_net_income_usd": total, "quarters": evidence}


def merge_covered_weights(
    holding_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    primaries = {
        str(row.get("symbol")).upper()
        for row in holding_rows
        if row.get("included") == 1 and row.get("symbol")
    }
    weights: Dict[str, float] = {}
    eligible_weight = 0.0
    orphan_targets: Dict[str, float] = {}
    warnings = []
    for row in holding_rows:
        is_primary = row.get("included") == 1 and row.get("symbol")
        covered_by = row.get("covered_by")
        if not is_primary and not covered_by:
            continue
        try:
            weight = float(row.get("weight_pct"))
        except (TypeError, ValueError):
            raise ValueError("eligible holding weight must be numeric") from None
        if weight < 0:
            raise ValueError("eligible holding weight must be non-negative")
        eligible_weight += weight
        if is_primary:
            symbol = str(row["symbol"]).upper()
            weights[symbol] = weights.get(symbol, 0.0) + weight
        else:
            target = str(covered_by).upper()
            if target in primaries:
                weights[target] = weights.get(target, 0.0) + weight
            else:
                orphan_targets[target] = orphan_targets.get(target, 0.0) + weight
    for target in sorted(orphan_targets):
        warnings.append(f"covered_by_target_missing:{target}")
    return {
        "weights": weights,
        "eligible_weight": eligible_weight,
        "orphan_covered_weight": sum(orphan_targets.values()),
        "orphan_targets": orphan_targets,
        "warnings": warnings,
    }


def _covered_members(
    members: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    covered = []
    for member in members:
        market_cap = member.get("market_cap")
        income = member.get("ttm_net_income_usd")
        weight = member.get("weight_pct")
        if market_cap is None or income is None or weight is None:
            continue
        if float(market_cap) <= 0 or float(weight) < 0:
            continue
        covered.append({
            **dict(member),
            "market_cap": float(market_cap),
            "ttm_net_income_usd": float(income),
            "weight_pct": float(weight),
        })
    return covered


def compute_weighted_ttm_pe_proxy(
    members: Sequence[Mapping[str, Any]],
    *,
    eligible_weight: float,
    minimum_weight_coverage: float = 0.90,
) -> Dict[str, Any]:
    if eligible_weight <= 0:
        raise ValueError("eligible_weight must be positive")
    covered = _covered_members(members)
    covered_weight = sum(row["weight_pct"] for row in covered)
    coverage = covered_weight / eligible_weight
    weighted_yield = None
    if covered_weight > 0:
        weighted_yield = sum(
            row["weight_pct"]
            * row["ttm_net_income_usd"] / row["market_cap"]
            for row in covered
        ) / covered_weight
    pe = (1.0 / weighted_yield
          if (coverage >= minimum_weight_coverage
              and weighted_yield is not None and weighted_yield > 0)
          else None)
    return {
        "pe": pe,
        "weighted_earnings_yield": weighted_yield,
        "eligible_weight": eligible_weight,
        "covered_weight": covered_weight,
        "weight_coverage": coverage,
        "covered_members": covered,
    }


def compute_uncapped_mcap_basket_pe(
    members: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    covered = _covered_members(members)
    market_cap = sum(row["market_cap"] for row in covered)
    income = sum(row["ttm_net_income_usd"] for row in covered)
    return {
        "pe": market_cap / income if market_cap > 0 and income > 0 else None,
        "covered_market_cap": market_cap,
        "ttm_net_income_usd": income,
        "covered_members": covered,
    }


def compute_daily_basket_valuation(
    *,
    valuation_date: str,
    holding_rows: Sequence[Mapping[str, Any]],
    income_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    market_cap_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    fx_by_currency: Mapping[str, Sequence[Mapping[str, Any]]],
    sanity_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    trading_dates: Iterable[str],
    composition: Mapping[str, Any],
    basket_symbol: str = "SOXX",
    methodology_version: str = "1.0",
) -> Dict[str, Any]:
    weight_info = merge_covered_weights(holding_rows)
    eligible_weight = float(weight_info["eligible_weight"])
    if eligible_weight <= 0:
        raise ValueError("snapshot has no eligible equity weight")
    members = []
    metric_members = []
    mcap_weight = 0.0
    income_weight = 0.0
    fx_weight = 0.0
    sanity_evidence = []
    warnings = list(weight_info["warnings"])
    warnings.extend(str(value) for value in composition.get(
        "snapshot_warnings", []))
    alias_by_symbol = {
        str(row["symbol"]).upper(): {
            "symbol": str(row["alias_symbol"]).upper(),
            "mode": str(row.get("alias_mode") or "fallback"),
            "reason": str(row.get("alias_reason") or "configured corporate alias"),
        }
        for row in holding_rows
        if row.get("included") == 1 and row.get("symbol")
        and row.get("alias_symbol")
    }

    for raw_symbol, weight in sorted(weight_info["weights"].items()):
        alias = alias_by_symbol.get(raw_symbol)
        if alias and alias["mode"] == "authoritative":
            candidates = [alias["symbol"]]
        else:
            candidates = [raw_symbol]
        if (alias and alias["mode"] != "authoritative"
                and alias["symbol"] != raw_symbol):
            candidates.append(alias["symbol"])
        evaluated = []
        for candidate in candidates:
            classifications = sanity_by_symbol.get(candidate, [])
            status_by_date = {
                str(row["date"]): str(row.get("status"))
                for row in classifications
            }
            quarantine = {
                value for value, status in status_by_date.items()
                if not accepted_market_cap_status(status)
            }
            candidate_mcap = select_asof_market_cap(
                market_cap_by_symbol.get(candidate, []), valuation_date,
                status_by_date, quarantine)
            candidate_quarters = select_four_continuous_asof_quarters(
                income_by_symbol.get(candidate, []), valuation_date, trading_dates)
            candidate_income = (
                compute_member_ttm_income_usd(
                    candidate_quarters, fx_by_currency, valuation_date)
                if candidate_quarters is not None else None)
            evaluated.append({
                "symbol": candidate, "market_cap": candidate_mcap,
                "quarters": candidate_quarters, "income": candidate_income,
                "classifications": classifications,
            })
        # Raw data is authoritative whenever complete. Alias is a fallback for
        # the whole member data key, never an unconditional ticker rewrite or
        # a source-by-source splice.
        selected = next((item for item in evaluated
                         if item["market_cap"] is not None
                         and item["income"] is not None), evaluated[0])
        symbol = selected["symbol"]
        market_cap = selected["market_cap"]
        quarters = selected["quarters"]
        income_result = selected["income"]
        alias_used = symbol != raw_symbol
        selected_classifications = selected["classifications"]
        status_by_date = {str(row["date"]): str(row.get("status"))
                          for row in selected_classifications}
        latest_observation = max((
            row for row in market_cap_by_symbol.get(symbol, [])
            if str(row.get("date") or "") <= valuation_date
        ), key=lambda row: str(row["date"]), default=None)
        # Persist anomalies for every evaluated data key. When raw CREE is
        # rejected and WOLF is selected, the raw quarantine is still part of
        # the audit trail rather than disappearing behind the fallback.
        for candidate in evaluated:
            for classification in candidate["classifications"]:
                if (str(classification["date"]) <= valuation_date
                        and (classification.get("candidate")
                             or classification.get("forced_refresh_planned")
                             or not accepted_market_cap_status(
                                 str(classification.get("status"))))):
                    sanity_evidence.append({
                        "raw_symbol": raw_symbol,
                        "symbol": candidate["symbol"],
                        **dict(classification),
                    })
        if market_cap is not None:
            mcap_weight += weight
        if quarters is not None:
            income_weight += weight
        if income_result is not None:
            fx_weight += weight

        exclusion_reason = None
        if market_cap is None:
            exclusion_reason = "market_cap_missing_stale_or_quarantined"
        elif quarters is None:
            exclusion_reason = "four_visible_continuous_quarters_missing"
        elif income_result is None:
            exclusion_reason = "fx_missing_or_stale"

        evidence = {
            "symbol": symbol,
            "raw_symbol": raw_symbol,
            "resolved_symbol": symbol,
            "alias_mode": alias["mode"] if alias_used and alias else None,
            "alias_reason": alias["reason"] if alias_used and alias else None,
            "weight_pct": weight,
            "market_cap": market_cap["market_cap"] if market_cap else None,
            "market_cap_date": market_cap["date"] if market_cap else None,
            "market_cap_sanity_status": (
                market_cap["sanity_status"] if market_cap else None),
            "market_cap_exclusion_date": (
                latest_observation.get("date")
                if market_cap is None and latest_observation else None),
            "market_cap_exclusion_status": (
                status_by_date.get(str(latest_observation.get("date")))
                if market_cap is None and latest_observation else None),
            "fiscal_dates": [row["date"] for row in quarters] if quarters else [],
            "accepted_dates": (
                [row["accepted_date"] for row in quarters] if quarters else []),
            "fx_evidence": income_result["quarters"] if income_result else [],
            "ttm_net_income_usd": (
                income_result["ttm_net_income_usd"] if income_result else None),
            "exclusion_reason": exclusion_reason,
        }
        members.append(evidence)
        if exclusion_reason is None:
            metric_members.append(evidence)

    for target, weight in sorted(weight_info["orphan_targets"].items()):
        members.append({
            "symbol": target, "raw_symbol": target,
            "resolved_symbol": target, "alias_mode": None, "alias_reason": None,
            "weight_pct": weight,
            "market_cap": None, "ttm_net_income_usd": None,
            "fiscal_dates": [], "accepted_dates": [], "fx_evidence": [],
            "exclusion_reason": "covered_by_target_missing",
        })

    primary = compute_weighted_ttm_pe_proxy(
        metric_members, eligible_weight=eligible_weight)
    secondary = compute_uncapped_mcap_basket_pe(primary["covered_members"])
    available_date = str(composition["composition_available_date"])
    anchor_date = str(composition.get(
        "anchor_trading_date", composition["holding_date"]))
    return {
        "basket_symbol": basket_symbol.upper(),
        "valuation_date": valuation_date,
        "holding_date": composition["holding_date"],
        "composition_effective_date": composition["composition_effective_date"],
        "composition_available_date": available_date,
        "is_ex_post_composition": int(valuation_date < available_date),
        "weight_basis": composition["weight_basis"],
        "data_quality_tier": composition["data_quality_tier"],
        "is_observed_weight_date": int(valuation_date == anchor_date),
        "eligible_weight": eligible_weight,
        "covered_weight": primary["covered_weight"],
        "rebalance_weighted_ttm_pe_gaap_proxy": primary["pe"],
        "weighted_earnings_yield": primary["weighted_earnings_yield"],
        "uncapped_mcap_basket_pe_gaap": secondary["pe"],
        "covered_market_cap": secondary["covered_market_cap"],
        "ttm_net_income_usd": secondary["ttm_net_income_usd"],
        "member_count": len(weight_info["weights"])
        + len(weight_info["orphan_targets"]),
        "covered_count": len(primary["covered_members"]),
        "weight_coverage": primary["weight_coverage"],
        "mcap_weight_coverage": mcap_weight / eligible_weight,
        "income_weight_coverage": income_weight / eligible_weight,
        "fx_weight_coverage": fx_weight / eligible_weight,
        "members_json": members,
        "warnings_json": warnings,
        "mcap_sanity_json": sanity_evidence,
        "methodology_version": methodology_version,
    }
