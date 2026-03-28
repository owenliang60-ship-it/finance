"""
Dual-engine BTC timing system.

Implements the v1.1 spec in docs/plans/2026-03-26-dual-engine-btc-timing-system.md
with explicit execution semantics:
- evaluate on completed 4H bars
- trade on the next bar open
- persist only the minimal cross-bar state
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import sqrt
from typing import Any, Dict, List, Optional

import pandas as pd

from src.indicators.bbwp import analyze_bbwp
from src.indicators.pmarp import analyze_pmarp
from src.indicators.rvol import analyze_rvol


@dataclass(frozen=True)
class DualEngineConfig:
    ema_period: int = 144
    pmarp_ema_period: int = 20
    pmarp_lookback: int = 150
    bbwp_period: int = 13
    bbwp_std: float = 1.0
    bbwp_lookback: int = 252
    rvol_window: int = 20
    # Helen v2.0: more aggressive mapping — full position at +0.03%
    right_bear_slope_pct: float = -0.03
    right_neutral_slope_pct: float = 0.0
    right_trend_slope_pct: float = 0.03
    risk_mode: str = "balanced"
    # Helen v2.0: natural handoff — left holds until right takes over or max hold expires
    left_max_hold_bars: int = 540  # 90 days × 6 bars/day for 4H


@dataclass
class DualEngineState:
    risk_mode: str = "balanced"
    risk_active: bool = False
    escape_price: Optional[float] = None
    k: float = 1.0
    risk_breakout_streak: int = 0
    left_latch_active: bool = False
    left_latch_position: float = 0.0
    left_trigger_price: Optional[float] = None
    left_hold_counter: int = 0


@dataclass
class DualEngineEvaluation:
    timestamp: str
    target_position_pct: float
    right_raw_position_pct: float
    right_risked_position_pct: float
    left_position_pct: float
    k: float
    reasons: List[str] = field(default_factory=list)
    state: DualEngineState = field(default_factory=DualEngineState)
    snapshot: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def compute_ema_slope_pct(prices: pd.Series, period: int = 144) -> Optional[float]:
    """Return the latest EMA slope in percentage points."""
    if len(prices) < period + 1:
        return None

    ema = prices.astype(float).ewm(span=period, adjust=False).mean()
    prev = float(ema.iloc[-2])
    curr = float(ema.iloc[-1])
    if abs(prev) < 1e-12:
        return None
    return (curr - prev) / prev * 100


def calculate_left_position_pct(x_base: float, m_bbwp: float, m_rvol: float) -> float:
    """
    Calculate the left-engine target position percentage.

    x_base == 0 remains fully dormant by design.
    """
    if x_base <= 0:
        return 0.0

    x_raw = x_base * m_bbwp * m_rvol
    x = min(x_raw, 4.0)
    x = max(x, 0.25)
    position = 40.0 * (0.6 * sqrt(x) - 0.2)
    return round(max(position, 0.0), 2)


def build_dual_engine_snapshot(
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    config: DualEngineConfig | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Build the minimal 4H + daily indicator snapshot used by the state machine."""
    config = config or DualEngineConfig()

    return {
        "4h": _build_timeframe_snapshot(df_4h, config),
        "1d": _build_timeframe_snapshot(df_daily, config),
    }


def evaluate_dual_engine_snapshot(
    snapshot: Dict[str, Dict[str, Any]],
    state: DualEngineState | None = None,
    config: DualEngineConfig | None = None,
) -> DualEngineEvaluation:
    """Evaluate a precomputed dual-engine snapshot."""
    config = config or DualEngineConfig()
    state = replace(state or DualEngineState(), risk_mode=(state.risk_mode if state else config.risk_mode))
    reasons: List[str] = []

    tf4h = snapshot["4h"]
    tf1d = snapshot["1d"]
    latest_close = tf4h.get("close")
    timestamp = tf4h.get("timestamp") or ""

    _update_risk_state(state, tf4h, tf1d, latest_close, reasons)
    left_position = _update_left_state(state, tf4h, tf1d, latest_close, reasons, config=config)
    right_raw = _calculate_right_position(tf4h, tf1d, config)
    right_risked = round(right_raw * state.k, 2)
    final_target = round(max(right_risked, left_position), 2)

    if final_target == 0:
        reasons.append("no_active_engine")

    return DualEngineEvaluation(
        timestamp=timestamp,
        target_position_pct=final_target,
        right_raw_position_pct=round(right_raw, 2),
        right_risked_position_pct=right_risked,
        left_position_pct=round(left_position, 2),
        k=round(state.k, 4),
        reasons=reasons,
        state=state,
        snapshot=snapshot,
    )


