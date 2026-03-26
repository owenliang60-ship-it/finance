"""Tests for the BBWP indicator."""

import pandas as pd

from src.indicators.bbwp import calculate_bbw, calculate_bbwp, analyze_bbwp


def test_calculate_bbw_returns_series():
    prices = pd.Series([100 + i for i in range(30)], dtype=float)
    bbw = calculate_bbw(prices, bb_period=5, bb_std=1.0)
    assert len(bbw) == len(prices)
    assert bbw.dropna().iloc[-1] > 0


def test_calculate_bbwp_range():
    prices = pd.Series(
        [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93, 108],
        dtype=float,
    )
    bbwp = calculate_bbwp(prices, bb_period=3, bb_std=1.0, lookback=5)
    valid = bbwp.dropna()
    assert not valid.empty
    assert valid.min() >= 0
    assert valid.max() <= 100


def test_analyze_bbwp_detects_squeeze():
    prices = [100] * 30
    prices += [150, 50, 160, 40, 170, 30, 160, 40, 150, 50, 130, 70, 120, 80, 110, 90]
    df = pd.DataFrame({"close": prices})

    result = analyze_bbwp(df, bb_period=5, bb_std=1.0, lookback=10)

    assert result["current"] is not None
    assert result["signal"] == "squeeze"
    assert result["current"] <= 5


def test_analyze_bbwp_requires_close():
    result = analyze_bbwp(pd.DataFrame({"volume": [1, 2, 3]}))
    assert result["current"] is None
    assert result["description"] == "数据不足"
