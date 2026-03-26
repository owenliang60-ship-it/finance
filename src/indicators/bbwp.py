"""
BBWP indicator (Bollinger Band Width Percentile).

BBW  = (UpperBand - LowerBand) / MiddleBand
BBWP = Percentile(BBW, lookback)
"""

from __future__ import annotations

from typing import Dict

import pandas as pd


def calculate_bbw(
    prices: pd.Series,
    bb_period: int = 13,
    bb_std: float = 1.0,
) -> pd.Series:
    """
    Calculate Bollinger Band Width.

    Args:
        prices: Close prices in chronological order.
        bb_period: Rolling window for the middle band.
        bb_std: Standard deviation multiplier.

    Returns:
        Series of BBW values.
    """
    prices = prices.reset_index(drop=True).astype(float)
    middle = prices.rolling(window=bb_period, min_periods=bb_period).mean()
    std = prices.rolling(window=bb_period, min_periods=bb_period).std(ddof=0)

    upper = middle + bb_std * std
    lower = middle - bb_std * std
    width = upper - lower

    bbw = width / middle.replace(0, pd.NA)
    return bbw.astype(float)


def calculate_bbwp(
    prices: pd.Series,
    bb_period: int = 13,
    bb_std: float = 1.0,
    lookback: int = 252,
) -> pd.Series:
    """
    Calculate BBWP as the percentile of BBW over a rolling lookback.
    """
    if len(prices) < bb_period + lookback:
        return pd.Series(index=prices.index, dtype=float)

    bbw = calculate_bbw(prices, bb_period=bb_period, bb_std=bb_std)
    bbwp = pd.Series(index=bbw.index, dtype=float)

    for i in range(lookback, len(bbw)):
        current = bbw.iloc[i]
        historical = bbw.iloc[i - lookback:i].dropna()

        if pd.isna(current) or historical.empty:
            continue

        count_le = (historical <= current).sum()
        bbwp.iloc[i] = count_le / len(historical) * 100

    return bbwp


def analyze_bbwp(
    df: pd.DataFrame,
    bb_period: int = 13,
    bb_std: float = 1.0,
    lookback: int = 252,
) -> Dict:
    """
    Analyze the latest BBWP state for a price DataFrame.
    """
    result = {
        "current": None,
        "previous": None,
        "turning_up": False,
        "turning_down": False,
        "signal": "normal",
        "description": "",
    }

    if df is None or df.empty or "close" not in df.columns:
        result["description"] = "数据不足"
        return result

    if "date" in df.columns:
        df = df.sort_values("date")

    bbwp = calculate_bbwp(
        df["close"],
        bb_period=bb_period,
        bb_std=bb_std,
        lookback=lookback,
    )
    valid = bbwp.dropna()

    if len(valid) < 2:
        result["description"] = "BBWP 计算数据不足"
        return result

    current = float(valid.iloc[-1])
    previous = float(valid.iloc[-2])
    turning_up = current > previous
    turning_down = current < previous

    result["current"] = round(current, 2)
    result["previous"] = round(previous, 2)
    result["turning_up"] = turning_up
    result["turning_down"] = turning_down

    if current >= 98 and turning_down:
        result["signal"] = "high_vol_contracting"
        result["description"] = (
            f"BBWP 高位回落 ({previous:.1f}→{current:.1f})，恐慌可能开始释放"
        )
    elif current >= 98:
        result["signal"] = "high_vol"
        result["description"] = f"BBWP={current:.1f}%，波动率处于极高区间"
    elif current <= 5 and turning_up:
        result["signal"] = "squeeze_expanding"
        result["description"] = f"BBWP 低位抬头 ({previous:.1f}→{current:.1f})，挤压可能结束"
    elif current <= 5:
        result["signal"] = "squeeze"
        result["description"] = f"BBWP={current:.1f}%，波动率极度收缩"
    else:
        result["signal"] = "normal"
        result["description"] = f"BBWP={current:.1f}%，波动率常态"

    return result
