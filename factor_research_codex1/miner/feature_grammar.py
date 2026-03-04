"""
Grammar definition for the controlled factor DSL.

The DSL uses function-call expressions, e.g.:
    rank_cs(zscore(ret_n(close, 20), 60))
"""

from typing import Set


PRICE_FIELDS: Set[str] = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "dollar_volume",
    "vwap",
}

# 2-arg, first arg is an expression, second arg is a window integer.
WINDOW_FUNCS: Set[str] = {
    "ret_n",
    "vol_n",
    "rsi",
    "sma",
    "ema",
    "zscore",
    "ts_rank",
    "lag",
    "delta",
}

# 1-arg expression transforms.
UNARY_FUNCS: Set[str] = {
    "abs",
    "normalize",
    "rank_cs",
}

# 2-arg expression combinators.
BINARY_FUNCS: Set[str] = {
    "add",
    "sub",
    "mul",
    "div",
}

# Misc operations with custom signatures.
CUSTOM_FUNCS: Set[str] = {
    "clip",   # clip(expr, lo, hi)
    "macd",   # macd(expr, fast, slow, signal)
    "atr",    # atr(high, low, close, window)
}

ALL_FUNCS: Set[str] = WINDOW_FUNCS | UNARY_FUNCS | BINARY_FUNCS | CUSTOM_FUNCS


def is_allowed_identifier(name: str) -> bool:
    """Return True if identifier is a permitted field name."""
    return name in PRICE_FIELDS


def is_allowed_function(name: str) -> bool:
    """Return True if function is in the DSL whitelist."""
    return name in ALL_FUNCS

