"""Helpers for constructing OCC-standard option symbols."""

from datetime import datetime


def build_occ_symbol(symbol: str, expiration: str, strike: float, side: str) -> str:
    """Build an OCC option symbol.

    Format: SYMBOL + YYMMDD + C/P + STRIKE*1000 padded to 8 digits
    Example: AAPL 2026-03-21 Call @ 200.0 -> AAPL260321C00200000
    """
    side_upper = side.upper()
    if side_upper not in ("CALL", "PUT"):
        raise ValueError(f"side must be CALL or PUT, got {side}")

    exp_dt = datetime.strptime(expiration, "%Y-%m-%d")
    date_part = exp_dt.strftime("%y%m%d")
    cp = "C" if side_upper == "CALL" else "P"
    strike_int = int(round(strike * 1000))
    strike_part = f"{strike_int:08d}"
    return f"{symbol.upper()}{date_part}{cp}{strike_part}"
