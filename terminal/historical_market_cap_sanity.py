"""Pure historical market-cap sanity plus a narrow forced-refresh adapter."""
from bisect import bisect_left
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


ACCEPTED_MARKET_CAP_STATUSES = frozenset({
    "clean", "price_move_plausible", "split_consistent",
})


def accepted_market_cap_status(status: str) -> bool:
    return status in ACCEPTED_MARKET_CAP_STATUSES


def quarantine_market_cap_dates(
    classifications: Sequence[Mapping[str, Any]],
) -> Set[str]:
    return {
        str(row["date"])
        for row in classifications
        if not accepted_market_cap_status(str(row.get("status")))
    }


def classify_market_cap_candidate(
    *,
    anomaly_open: bool,
    implied_shares: Optional[float],
    expected_shares: Optional[float],
    split_event: bool,
    market_cap_jump: bool,
    price_aligned: bool,
    share_tolerance: float = 0.20,
) -> Dict[str, Any]:
    """Classify one row against the last clean, split-adjusted share regime."""
    if implied_shares is None or expected_shares is None:
        return {"status": "unresolved", "anomaly_open": True,
                "normalization_recovery": False}
    shares_match = abs(implied_shares / expected_shares - 1.0) <= share_tolerance
    if anomaly_open:
        if shares_match:
            return {
                "status": "split_consistent" if split_event else "clean",
                "anomaly_open": False,
                "normalization_recovery": True,
            }
        return {"status": "invalid_mcap", "anomaly_open": True,
                "normalization_recovery": False}
    if split_event:
        return {
            "status": "split_consistent" if shares_match else "invalid_mcap",
            "anomaly_open": not shares_match,
            "normalization_recovery": False,
        }
    if market_cap_jump:
        if price_aligned:
            return {"status": "price_move_plausible", "anomaly_open": False,
                    "normalization_recovery": False}
        return {"status": "invalid_mcap", "anomaly_open": True,
                "normalization_recovery": False}
    return {"status": "clean", "anomaly_open": False,
            "normalization_recovery": False}


def _split_candidate_dates(
    ordered_dates: Sequence[str], split_dates: Iterable[str],
) -> Set[str]:
    candidates: Set[str] = set()
    for split_date in split_dates:
        position = bisect_left(ordered_dates, split_date)
        if (position < len(ordered_dates)
                and ordered_dates[position] == split_date):
            for index in range(max(0, position - 1),
                               min(len(ordered_dates), position + 2)):
                candidates.add(ordered_dates[index])
        else:
            # Non-trading vendor date: inspect the immediately surrounding
            # observations, but do not widen beyond one trading observation.
            if position < len(ordered_dates):
                candidates.add(ordered_dates[position])
            if position > 0:
                candidates.add(ordered_dates[position - 1])
    return candidates


