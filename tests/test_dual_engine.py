"""Tests for the dual-engine BTC timing system."""

from pathlib import Path

import pandas as pd

from backtest.timing.dual_engine_backtest import run_dual_engine_backtest
from backtest.timing.continuous_engine import run_continuous_backtest
from src.timing.dual_engine import (
    DualEngineConfig,
    DualEngineEvaluation,
    DualEngineState,
    calculate_left_position_pct,
    evaluate_dual_engine,
)
from src.timing.state_store import DualEngineStateStore


def _make_daily_df(days: int = 320) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=days, freq="D")
    prices = []
    value = 100.0
    for i in range(days):
        if i < 280:
            value *= 0.995
        else:
            value *= 1.02
        prices.append(value)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1_000_000 + i * 1000 for i in range(days)],
        }
    )


def _make_4h_df(bars: int = 420) -> pd.DataFrame:
    dates = pd.date_range("2023-11-01", periods=bars, freq="4h")
    prices = []
    volumes = []
    value = 100.0
    for i in range(bars):
        if i < bars - 30:
            value *= 0.998
            volumes.append(100_000)
        elif i < bars - 5:
            value *= 0.97
            volumes.append(120_000)
        else:
            value *= 1.03
            volumes.append(600_000 + i * 1_000)
        prices.append(value)

    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d %H:%M:%S"),
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": volumes,
        }
    )


def test_left_position_zero_when_dormant():
    assert calculate_left_position_pct(0.0, 1.0, 1.0) == 0.0


def test_left_position_respects_low_volume_penalty():
    assert calculate_left_position_pct(1.0, 1.0, 0.5) == 8.97


def test_right_risk_does_not_zero_left_latch():
    daily = _make_daily_df()
    intraday = _make_4h_df()
    state = DualEngineState(
        risk_mode="balanced",
        risk_active=True,
        k=0.0,
        left_latch_active=True,
        left_latch_position=18.0,
        left_trigger_price=float(intraday.iloc[-1]["close"]) * 0.95,
    )

    result = evaluate_dual_engine(intraday, daily, state=state)

    assert result.left_position_pct == 18.0
    assert result.target_position_pct >= 18.0


def test_state_store_round_trip(tmp_path):
    store = DualEngineStateStore(tmp_path / "btc_timing.db")
    state = DualEngineState(
        risk_mode="fortress",
        risk_active=True,
        escape_price=123.0,
        k=0.0,
        risk_breakout_streak=2,
        left_latch_active=True,
        left_latch_position=16.5,
        left_trigger_price=110.0,
    )

    store.save(state)
    loaded = store.load()

    assert loaded.risk_mode == "fortress"
    assert loaded.escape_price == 123.0
    assert loaded.left_latch_position == 16.5

    evaluation = DualEngineEvaluation(
        timestamp="2026-03-26 00:00:00",
        target_position_pct=12.5,
        right_raw_position_pct=20.0,
        right_risked_position_pct=8.0,
        left_position_pct=12.5,
        k=0.4,
        reasons=["left_latch:12.50"],
        state=loaded,
    )
    store.save_evaluation(evaluation)

    import sqlite3

    with sqlite3.connect(tmp_path / "btc_timing.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        history_count = conn.execute("SELECT COUNT(*) FROM engine_history").fetchone()[0]

    assert "engine_state" in tables
    assert "engine_history" in tables
    assert history_count == 1


def test_continuous_backtest_runs_with_partial_positions():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="4h").strftime("%Y-%m-%d %H:%M:%S"),
            "open": [100, 102, 104, 103, 106, 108],
            "close": [102, 104, 103, 106, 108, 110],
        }
    )
    targets = [0.0, 0.5, 1.0, 0.25, 0.75, 0.5]

    result = run_continuous_backtest("BTCUSDT", "test", df, targets, transaction_cost_bps=10.0)

    assert result.strategy_nav
    assert result.buyhold_nav
    assert 0 <= result.mean_exposure <= 1
    assert result.n_rebalances > 0


def test_continuous_backtest_applies_dead_zone():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="4h").strftime("%Y-%m-%d %H:%M:%S"),
            "open": [100, 100, 100, 100, 100, 100],
            "close": [101, 101, 101, 101, 101, 101],
        }
    )
    targets = [0.498, 0.502, 0.49, 0.60, 0.61, 0.62]

    result = run_continuous_backtest("BTCUSDT", "dead_zone", df, targets)

    assert result.n_rebalances == 2


def test_dual_engine_backtest_supports_start_timestamp():
    daily = _make_daily_df()
    intraday = _make_4h_df()
    start_timestamp = intraday["date"].iloc[-100]

    result = run_dual_engine_backtest(
        symbol="BTCUSDT",
        price_4h_df=intraday,
        price_daily_df=daily,
        state=DualEngineState(risk_mode="balanced"),
        config=DualEngineConfig(risk_mode="balanced"),
        start_timestamp=start_timestamp,
    )

    assert result.evaluations
    assert result.evaluations[0].timestamp >= start_timestamp
    assert result.backtest is not None
    assert result.backtest.strategy_nav[0][0] >= start_timestamp