def evaluate_dual_engine(
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    state: DualEngineState | None = None,
    config: DualEngineConfig | None = None,
) -> DualEngineEvaluation:
    """
    Evaluate the dual-engine system on the latest completed 4H bar.
    """
    snapshot = build_dual_engine_snapshot(df_4h, df_daily, config)
    return evaluate_dual_engine_snapshot(snapshot, state=state, config=config)


def _build_timeframe_snapshot(
    df: pd.DataFrame,
    config: DualEngineConfig,
) -> Dict[str, Any]:
    if df is None or df.empty:
        return {
            "timestamp": "",
            "close": None,
            "ema_slope_pct": None,
            "pmarp": {"current": None, "previous": None},
            "bbwp": {"current": None, "previous": None},
            "rvol": {"current": None},
        }

    ordered = df.sort_values("date").reset_index(drop=True)
    pmarp = analyze_pmarp(
        ordered,
        ema_period=config.pmarp_ema_period,
        lookback=config.pmarp_lookback,
    )
    bbwp = analyze_bbwp(
        ordered,
        bb_period=config.bbwp_period,
        bb_std=config.bbwp_std,
        lookback=config.bbwp_lookback,
    )
    rvol = analyze_rvol(ordered, lookback=config.rvol_window)

    return {
        "timestamp": str(ordered.iloc[-1]["date"]),
        "close": float(ordered.iloc[-1]["close"]),
        "ema_slope_pct": compute_ema_slope_pct(ordered["close"], period=config.ema_period),
        "pmarp": pmarp,
        "bbwp": bbwp,
        "rvol": rvol,
    }


def _calculate_right_position(
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    config: DualEngineConfig,
) -> float:
    slope_1d = tf1d.get("ema_slope_pct")
    slope_4h = tf4h.get("ema_slope_pct")

    if slope_1d is None or slope_4h is None:
        return 0.0

    strategic = _strategic_multiplier(
        slope_1d,
        config.right_bear_slope_pct,
        config.right_neutral_slope_pct,
        config.right_trend_slope_pct,
    )
    tactical = _tactical_multiplier(
        slope_4h,
        config.right_bear_slope_pct,
        config.right_neutral_slope_pct,
    )
    return strategic * tactical * 100.0


def _strategic_multiplier(
    slope_pct: float,
    bear_floor: float,
    neutral_ceiling: float,
    trend_ceiling: float,
) -> float:
    if slope_pct < bear_floor:
        return 0.0
    if slope_pct <= neutral_ceiling:
        return _linear_interpolate(slope_pct, bear_floor, neutral_ceiling, 0.0, 0.5)
    if slope_pct <= trend_ceiling:
        return _linear_interpolate(slope_pct, neutral_ceiling, trend_ceiling, 0.5, 1.0)
    return 1.0


def _tactical_multiplier(
    slope_pct: float,
    bear_floor: float,
    neutral_ceiling: float,
) -> float:
    if slope_pct < bear_floor:
        return 0.0
    if slope_pct <= neutral_ceiling:
        return _linear_interpolate(slope_pct, bear_floor, neutral_ceiling, 0.0, 1.0)
    return 1.0


def _linear_interpolate(x: float, x1: float, x2: float, y1: float, y2: float) -> float:
    if abs(x2 - x1) < 1e-12:
        return y2
    ratio = (x - x1) / (x2 - x1)
    return y1 + ratio * (y2 - y1)


def _update_risk_state(
    state: DualEngineState,
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    latest_close: Optional[float],
    reasons: List[str],
) -> None:
    # Helen v2.0: risk module removed — trend exit (EMA slope < 0) provides
    # all crash protection. K is always 1.0.
    state.k = 1.0
    state.risk_active = False


