"""US Equity Theme Basket Crowding Indicator (V2.2).

Read-only analytical module computing 0-10 crowding scores across 3 equal-weighted dimensions
(Return, Volume, Valuation) and 5 causal EMA3-smoothed sub-indicators with hysteresis state lamps.
"""
from __future__ import annotations

from backtest.theme_crowding.dashboard import render_dashboard_html
from backtest.theme_crowding.engine import (
    DUAL_CLASS_DV_MAP,
    HYST_CROWD_IN,
    HYST_CROWD_OUT,
    HYST_WARM_IN,
    HYST_WARM_OUT,
    MA_WINDOW,
    MIN_LISTING_BARS,
    PCTILE_WINDOW,
    POST_EMA_SPAN,
    VAL_PCTILE_MAX_WINDOW,
    VAL_PCTILE_MIN_WINDOW,
    compute_crowding_payload,
    compute_dd_gate,
    ema_joint_valid,
    fx_to_usd,
    roll_pctile_bounded,
    run_hysteresis_states,
    sanitize_symbol_mcap_series,
)

__all__ = [
    "DUAL_CLASS_DV_MAP",
    "HYST_CROWD_IN",
    "HYST_CROWD_OUT",
    "HYST_WARM_IN",
    "HYST_WARM_OUT",
    "MA_WINDOW",
    "MIN_LISTING_BARS",
    "PCTILE_WINDOW",
    "POST_EMA_SPAN",
    "VAL_PCTILE_MAX_WINDOW",
    "VAL_PCTILE_MIN_WINDOW",
    "compute_crowding_payload",
    "compute_dd_gate",
    "ema_joint_valid",
    "fx_to_usd",
    "render_dashboard_html",
    "roll_pctile_bounded",
    "run_hysteresis_states",
    "sanitize_symbol_mcap_series",
]