def scan_market_cap_candidates(
    market_cap_rows: Sequence[Mapping[str, Any]],
    price_rows: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
    *,
    jump_threshold: float = 0.40,
    return_alignment_tolerance: float = 0.15,
    share_tolerance: float = 0.20,
) -> List[Dict[str, Any]]:
    """Classify every market-cap row in date order.

    The state anchor is the last accepted implied-share regime. Announced
    split ratios update that anchor even while an anomaly is open; therefore a
    superficially correct adjacent-day ratio cannot close KLAC's bad window.
    """
    mcaps: Dict[str, Mapping[str, Any]] = {}
    for row in market_cap_rows:
        row_date = str(row.get("date") or "")
        if not row_date or row_date in mcaps:
            raise ValueError("market-cap dates must be non-empty and unique")
        value = row.get("market_cap", row.get("marketCap"))
        if value is None or float(value) <= 0:
            raise ValueError("market_cap must be positive")
        mcaps[row_date] = {**row, "market_cap": float(value)}
    if not mcaps:
        return []

    prices: Dict[str, float] = {}
    for row in price_rows:
        row_date = str(row.get("date") or "")
        close = row.get("close")
        if row_date and close is not None and float(close) > 0:
            prices[row_date] = float(close)

    split_by_date: Dict[str, float] = {}
    for row in split_rows:
        split_date = str(row.get("date") or "")
        try:
            ratio = float(row["numerator"]) / float(row["denominator"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError("invalid split ratio") from exc
        if not split_date or ratio <= 0:
            raise ValueError("invalid split event")
        split_by_date[split_date] = split_by_date.get(split_date, 1.0) * ratio

    ordered_dates = sorted(mcaps)
    split_adjacent = _split_candidate_dates(ordered_dates, split_by_date)
    effective_splits: Dict[str, float] = {}
    effective_split_sources: Dict[str, List[str]] = {}
    for source_date, ratio in split_by_date.items():
        position = bisect_left(ordered_dates, source_date)
        if position >= len(ordered_dates):
            continue
        effective_date = ordered_dates[position]
        effective_splits[effective_date] = (
            effective_splits.get(effective_date, 1.0) * ratio)
        effective_split_sources.setdefault(effective_date, []).append(source_date)
    expected_shares: Optional[float] = None
    anomaly_open = False
    previous_mcap: Optional[float] = None
    previous_price: Optional[float] = None
    previous_observed_shares: Optional[float] = None
    output: List[Dict[str, Any]] = []

    for row_date in ordered_dates:
        market_cap = float(mcaps[row_date]["market_cap"])
        close = prices.get(row_date)
        implied_shares = market_cap / close if close is not None else None
        mcap_return = (market_cap / previous_mcap - 1.0
                       if previous_mcap is not None else None)
        price_return = (close / previous_price - 1.0
                        if close is not None and previous_price is not None else None)
        implied_share_ratio = (
            implied_shares / previous_observed_shares
            if implied_shares is not None and previous_observed_shares is not None
            else None)

        # Weekend/holiday vendor events take effect on the first following
        # market-cap observation, not only on an exact date match.
        split_ratio = effective_splits.get(row_date)
        split_adjustment_applied = False
        split_adjustment_mode = None
        if split_ratio is not None and expected_shares is not None:
            # FMP daily prices and historical market caps may both be
            # back-adjusted across the entire history. Apply the corporate
            # action only when the observed implied-share count actually
            # changes by the announced ratio; otherwise applying it again
            # creates a permanent false anomaly (MCHP 2021-10-13).
            if (implied_share_ratio is not None
                    and abs(implied_share_ratio / split_ratio - 1.0)
                    <= share_tolerance):
                expected_shares *= split_ratio
                split_adjustment_applied = True
                split_adjustment_mode = "observed_share_change"
            elif (implied_share_ratio is not None
                  and abs(implied_share_ratio - 1.0) <= share_tolerance):
                split_adjustment_mode = "already_back_adjusted"
            else:
                split_adjustment_mode = "unconfirmed"
        expected_for_evidence = expected_shares
        jump = mcap_return is not None and abs(mcap_return) > jump_threshold
        adjacent = row_date in split_adjacent
        price_aligned = (
            mcap_return is not None
            and price_return is not None
            and implied_share_ratio is not None
            and abs(mcap_return - price_return) <= return_alignment_tolerance
            and abs(implied_share_ratio - 1.0) <= share_tolerance
        )
        was_open = anomaly_open

        if expected_shares is None and implied_shares is not None:
            classification = {
                "status": "clean", "anomaly_open": False,
                "normalization_recovery": False,
            }
        else:
            classification = classify_market_cap_candidate(
                anomaly_open=anomaly_open,
                implied_shares=implied_shares,
                expected_shares=expected_shares,
                split_event=split_ratio is not None,
                market_cap_jump=jump,
                price_aligned=price_aligned,
                share_tolerance=share_tolerance,
            )
        anomaly_open = bool(classification["anomaly_open"])
        status = str(classification["status"])

        if accepted_market_cap_status(status) and implied_shares is not None:
            expected_shares = implied_shares

        if jump and adjacent:
            reason = "market_cap_jump+split_adjacent"
        elif jump:
            reason = "market_cap_jump"
        elif split_ratio is not None:
            reason = "split_event"
        elif adjacent:
            reason = "split_adjacent"
        elif was_open or anomaly_open:
            reason = "anomaly_interval"
        else:
            reason = None

        output.append({
            "symbol": str(mcaps[row_date].get("symbol") or "").upper(),
            "date": row_date,
            "market_cap": market_cap,
            "close": close,
            "mcap_return": mcap_return,
            "price_return": price_return,
            "implied_shares": implied_shares,
            "implied_share_ratio": implied_share_ratio,
            "expected_shares": expected_for_evidence,
            "split_ratio": split_ratio,
            "split_source_dates": effective_split_sources.get(row_date, []),
            "split_adjustment_applied": split_adjustment_applied,
            "split_adjustment_mode": split_adjustment_mode,
            "candidate": bool(jump or adjacent or was_open or anomaly_open),
            "candidate_reason": reason,
            "status": status,
            "normalization_recovery": bool(
                classification["normalization_recovery"]),
        })
        previous_mcap = market_cap
        previous_price = close
        previous_observed_shares = implied_shares

    return output


def build_forced_refresh_windows(
    classifications: Sequence[Mapping[str, Any]],
    trading_dates: Sequence[str],
    *,
    pad: int = 5,
) -> List[Dict[str, Any]]:
    if pad < 0:
        raise ValueError("pad must be non-negative")
    calendar = sorted(set(trading_dates))
    position = {value: index for index, value in enumerate(calendar)}
    triggers = sorted({
        str(row["date"])
        for row in classifications
        if not accepted_market_cap_status(str(row.get("status")))
    })
    if not triggers:
        return []
    if any(value not in position for value in triggers):
        raise ValueError("classification date missing from trading calendar")

    groups: List[List[str]] = []
    for trigger in triggers:
        if not groups or position[trigger] > position[groups[-1][-1]] + 1:
            groups.append([trigger])
        else:
            groups[-1].append(trigger)

    expanded: List[Dict[str, Any]] = []
    for group in groups:
        start_index = max(0, position[group[0]] - pad)
        end_index = min(len(calendar) - 1, position[group[-1]] + pad)
        window = {
            "from_date": calendar[start_index],
            "to_date": calendar[end_index],
            "trigger_dates": list(group),
            "_start": start_index,
            "_end": end_index,
        }
        if expanded and start_index <= expanded[-1]["_end"]:
            expanded[-1]["to_date"] = calendar[max(expanded[-1]["_end"], end_index)]
            expanded[-1]["_end"] = max(expanded[-1]["_end"], end_index)
            expanded[-1]["trigger_dates"].extend(group)
        else:
            expanded.append(window)
    for window in expanded:
        window.pop("_start")
        window.pop("_end")
    return expanded


def refresh_market_cap_windows(
    symbol: str,
    windows: Sequence[Mapping[str, str]],
    client: Any,
    store: Any,
) -> List[Dict[str, Any]]:
    """Refetch using the existing client and authoritative range-replace CRUD."""
    results = []
    for window in windows:
        start = window["from_date"]
        end = window["to_date"]
        rows = client.get_historical_market_cap(
            symbol, from_date=start, to_date=end)
        store.replace_historical_market_cap_range(symbol, start, end, rows)
        results.append({"from_date": start, "to_date": end, "rows": len(rows)})
    return results