def _update_left_state(
    state: DualEngineState,
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    latest_close: Optional[float],
    reasons: List[str],
    config: Optional["DualEngineConfig"] = None,
) -> float:
    """Helen v2.0: natural handoff — left holds until right takes over or max hold expires."""
    if latest_close is None:
        return 0.0

    max_hold = config.left_max_hold_bars if config else 540

    pmarp_4h = tf4h["pmarp"]
    pmarp_1d = tf1d["pmarp"]

    if state.left_latch_active:
        state.left_hold_counter += 1
        # Exit 1: hard stop -20%
        if state.left_trigger_price is not None and latest_close <= state.left_trigger_price * 0.8:
            reasons.append("left_hard_stop")
            _clear_left_latch(state)
            return 0.0
        # Exit 2: max hold period expired
        if max_hold > 0 and state.left_hold_counter >= max_hold:
            reasons.append("left_max_hold_exit")
            _clear_left_latch(state)
            return 0.0
        # No explicit handoff — left stays latched.
        # MAX(right, left) naturally transitions when right > left.
        return state.left_latch_position

    x_base = 0.0
    if _low_zone_turn_up(pmarp_4h, 2):
        x_base = 1.0
        if pmarp_1d.get("current") is not None and pmarp_1d["current"] < 5:
            x_base += 1.0
    if x_base == 0:
        return 0.0

    m_bbwp = _bbwp_multiplier(tf4h["bbwp"], tf1d["bbwp"], pmarp_4h.get("current"))
    m_rvol = _rvol_multiplier(tf4h["rvol"], tf1d["rvol"])
    left_position = calculate_left_position_pct(x_base, m_bbwp, m_rvol)

    if left_position > 0:
        state.left_latch_active = True
        state.left_latch_position = left_position
        state.left_trigger_price = latest_close
        state.left_hold_counter = 0
        reasons.append(f"left_latch:{left_position:.2f}")

    return left_position


def _clear_left_latch(state: DualEngineState) -> None:
    state.left_latch_active = False
    state.left_latch_position = 0.0
    state.left_trigger_price = None
    state.left_hold_counter = 0


def _bbwp_multiplier(
    bbwp_4h: Dict[str, Any],
    bbwp_1d: Dict[str, Any],
    pmarp_4h_current: Optional[float],
) -> float:
    if _high_zone_turn_down(bbwp_1d, 98) and _high_zone_turn_down(bbwp_4h, 98):
        return 2.5
    if _high_zone_turn_down(bbwp_4h, 98) and pmarp_4h_current is not None and pmarp_4h_current < 10:
        return 2.0
    if bbwp_4h.get("current") is not None and bbwp_4h["current"] < 5:
        return 1.5
    return 1.0


def _rvol_multiplier(rvol_4h: Dict[str, Any], rvol_1d: Dict[str, Any]) -> float:
    current_4h = rvol_4h.get("current")
    current_1d = rvol_1d.get("current")
    if current_1d is not None and current_1d > 2.0 and current_4h is not None and current_4h > 2.5:
        return 2.0
    if current_4h is not None and current_4h > 2.5:
        return 1.5
    if current_1d is not None and current_1d < 0.8:
        return 0.5
    return 1.0


def _high_zone_turn_down(indicator: Dict[str, Any], threshold: float) -> bool:
    current = indicator.get("current")
    previous = indicator.get("previous")
    if current is None or previous is None:
        return False
    # v1.1 keeps the spec's strict thresholds: >98 / >90 are exclusive.
    return current > threshold and current < previous


def _low_zone_turn_up(indicator: Dict[str, Any], threshold: float) -> bool:
    current = indicator.get("current")
    previous = indicator.get("previous")
    if current is None or previous is None:
        return False
    # v1.1 keeps the spec's strict thresholds: <2 is exclusive.
    return current < threshold and current > previous


# --- Forge generic contract ---
# StrategyConfig alias + run_backtest wrapper. Helen internal logic is untouched above.

StrategyConfig = DualEngineConfig


def run_backtest(
    symbol: str,
    price_4h_df: pd.DataFrame,
    price_daily_df: pd.DataFrame,
    config: DualEngineConfig | None = None,
    transaction_cost_bps: float = 10.0,
    rebalance_dead_zone_pct: float = 5.0,
    start_timestamp: str | None = None,
):
    """Forge-compatible wrapper: delegates to run_dual_engine_backtest."""
    from backtest.timing.dual_engine_backtest import run_dual_engine_backtest

    config = config or DualEngineConfig()
    result = run_dual_engine_backtest(
        symbol=symbol,
        price_4h_df=price_4h_df,
        price_daily_df=price_daily_df,
        state=DualEngineState(risk_mode=config.risk_mode),
        config=config,
        transaction_cost_bps=transaction_cost_bps,
        rebalance_dead_zone_pct=rebalance_dead_zone_pct,
        start_timestamp=start_timestamp,
        evaluate_snapshot_fn=evaluate_dual_engine_snapshot,
    )
    return result.backtest
