from types import SimpleNamespace

import pandas as pd

from backtest.timing.continuous_engine import (
    run_continuous_backtest,
    trim_continuous_result_window,
    window_slice,
)
from backtest.timing.dual_engine_backtest import run_dual_engine_backtest
from src.timing.dual_engine import DualEngineConfig, DualEngineEvaluation, DualEngineState


def _make_daily_df(days: int = 120) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=days, freq="D")
    prices = [100 + idx for idx in range(days)]
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": prices,
            "high": [price * 1.01 for price in prices],
            "low": [price * 0.99 for price in prices],
            "close": prices,
            "volume": [1_000_000] * days,
        }
    )


def _make_4h_df(bars: int = 60) -> pd.DataFrame:
    dates = pd.date_range("2023-03-01", periods=bars, freq="4h")
    prices = [100 + idx * 0.5 for idx in range(bars)]
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d %H:%M:%S"),
            "open": prices,
            "high": [price * 1.01 for price in prices],
            "low": [price * 0.99 for price in prices],
            "close": prices,
            "volume": [500_000] * bars,
        }
    )


def test_trim_continuous_result_window_recomputes_window_stats():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="4h").strftime("%Y-%m-%d %H:%M:%S"),
            "open": [100, 100, 100, 100, 100, 100],
            "close": [100, 101, 102, 103, 104, 105],
        }
    )
    targets = [0.0, 0.0, 1.0, 1.0, 0.5, 0.5]

    full_result = run_continuous_backtest("BTCUSDT", "window", df, targets)
    trimmed = trim_continuous_result_window(
        full_result,
        days_per_year=365 * 6,
        start_timestamp=df["date"].iloc[3],
        end_timestamp=df["date"].iloc[5],
    )

    assert trimmed.strategy_nav[0][0] == df["date"].iloc[3]
    assert trimmed.strategy_nav[-1][0] == df["date"].iloc[5]
    assert trimmed.n_rebalances == 1
    assert trimmed.mean_exposure == 0.75
    assert trimmed.strategy_metrics.n_trades == 1


def test_window_slice_includes_anchor_bar():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=10, freq="4h").strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "close": list(range(100, 110)),
        }
    )
    targets = [float(i) / 10 for i in range(10)]
    # Slice starting at bar 5 (2024-01-01 20:00:00)
    sliced_df, sliced_targets = window_slice(df, targets, df["date"].iloc[5])
    # Anchor is bar 4, so we get bars 4..9 = 6 bars
    assert len(sliced_df) == 6
    assert len(sliced_targets) == 6
    assert sliced_df["close"].iloc[0] == 104  # anchor bar
    assert sliced_targets[0] == 0.4  # anchor target


def test_window_slice_start_at_first_bar():
    df = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "close": [100, 101, 102],
        }
    )
    targets = [0.0, 0.5, 1.0]
    sliced_df, sliced_targets = window_slice(df, targets, "2024-01-01")
    # anchor_idx = max(0-1, 0) = 0, so all bars included
    assert len(sliced_df) == 3
    assert len(sliced_targets) == 3


def test_window_slice_raises_on_no_bars():
    df = pd.DataFrame(
        {"date": ["2024-01-01", "2024-01-02"], "close": [100, 101]}
    )
    targets = [0.0, 1.0]
    try:
        window_slice(df, targets, "2025-01-01")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_run_dual_engine_backtest_accepts_injected_evaluator():
    daily = _make_daily_df()
    intraday = _make_4h_df()
    calls = {"count": 0}

    def fake_evaluator(snapshot, state=None, config=None):
        del config
        calls["count"] += 1
        state = state or DualEngineState(risk_mode="balanced")
        return DualEngineEvaluation(
            timestamp=snapshot["4h"]["timestamp"],
            target_position_pct=50.0,
            right_raw_position_pct=50.0,
            right_risked_position_pct=50.0,
            left_position_pct=0.0,
            k=1.0,
            reasons=["fake"],
            state=state,
            snapshot=snapshot,
        )

    result = run_dual_engine_backtest(
        symbol="BTCUSDT",
        price_4h_df=intraday,
        price_daily_df=daily,
        state=DualEngineState(risk_mode="balanced"),
        config=DualEngineConfig(risk_mode="balanced"),
        evaluate_snapshot_fn=fake_evaluator,
    )

    assert calls["count"] == len(intraday)
    assert result.backtest is not None
    assert result.backtest.mean_exposure == 0.5
