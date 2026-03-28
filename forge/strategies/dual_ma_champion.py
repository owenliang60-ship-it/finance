"""Dual moving average crossover strategy — Forge seed.

Golden cross (fast > slow) → 100% position.
Death cross (fast < slow) → 0% position.
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
    fast_period: int = 20
    slow_period: int = 50


def run_backtest(
    symbol: str,
    price_4h_df: pd.DataFrame,
    price_daily_df: pd.DataFrame,
    config: StrategyConfig | None = None,
    transaction_cost_bps: float = 10.0,
    rebalance_dead_zone_pct: float = 5.0,
    start_timestamp: str | None = None,
) -> ContinuousTimingResult:
    """Run dual-MA crossover backtest on 4H bars."""
    config = config or StrategyConfig()
    df = price_4h_df.sort_values("date").reset_index(drop=True).copy()
    close = df["close"].astype(float)

    fast_ma = close.rolling(config.fast_period).mean()
    slow_ma = close.rolling(config.slow_period).mean()

    targets = [
        1.0 if (not pd.isna(f) and not pd.isna(s) and f > s) else 0.0
        for f, s in zip(fast_ma, slow_ma)
    ]

    execution_df = df
    execution_targets = targets
    if start_timestamp:
        execution_df, execution_targets = window_slice(df, targets, start_timestamp)

    return run_continuous_backtest(
        symbol=symbol,
        signal_name="dual_ma",
        price_df=execution_df,
        target_positions=execution_targets,
        transaction_cost_bps=transaction_cost_bps,
        rebalance_dead_zone_pct=rebalance_dead_zone_pct,
        days_per_year=365 * 6,
    )
