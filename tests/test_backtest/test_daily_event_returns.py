import math

import pandas as pd

from backtest.research.daily_event_returns import (
    build_close_forward_return_matrices,
    build_prior_excess_return_matrix,
    build_t1open_excess_return_matrices,
)


def _make_price_frame(rows):
    return pd.DataFrame(rows, columns=["date", "open", "close"])


def test_build_t1open_excess_return_matrices_uses_next_open_and_exit_close():
    price_dict = {
        "AAA": _make_price_frame([
            ("2024-01-01", 100, 100),
            ("2024-01-02", 102, 103),
            ("2024-01-03", 104, 105),
            ("2024-01-04", 106, 107),
        ])
    }
    benchmark = _make_price_frame([
        ("2024-01-01", 200, 200),
        ("2024-01-02", 202, 202),
        ("2024-01-03", 204, 205),
        ("2024-01-04", 206, 206),
    ])

    matrices = build_t1open_excess_return_matrices(
        price_dict=price_dict,
        benchmark_df=benchmark,
        computation_dates=["2024-01-01", "2024-01-02"],
        horizons=[2],
    )

    result = matrices[2]
    aaa = float(result.loc["2024-01-01", "AAA"])
    expected_stock = 105 / 102 - 1.0
    expected_bench = 205 / 202 - 1.0
    assert abs(aaa - (expected_stock - expected_bench)) < 1e-12


def test_build_t1open_excess_return_matrices_returns_nan_when_horizon_insufficient():
    price_dict = {
        "AAA": _make_price_frame([
            ("2024-01-01", 100, 100),
            ("2024-01-02", 101, 101),
        ])
    }
    benchmark = _make_price_frame([
        ("2024-01-01", 200, 200),
        ("2024-01-02", 201, 201),
    ])

    matrices = build_t1open_excess_return_matrices(
        price_dict=price_dict,
        benchmark_df=benchmark,
        computation_dates=["2024-01-01"],
        horizons=[2],
    )
    assert math.isnan(float(matrices[2].loc["2024-01-01", "AAA"]))


def test_build_prior_excess_return_matrix():
    price_dict = {
        "AAA": _make_price_frame([
            ("2024-01-01", 100, 100),
            ("2024-01-02", 100, 110),
            ("2024-01-03", 100, 121),
        ])
    }
    benchmark = _make_price_frame([
        ("2024-01-01", 100, 100),
        ("2024-01-02", 100, 105),
        ("2024-01-03", 100, 110),
    ])

    matrix = build_prior_excess_return_matrix(
        price_dict=price_dict,
        benchmark_df=benchmark,
        computation_dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        lookback_days=1,
    )

    # 2024-01-03: stock 121/110 -1 = 10%; bench 110/105 -1 ≈ 4.7619%
    expected = 121 / 110 - 1.0 - (110 / 105 - 1.0)
    assert abs(float(matrix.loc["2024-01-03", "AAA"]) - expected) < 1e-12


def test_build_close_forward_return_matrices_uses_signal_day_close_to_future_close():
    price_dict = {
        "AAA": _make_price_frame([
            ("2024-01-01", 100, 100),
            ("2024-01-02", 101, 103),
            ("2024-01-03", 102, 106),
            ("2024-01-04", 103, 109),
        ])
    }

    matrices = build_close_forward_return_matrices(
        price_dict=price_dict,
        computation_dates=["2024-01-01", "2024-01-02"],
        horizons=[2],
    )

    result = matrices[2]
    expected = 106 / 100 - 1.0
    assert abs(float(result.loc["2024-01-01", "AAA"]) - expected) < 1e-12
