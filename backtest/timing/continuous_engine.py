"""Continuous-position timing backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from backtest.metrics import BacktestMetrics, compute_metrics


@dataclass
class ContinuousTimingResult:
    symbol: str
    signal_name: str
    strategy_nav: List[Tuple[str, float]]
    buyhold_nav: List[Tuple[str, float]]
    strategy_metrics: BacktestMetrics
    buyhold_metrics: BacktestMetrics
    excess_cagr: float
    sharpe_diff: float
    mdd_diff: float
    n_rebalances: int
    mean_exposure: float


def run_continuous_backtest(
    symbol: str,
    signal_name: str,
    price_df,
    target_positions,
    initial_capital: float = 100.0,
    transaction_cost_bps: float = 10.0,
    rebalance_dead_zone_pct: float = 5.0,
    days_per_year: int = 365 * 6,
) -> ContinuousTimingResult:
    """
    Backtest a continuous target-position series.

    Execution semantics:
    - target at bar i is computed on bar i close
    - target becomes active on bar i+1 open
    """
    dates = price_df["date"].astype(str).tolist()
    closes = price_df["close"].astype(float).tolist()
    opens = (
        price_df["open"].astype(float).tolist()
        if "open" in price_df.columns
        else closes
    )

    if len(dates) < 2:
        empty_metrics = compute_metrics([], days_per_year=days_per_year)
        return ContinuousTimingResult(
            symbol=symbol,
            signal_name=signal_name,
            strategy_nav=[],
            buyhold_nav=[],
            strategy_metrics=empty_metrics,
            buyhold_metrics=empty_metrics,
            excess_cagr=0.0,
            sharpe_diff=0.0,
            mdd_diff=0.0,
            n_rebalances=0,
            mean_exposure=0.0,
        )

    if len(target_positions) != len(dates):
        raise ValueError("target_positions length must equal price_df length")

    nav = initial_capital
    bh_nav = initial_capital
    prev_target = 0.0
    total_turnover = 0.0
    total_costs = 0.0
    n_rebalances = 0
    exposure_sum = 0.0

    strategy_nav = [(dates[0], round(nav, 6))]
    buyhold_nav = [(dates[0], round(bh_nav, 6))]
    cost_rate = transaction_cost_bps / 10_000
    dead_zone = rebalance_dead_zone_pct / 100

    for i in range(1, len(dates)):
        requested_target = float(target_positions[i - 1])
        requested_target = max(0.0, min(requested_target, 1.0))
        target = (
            prev_target
            if abs(requested_target - prev_target) < dead_zone
            else requested_target
        )

        turnover = abs(target - prev_target)
        if turnover > 1e-12:
            trade_cost = nav * turnover * cost_rate
            nav -= trade_cost
            total_costs += trade_cost
            total_turnover += turnover
            n_rebalances += 1

        bar_open = opens[i]
        bar_close = closes[i]
        if bar_open <= 0:
            raise ValueError("bar open price must be positive")

        bar_return = bar_close / bar_open - 1
        nav *= 1 + target * bar_return
        bh_nav *= closes[i] / closes[i - 1]

        exposure_sum += target
        prev_target = target
        strategy_nav.append((dates[i], round(nav, 6)))
        buyhold_nav.append((dates[i], round(bh_nav, 6)))

    years = len(dates) / days_per_year if days_per_year > 0 else 0
    annual_turnover = total_turnover / years if years > 0 else 0.0
    mean_exposure = exposure_sum / max(len(dates) - 1, 1)

    strategy_metrics = compute_metrics(
        strategy_nav,
        benchmark_nav=buyhold_nav,
        total_costs=total_costs,
        n_trades=n_rebalances,
        annual_turnover=annual_turnover,
        days_per_year=days_per_year,
    )
    buyhold_metrics = compute_metrics(
        buyhold_nav,
        n_trades=0,
        days_per_year=days_per_year,
    )

    return ContinuousTimingResult(
        symbol=symbol,
        signal_name=signal_name,
        strategy_nav=strategy_nav,
        buyhold_nav=buyhold_nav,
        strategy_metrics=strategy_metrics,
        buyhold_metrics=buyhold_metrics,
        excess_cagr=round(strategy_metrics.cagr - buyhold_metrics.cagr, 6),
        sharpe_diff=round(strategy_metrics.sharpe_ratio - buyhold_metrics.sharpe_ratio, 4),
        mdd_diff=round(strategy_metrics.max_drawdown - buyhold_metrics.max_drawdown, 6),
        n_rebalances=n_rebalances,
        mean_exposure=round(mean_exposure, 4),
    )
