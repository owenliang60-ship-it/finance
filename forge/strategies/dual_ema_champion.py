"""Dual EMA crossover with trend regime filter.

Entry requires golden cross AND slow EMA trending upward (positive slope
over 60 bars). Exit on death cross regardless of regime. Blocks entries
during bear market relief rallies where the slow EMA is still declining.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.timing.continuous_engine import (
    ContinuousTimingResult,
    run_continuous_backtest,
    window_slice,
)


@dataclass(frozen=True)
class StrategyConfig:
    fast_period: int = 33
    slow_period: int = 140


def run_backtest(
    symbol: str,
    price_4h_df: pd.DataFrame,
    price_daily_df: pd.DataFrame,
    config: StrategyConfig | None = None,
    transaction_cost_bps: float = 10.0,
    rebalance_dead_zone_pct: float = 5.0,
    start_timestamp: str | None = None,
) -> ContinuousTimingResult:
    """Run dual-EMA crossover backtest with trend regime filter."""
    config = config or StrategyConfig()
    df = price_4h_df.sort_values("date").reset_index(drop=True).copy()
    close = df["close"].astype(float)

    fast_ema = close.ewm(span=config.fast_period, adjust=False).mean()
    slow_ema = close.ewm(span=config.slow_period, adjust=False).mean()

    # Regime: slow EMA must be rising (positive slope over 60 bars = 10 days)
    slow_ema_slope = slow_ema.diff(60)

    in_position = False
    targets = []
    for i in range(len(close)):
        f = fast_ema.iloc[i]
        s = slow_ema.iloc[i]

        if pd.isna(f) or pd.isna(s):
            targets.append(0.0)
            in_position = False
            continue

        if in_position:
            if f <= s:  # death cross → exit regardless of regime
                in_position = False
                targets.append(0.0)
            else:
                targets.append(1.0)
        else:
            # Regime: slow EMA rising (default to bullish if not enough data)
            slope = slow_ema_slope.iloc[i]
            regime_ok = True if pd.isna(slope) else slope > 0
            if f > s and regime_ok:
                in_position = True
                targets.append(1.0)
            else:
                targets.append(0.0)

    execution_df = df
    execution_targets = targets
    if start_timestamp:
        execution_df, execution_targets = window_slice(df, targets, start_timestamp)

    return run_continuous_backtest(
        symbol=symbol,
        signal_name="dual_ema",
        price_df=execution_df,
        target_positions=execution_targets,
        transaction_cost_bps=transaction_cost_bps,
        rebalance_dead_zone_pct=rebalance_dead_zone_pct,
        days_per_year=365 * 6,
    )
