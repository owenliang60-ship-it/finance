"""Tests for the dual MA seed strategy."""

import pandas as pd


def test_dual_ma_run_backtest_basic():
    """Dual MA strategy runs on synthetic data and returns valid result."""
    # Import here so path setup works
    from forge.strategies.dual_ma_champion import StrategyConfig, run_backtest

    # Create synthetic 4H data with clear trend
    n_bars = 200
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="4h")
    # Uptrend: prices go from 100 to 300
    prices = [100 + i for i in range(n_bars)]
    df_4h = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d %H:%M:%S"),
            "open": prices,
            "close": prices,
        }
    )
    df_1d = pd.DataFrame({"date": ["2020-01-01"], "close": [100]})  # unused but required

    config = StrategyConfig(fast_period=10, slow_period=30)
    result = run_backtest(
        symbol="TEST",
        price_4h_df=df_4h,
        price_daily_df=df_1d,
        config=config,
    )

    assert result is not None
    assert result.symbol == "TEST"
    assert result.signal_name == "dual_ma"
    assert result.mean_exposure > 0  # should be long most of the time in uptrend
    assert len(result.strategy_nav) == n_bars


def test_dual_ma_with_start_timestamp():
    """Dual MA strategy correctly slices when start_timestamp provided."""
    from forge.strategies.dual_ma_champion import StrategyConfig, run_backtest

    n_bars = 200
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="4h")
    prices = [100 + i for i in range(n_bars)]
    df_4h = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d %H:%M:%S"),
            "open": prices,
            "close": prices,
        }
    )
    df_1d = pd.DataFrame({"date": ["2020-01-01"], "close": [100]})

    config = StrategyConfig(fast_period=10, slow_period=30)
    result = run_backtest(
        symbol="TEST",
        price_4h_df=df_4h,
        price_daily_df=df_1d,
        config=config,
        start_timestamp="2020-01-10",
    )

    assert result is not None
    # Sliced result should have fewer bars
    assert len(result.strategy_nav) < n_bars


def test_dual_ma_params_override():
    """StrategyConfig params actually change behavior."""
    from forge.strategies.dual_ma_champion import StrategyConfig, run_backtest

    n_bars = 300
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="4h")
    # Oscillating prices to make different periods produce different results
    import math
    prices = [100 + 20 * math.sin(i / 10) for i in range(n_bars)]
    df_4h = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d %H:%M:%S"),
            "open": prices,
            "close": prices,
        }
    )
    df_1d = pd.DataFrame({"date": ["2020-01-01"], "close": [100]})

    result_fast = run_backtest("TEST", df_4h, df_1d, StrategyConfig(fast_period=5, slow_period=20))
    result_slow = run_backtest("TEST", df_4h, df_1d, StrategyConfig(fast_period=50, slow_period=100))

    # Different params should produce different exposure profiles
    assert result_fast.n_rebalances != result_slow.n_rebalances or \
           abs(result_fast.mean_exposure - result_slow.mean_exposure) > 0.01
