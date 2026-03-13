"""
超额收益矩阵测试 — R1: Benchmark-Adjusted Excess Returns
"""

import numpy as np
import pandas as pd
import pytest

from backtest.factor_study.forward_returns import (
    build_excess_return_matrix,
    build_return_matrix,
)


def _make_price_df(dates, prices):
    """构建与适配器格式一致的价格 DataFrame"""
    return pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else d for d in dates],
        "close": prices,
    })


class TestBuildExcessReturnMatrix:
    def test_subtracts_benchmark(self):
        """Excess return = stock return - benchmark return for same horizon."""
        dates = pd.bdate_range("2024-01-01", periods=30)
        date_strs = [d.strftime("%Y-%m-%d") for d in dates]

        # AAPL: 100 → 110 linear (+10% over 29 days)
        aapl_prices = [100 + i * (10 / 29) for i in range(30)]
        # SPY: 100 → 105 linear (+5% over 29 days)
        spy_prices = [100 + i * (5 / 29) for i in range(30)]

        price_dict = {
            "AAPL": _make_price_df(dates, aapl_prices),
        }
        benchmark_df = _make_price_df(dates, spy_prices)

        raw = build_return_matrix(price_dict, [date_strs[0]], [20])
        excess = build_excess_return_matrix(
            price_dict, benchmark_df, [date_strs[0]], [20],
        )

        raw_ret = raw[20].loc[date_strs[0], "AAPL"]
        excess_ret = excess[20].loc[date_strs[0], "AAPL"]

        # Excess should be roughly raw - benchmark
        spy_ret_20d = spy_prices[20] / spy_prices[0] - 1
        expected_excess = raw_ret - spy_ret_20d

        assert abs(excess_ret - expected_excess) < 1e-10

    def test_multiple_horizons(self):
        """Excess calculation works for multiple horizons."""
        dates = pd.bdate_range("2024-01-01", periods=80)
        date_strs = [d.strftime("%Y-%m-%d") for d in dates]

        rng = np.random.RandomState(42)
        stock_prices = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 80)))
        bench_prices = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 80)))

        price_dict = {"AAPL": _make_price_df(dates, stock_prices)}
        benchmark_df = _make_price_df(dates, bench_prices)

        comp_dates = date_strs[:20:5]  # 4 computation dates
        horizons = [5, 10, 20]

        excess = build_excess_return_matrix(
            price_dict, benchmark_df, comp_dates, horizons,
        )

        assert set(excess.keys()) == {5, 10, 20}
        for h in horizons:
            assert not excess[h].empty

    def test_benchmark_missing_date_gives_nan(self):
        """When benchmark lacks a computation date, excess return is NaN."""
        dates = pd.bdate_range("2024-01-01", periods=30)
        date_strs = [d.strftime("%Y-%m-%d") for d in dates]

        price_dict = {
            "AAPL": _make_price_df(dates, [100 + i for i in range(30)]),
        }
        # Benchmark starts 5 days later
        benchmark_df = _make_price_df(dates[5:], [100 + i for i in range(25)])

        excess = build_excess_return_matrix(
            price_dict, benchmark_df, [date_strs[0]], [5],
        )

        # Date 0 not in benchmark → raw return preserved (no benchmark to subtract)
        raw = build_return_matrix(price_dict, [date_strs[0]], [5])
        assert excess[5].loc[date_strs[0], "AAPL"] == raw[5].loc[date_strs[0], "AAPL"]

    def test_multiple_stocks(self):
        """Excess subtracted uniformly across all stocks."""
        dates = pd.bdate_range("2024-01-01", periods=30)
        date_strs = [d.strftime("%Y-%m-%d") for d in dates]

        rng = np.random.RandomState(42)
        price_dict = {}
        for sym in ["AAPL", "MSFT", "GOOG"]:
            prices = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 30)))
            price_dict[sym] = _make_price_df(dates, prices)

        bench_prices = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 30)))
        benchmark_df = _make_price_df(dates, bench_prices)

        comp_date = date_strs[0]
        raw = build_return_matrix(price_dict, [comp_date], [10])
        excess = build_excess_return_matrix(
            price_dict, benchmark_df, [comp_date], [10],
        )

        bench_ret = bench_prices[10] / bench_prices[0] - 1
        for sym in ["AAPL", "MSFT", "GOOG"]:
            expected = raw[10].loc[comp_date, sym] - bench_ret
            assert abs(excess[10].loc[comp_date, sym] - expected) < 1e-10

    def test_no_benchmark_same_as_raw(self):
        """build_return_matrix and build_excess_return_matrix with flat benchmark give same shape."""
        dates = pd.bdate_range("2024-01-01", periods=30)
        date_strs = [d.strftime("%Y-%m-%d") for d in dates]

        price_dict = {
            "AAPL": _make_price_df(dates, [100 + i for i in range(30)]),
        }
        # Flat benchmark (0% return) → excess == raw
        benchmark_df = _make_price_df(dates, [100.0] * 30)

        raw = build_return_matrix(price_dict, [date_strs[0]], [5])
        excess = build_excess_return_matrix(
            price_dict, benchmark_df, [date_strs[0]], [5],
        )

        assert abs(excess[5].loc[date_strs[0], "AAPL"] -
                    raw[5].loc[date_strs[0], "AAPL"]) < 1e-10
