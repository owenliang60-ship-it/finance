"""Narrow USD-per-local-unit validation for SOXX income conversion."""
from typing import Any, Optional


# Each band is roughly a 1.5-2x envelope around the currency's realised range
# over the backfill window, wide enough to survive a devaluation and narrow
# enough that an inverted quote (1/rate) can never land inside it.
USD_PER_UNIT_BOUNDS = {
    "EUR": (0.50, 2.00),
    "TWD": (0.02, 0.05),
    # PDD (QQQ) files in CNY. CNYUSD has traded 0.1366-0.1580 since 2021;
    # the inverse quote is ~7, three decades away from the upper bound.
    "CNY": (0.10, 0.20),
    # SK hynix ADS (CUSIP 78392B206) reports in KRW. Reviewed 2026-09-26:
    # FMP 5y KRWUSD range 0.000637-0.000852; Fed H.10 confirms orientation.
    "KRW": (0.0004, 0.0013),
}


def validate_usd_per_unit(
    currency: str,
    rate: Any,
    source_symbol: Optional[str] = None,
) -> float:
    """Return a plausible ``<currency>USD`` rate or fail closed.

    The basket sources include EUR, TWD, CNY and KRW reporters. Unknown
    non-USD currencies are rejected until an explicit, reviewed range is
    added; this prevents silently accepting an inverted quote.
    """
    normalized = str(currency or "").upper().strip()
    try:
        numeric = float(rate)
    except (TypeError, ValueError) as exc:
        raise ValueError("USD-per-unit rate must be numeric") from exc
    if normalized == "USD":
        if numeric != 1.0 or source_symbol not in (None, "", "USD"):
            raise ValueError("USD-per-unit USD rate must equal 1")
        return numeric
    bounds = USD_PER_UNIT_BOUNDS.get(normalized)
    if bounds is None:
        raise ValueError(f"USD-per-unit currency not allowlisted: {normalized}")
    expected_symbol = f"{normalized}USD"
    if str(source_symbol or "").upper().strip() != expected_symbol:
        raise ValueError(
            f"USD-per-unit source must be {expected_symbol}, got {source_symbol!r}")
    lower, upper = bounds
    if not lower <= numeric <= upper:
        raise ValueError(
            f"USD-per-unit {normalized} rate outside [{lower}, {upper}]")
    return numeric


def is_plausible_usd_per_unit(
    currency: str,
    rate: Any,
    source_symbol: Optional[str] = None,
) -> bool:
    try:
        validate_usd_per_unit(currency, rate, source_symbol)
    except ValueError:
        return False
    return True
