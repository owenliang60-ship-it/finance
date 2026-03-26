"""Backtest harness for the dual-engine BTC timing system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.timing.continuous_engine import (
    ContinuousTimingResult,
    run_continuous_backtest,
)
from src.indicators.bbwp import calculate_bbwp
from src.indicators.pmarp import calculate_pmarp
from src.indicators.rvol import calculate_rvol_series
from src.timing.dual_engine import (
    DualEngineConfig,
    DualEngineEvaluation,
    DualEngineState,
    evaluate_dual_engine_snapshot,
)


@dataclass
class DualEngineBacktestResult:
    evaluations: List[DualEngineEvaluation] = field(default_factory=list)
    backtest: ContinuousTimingResult | None = None


def run_dual_engine_backtest(
    symbol: str,
    price_4h_df: pd.DataFrame,
    price_daily_df: pd.DataFrame,
    state: DualEngineState | None = None,
    config: DualEngineConfig | None = None,
    transaction_cost_bps: float = 10.0,
    rebalance_dead_zone_pct: float = 5.0,
    start_timestamp: str | None = None,
) -> DualEngineBacktestResult:
    """
    Evaluate the dual-engine system on each completed 4H bar and backtest
    the resulting continuous target-position series.
    """
    config = config or DualEngineConfig()
    working_state = state or DualEngineState(risk_mode=config.risk_mode)
    ordered_4h = _prepare_indicator_frame(price_4h_df, config)
    ordered_daily = _prepare_indicator_frame(price_daily_df, config)
    daily_close_times = pd.to_datetime(ordered_daily["date"]) + pd.Timedelta(days=1)

    evaluations: List[DualEngineEvaluation] = []
    targets: List[float] = []

    for i in range(len(ordered_4h)):
        evaluation_time = pd.to_datetime(ordered_4h.iloc[i]["date"]) + pd.Timedelta(hours=4)
        daily_count = int(daily_close_times.searchsorted(evaluation_time, side="right"))
        daily_idx = daily_count - 1
        snapshot = {
            "4h": _snapshot_from_frame_row(ordered_4h, i),
            "1d": _snapshot_from_frame_row(ordered_daily, daily_idx) if daily_idx >= 0 else _empty_snapshot(),
        }

        evaluation = evaluate_dual_engine_snapshot(snapshot, state=working_state, config=config)
        working_state = evaluation.state
        evaluations.append(evaluation)
        targets.append(evaluation.target_position_pct / 100.0)

    execution_price_df = ordered_4h
    execution_targets = targets
    visible_evaluations = evaluations

    if start_timestamp:
        execution_price_df, execution_targets, visible_evaluations = _window_backtest_inputs(
            ordered_4h,
            targets,
            evaluations,
            start_timestamp,
        )

    backtest = run_continuous_backtest(
        symbol=symbol,
        signal_name="dual_engine_btc",
        price_df=execution_price_df,
        target_positions=execution_targets,
        transaction_cost_bps=transaction_cost_bps,
        rebalance_dead_zone_pct=rebalance_dead_zone_pct,
        days_per_year=365 * 6,
    )
    if start_timestamp:
        backtest = _trim_continuous_result(backtest, start_timestamp, days_per_year=365 * 6)

    return DualEngineBacktestResult(evaluations=visible_evaluations, backtest=backtest)


def _prepare_indicator_frame(df: pd.DataFrame, config: DualEngineConfig) -> pd.DataFrame:
    ordered = df.sort_values("date").reset_index(drop=True).copy()
    close = ordered["close"].astype(float)

    ema = close.ewm(span=config.ema_period, adjust=False).mean()
    ema_prev = ema.shift(1)
    ordered["ema_slope_pct"] = ((ema - ema_prev) / ema_prev.replace(0, pd.NA)) * 100

    pmarp = calculate_pmarp(
        close,
        ema_period=config.pmarp_ema_period,
        lookback=config.pmarp_lookback,
    )
    bbwp = calculate_bbwp(
        close,
        bb_period=config.bbwp_period,
        bb_std=config.bbwp_std,
        lookback=config.bbwp_lookback,
    )
    ordered["pmarp_current"] = pmarp
    ordered["pmarp_previous"] = pmarp.shift(1)
    ordered["bbwp_current"] = bbwp
    ordered["bbwp_previous"] = bbwp.shift(1)

    if "volume" in ordered.columns:
        rvol = calculate_rvol_series(
            ordered["volume"].astype(float),
            lookback=config.rvol_window,
        )
        ordered["rvol_current"] = rvol
    else:
        ordered["rvol_current"] = pd.NA

    return ordered


def _snapshot_from_frame_row(frame: pd.DataFrame, idx: int) -> dict:
    if idx < 0 or idx >= len(frame):
        return _empty_snapshot()

    row = frame.iloc[idx]
    return {
        "timestamp": str(row["date"]),
        "close": _clean_value(row.get("close")),
        "ema_slope_pct": _clean_value(row.get("ema_slope_pct")),
        "pmarp": {
            "current": _clean_value(row.get("pmarp_current")),
            "previous": _clean_value(row.get("pmarp_previous")),
        },
        "bbwp": {
            "current": _clean_value(row.get("bbwp_current")),
            "previous": _clean_value(row.get("bbwp_previous")),
        },
        "rvol": {
            "current": _clean_value(row.get("rvol_current")),
        },
    }


def _clean_value(value):
    if pd.isna(value):
        return None
    return float(value)


def _empty_snapshot() -> dict:
    return {
        "timestamp": "",
        "close": None,
        "ema_slope_pct": None,
        "pmarp": {"current": None, "previous": None},
        "bbwp": {"current": None, "previous": None},
        "rvol": {"current": None},
    }


def _window_backtest_inputs(
    ordered_4h: pd.DataFrame,
    targets: List[float],
    evaluations: List[DualEngineEvaluation],
    start_timestamp: str,
) -> tuple[pd.DataFrame, List[float], List[DualEngineEvaluation]]:
    timestamps = pd.to_datetime(ordered_4h["date"])
    start_ts = pd.Timestamp(start_timestamp)
    visible_mask = timestamps >= start_ts
    if not bool(visible_mask.any()):
        raise ValueError(f"No 4H bars found on or after start_timestamp={start_timestamp}")

    start_idx = int(visible_mask.to_numpy().argmax())
    anchor_idx = max(start_idx - 1, 0)
    execution_price_df = ordered_4h.iloc[anchor_idx:].reset_index(drop=True)
    execution_targets = targets[anchor_idx:]
    visible_evaluations = evaluations[start_idx:]
    return execution_price_df, execution_targets, visible_evaluations


def _trim_continuous_result(
    result: ContinuousTimingResult,
    start_timestamp: str,
    days_per_year: int,
) -> ContinuousTimingResult:
    start_ts = pd.Timestamp(start_timestamp)
    strategy_nav = [(dt, nav) for dt, nav in result.strategy_nav if pd.Timestamp(dt) >= start_ts]
    buyhold_nav = [(dt, nav) for dt, nav in result.buyhold_nav if pd.Timestamp(dt) >= start_ts]
    if not strategy_nav or not buyhold_nav:
        raise ValueError(f"No NAV data found on or after start_timestamp={start_timestamp}")

    strategy_metrics = compute_metrics(
        strategy_nav,
        benchmark_nav=buyhold_nav,
        total_costs=result.strategy_metrics.total_costs,
        n_trades=result.n_rebalances,
        annual_turnover=result.strategy_metrics.annual_turnover,
        days_per_year=days_per_year,
    )
    buyhold_metrics = compute_metrics(
        buyhold_nav,
        n_trades=0,
        days_per_year=days_per_year,
    )
    return ContinuousTimingResult(
        symbol=result.symbol,
        signal_name=result.signal_name,
        strategy_nav=strategy_nav,
        buyhold_nav=buyhold_nav,
        strategy_metrics=strategy_metrics,
        buyhold_metrics=buyhold_metrics,
        excess_cagr=round(strategy_metrics.cagr - buyhold_metrics.cagr, 6),
        sharpe_diff=round(strategy_metrics.sharpe_ratio - buyhold_metrics.sharpe_ratio, 4),
        mdd_diff=round(strategy_metrics.max_drawdown - buyhold_metrics.max_drawdown, 6),
        n_rebalances=result.n_rebalances,
        mean_exposure=result.mean_exposure,
    )
