"""Verification orchestrator (Task 10).

Produces three artifacts in a single deterministic pass:

1. ``param_summary`` (12 rows) — primary cell SPY 10d, master verdict table
2. ``param_summary_qqq10d`` (12 rows) — sensitivity cell QQQ 10d
3. ``verification_table`` (240 rows) — per-(ma, K, target, horizon) diagnostic

Each row of param_summary carries the 6 hurdle booleans + ``passes_count_param``.
``long_horizon_diff`` is informational and NOT counted in passes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from backtest.breadth_study.percentile_events import detect_upcross_events
from backtest.breadth_study.percentile_hurdles import (
    check_h1_trigger_frequency,
    check_h2_hit_rate_lift,
    check_h3_target_consistency,
    check_h4_short_horizon_consistency,
    compute_effective_sample_years,
)
from backtest.breadth_study.percentile_perm import (
    check_h5_permutation,
    year_stratified_permutation_p,
)
from backtest.breadth_study.percentile_signal import build_percentile_signal
from backtest.breadth_study.percentile_strategy import (
    check_h6_strategy_excess_cagr,
    event_strategy_bootstrap_ci,
    event_strategy_cagr,
)


def _enumerate_params(manifest: Dict[str, Any]) -> List[Tuple[int, float, str]]:
    """Yield (ma_window, threshold, event_type) tuples in deterministic order.

    Order: (MA20 low_recovery x3) -> (MA20 high_strength x3) -> MA50 same.
    """
    out: List[Tuple[int, float, str]] = []
    for ma in manifest["ma_windows"]:
        for K in manifest["thresholds"]["low_recovery"]:
            out.append((int(ma), float(K), "low_recovery"))
        for K in manifest["thresholds"]["high_strength"]:
            out.append((int(ma), float(K), "high_strength"))
    return out


def _slice_returns_in_window(
    target_returns: pd.DataFrame,
    first_valid_date: pd.Timestamp,
    last_date: pd.Timestamp,
) -> pd.DataFrame:
    return target_returns[
        (target_returns["date"] >= pd.Timestamp(first_valid_date))
        & (target_returns["date"] <= pd.Timestamp(last_date))
    ].reset_index(drop=True)


def _event_and_non_event_returns(
    panel: pd.DataFrame,
    return_col: str,
    event_dates_set: set,
) -> Tuple[np.ndarray, np.ndarray]:
    is_event = panel["date"].isin(event_dates_set)
    rets = panel[return_col]
    event_rets = rets[is_event].dropna().to_numpy()
    non_event_rets = rets[~is_event].dropna().to_numpy()
    return event_rets, non_event_rets


def _row_metrics(
    panel: pd.DataFrame,
    target: str,
    horizon: int,
    event_dates: List[pd.Timestamp],
) -> Dict[str, Any]:
    return_col = f"{target}_fwd_{horizon}d"
    if return_col not in panel.columns:
        return {
            "event_n": 0,
            "event_mean": float("nan"),
            "non_event_mean": float("nan"),
            "mean_diff": float("nan"),
            "hit_rate": float("nan"),
            "non_event_hit_rate": float("nan"),
        }
    event_set = {pd.Timestamp(d) for d in event_dates}
    event_rets, non_event_rets = _event_and_non_event_returns(panel, return_col, event_set)
    n_event = int(event_rets.size)
    if n_event == 0 or non_event_rets.size == 0:
        return {
            "event_n": n_event,
            "event_mean": float("nan"),
            "non_event_mean": float("nan"),
            "mean_diff": float("nan"),
            "hit_rate": float("nan"),
            "non_event_hit_rate": float("nan"),
        }
    return {
        "event_n": n_event,
        "event_mean": float(event_rets.mean()),
        "non_event_mean": float(non_event_rets.mean()),
        "mean_diff": float(event_rets.mean() - non_event_rets.mean()),
        "hit_rate": float((event_rets > 0).mean()),
        "non_event_hit_rate": float((non_event_rets > 0).mean()),
    }


def _expected_sign_for(event_type: str) -> int:
    # Both low-recovery and high-strength upcrosses expect positive forward
    # return per hypothesis (low: oversold rebound; high: strength continuation).
    if event_type in ("low_recovery", "high_strength"):
        return 1
    raise ValueError(f"Unknown event_type: {event_type}")


def _build_param_summary(
    manifest: Dict[str, Any],
    daily_breadth: pd.DataFrame,
    target_prices_dict: Dict[str, pd.DataFrame],
    target_returns: pd.DataFrame,
    primary_target: str,
    primary_horizon: int,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Returns (param_summary_df, table_rows_for_diagnostic).

    `table_rows_for_diagnostic` is appended-to but only the primary call
    persists them — the sensitivity call discards them via the orchestrator.
    """
    param_rows: List[Dict[str, Any]] = []
    table_rows: List[Dict[str, Any]] = []

    perm_cfg = manifest["permutation"]
    boot_cfg = manifest["strategy_bootstrap"]
    hurdle_cfg = manifest["hurdle_thresholds"]

    for ma, K, event_type in _enumerate_params(manifest):
        breadth_col = f"breadth_{ma}"
        if breadth_col not in daily_breadth.columns:
            raise KeyError(
                f"daily_breadth missing column {breadth_col!r}; "
                f"have={list(daily_breadth.columns)}"
            )
        signal = build_percentile_signal(
            breadth=daily_breadth[breadth_col],
            lookback=manifest["percentile_lookback"],
            smoother_window=5,
        )
        first_valid, last, effective_years = compute_effective_sample_years(
            signal, daily_breadth["date"]
        )

        events_short = detect_upcross_events(
            signal, threshold=K, cooldown_days=manifest["cooldown_short_horizon"]
        )
        events_long = detect_upcross_events(
            signal, threshold=K, cooldown_days=manifest["cooldown_long_horizon"]
        )

        date_series = daily_breadth["date"]
        events_short_dates = [pd.Timestamp(date_series.iloc[ev["index"]]) for ev in events_short]
        events_long_dates = [pd.Timestamp(date_series.iloc[ev["index"]]) for ev in events_long]

        # Effective-window panel for forward returns
        panel = _slice_returns_in_window(target_returns, first_valid, last)

        # Per-cell diagnostic rows (240 rows total when called for primary)
        for tgt in manifest["targets"]:
            for h in manifest["horizons_short"]:
                metrics = _row_metrics(panel, tgt, h, events_short_dates)
                table_rows.append({
                    "ma_window": ma, "threshold": K, "event_type": event_type,
                    "target": tgt, "horizon": h, **metrics,
                })
            for h in manifest["horizons_long"]:
                metrics = _row_metrics(panel, tgt, h, events_long_dates)
                table_rows.append({
                    "ma_window": ma, "threshold": K, "event_type": event_type,
                    "target": tgt, "horizon": h, **metrics,
                })

        primary_events = (
            events_short_dates if primary_horizon in manifest["horizons_short"]
            else events_long_dates
        )
        primary_cooldown = (
            manifest["cooldown_short_horizon"] if primary_horizon in manifest["horizons_short"]
            else manifest["cooldown_long_horizon"]
        )

        # H1 trigger frequency
        h1 = check_h1_trigger_frequency(
            len(primary_events), effective_years,
            hurdle_cfg["h1_trigger_freq_min_per_year"],
            hurdle_cfg["h1_trigger_freq_max_per_year"],
        )

        # H2 hit-rate lift on primary cell
        primary_col = f"{primary_target}_fwd_{primary_horizon}d"
        h2 = False
        event_hit = float("nan")
        baseline_hit = float("nan")
        hit_lift_pp = float("nan")
        if primary_col in panel.columns:
            event_set = set(primary_events)
            event_rets, non_event_rets = _event_and_non_event_returns(
                panel, primary_col, event_set
            )
            if event_rets.size > 0 and non_event_rets.size > 0:
                event_hit = float((event_rets > 0).mean()) * 100.0
                baseline_hit = float((non_event_rets > 0).mean()) * 100.0
                hit_lift_pp = event_hit - baseline_hit
                h2 = check_h2_hit_rate_lift(
                    event_rets, non_event_rets,
                    hurdle_cfg["h2_hit_rate_lift_pp"],
                )

        # H3 cross-target same sign at primary horizon
        target_diffs_at_primary: Dict[str, float] = {}
        for tgt in manifest["targets"]:
            metrics = _row_metrics(panel, tgt, primary_horizon, primary_events)
            target_diffs_at_primary[tgt] = metrics["mean_diff"]
        expected_sign = _expected_sign_for(event_type)
        h3 = check_h3_target_consistency(
            target_diffs_at_primary, expected_sign,
            hurdle_cfg["h3_target_same_sign_min"],
        )
        same_sign_targets = [
            tgt for tgt, v in target_diffs_at_primary.items()
            if isinstance(v, (int, float)) and not np.isnan(v)
            and ((v > 0 and expected_sign > 0) or (v < 0 and expected_sign < 0))
        ]

        # H4 short-horizon consistency on primary target
        short_horizon_diffs: Dict[int, float] = {}
        for h_short in hurdle_cfg["h4_short_horizons"]:
            metrics = _row_metrics(panel, primary_target, h_short, events_short_dates)
            short_horizon_diffs[int(h_short)] = metrics["mean_diff"]
        h4 = check_h4_short_horizon_consistency(
            short_horizon_diffs, expected_sign,
            hurdle_cfg["h4_short_horizon_same_sign_min"],
        )
        short_same_horizons = [
            int(h) for h, v in short_horizon_diffs.items()
            if isinstance(v, (int, float)) and not np.isnan(v)
            and ((v > 0 and expected_sign > 0) or (v < 0 and expected_sign < 0))
        ]

        # 60d informational
        long_metrics = _row_metrics(panel, primary_target, 60, events_long_dates)
        long_horizon_diff = long_metrics["mean_diff"]

        # H5 permutation p-value on primary cell
        h5 = False
        h5_p = float("nan")
        h5_method = "n/a"
        h5_success = float("nan")
        if primary_col in panel.columns and primary_events:
            h5_p, _, h5_diag = year_stratified_permutation_p(
                primary_events,
                panel,
                target=primary_target,
                horizon=primary_horizon,
                cooldown_days=primary_cooldown,
                trials=perm_cfg["trials"],
                seed=perm_cfg["seed"],
                rejection_max_attempts_per_event=perm_cfg["rejection_max_attempts_per_event"],
                fallback_to_sequential_below=perm_cfg["fallback_to_sequential_below"],
                warning_threshold=perm_cfg["warning_threshold"],
            )
            h5_method = h5_diag["sampling_method_used"]
            h5_success = float(h5_diag["success_rate"])
            h5 = check_h5_permutation(h5_p, hurdle_cfg["h5_permutation_p_max"])

        # H6 strategy + bootstrap CI on primary cell
        h6 = False
        strat_metrics = {
            "strategy_cagr": float("nan"),
            "bnh_cagr": float("nan"),
            "excess_cagr": float("nan"),
            "n_trades": 0,
            "exposure_pct": float("nan"),
        }
        ci = {
            "excess_cagr_point": float("nan"),
            "excess_cagr_ci_low": float("nan"),
            "excess_cagr_ci_high": float("nan"),
            "excess_cagr_share_negative": float("nan"),
        }
        if primary_target in target_prices_dict:
            tgt_prices = target_prices_dict[primary_target]
            strat_metrics = event_strategy_cagr(
                primary_events, tgt_prices, primary_target, primary_horizon,
                first_valid, last,
                one_way_bps=manifest["strategy_costs"]["one_way_bps"],
            )
            h6 = check_h6_strategy_excess_cagr(
                strat_metrics["excess_cagr"],
                hurdle_cfg["h6_strategy_excess_cagr_pp"],
            )
            if primary_events:
                ci_full = event_strategy_bootstrap_ci(
                    primary_events, tgt_prices, primary_target, primary_horizon,
                    first_valid, last,
                    one_way_bps=manifest["strategy_costs"]["one_way_bps"],
                    trials=boot_cfg["trials"],
                    seed=boot_cfg["seed"],
                    ci_lower_pct=boot_cfg["ci_lower_pct"],
                    ci_upper_pct=boot_cfg["ci_upper_pct"],
                )
                ci.update({
                    "excess_cagr_point": ci_full["excess_cagr_point"],
                    "excess_cagr_ci_low": ci_full["excess_cagr_ci_low"],
                    "excess_cagr_ci_high": ci_full["excess_cagr_ci_high"],
                    "excess_cagr_share_negative": ci_full["excess_cagr_share_negative"],
                })

        passes_count = int(sum([h1, h2, h3, h4, h5, h6]))

        param_rows.append({
            "ma_window": ma,
            "threshold": K,
            "event_type": event_type,
            "primary_cell": f"{primary_target}_{primary_horizon}d",
            "event_n_short": len(events_short_dates),
            "event_n_long": len(events_long_dates),
            "first_valid_date": pd.Timestamp(first_valid).strftime("%Y-%m-%d"),
            "last_date": pd.Timestamp(last).strftime("%Y-%m-%d"),
            "effective_years": round(float(effective_years), 4),
            "events_per_year": (
                round(len(primary_events) / effective_years, 4)
                if effective_years > 0 else float("nan")
            ),
            "event_hit": event_hit,
            "baseline_hit": baseline_hit,
            "hit_lift_pp": hit_lift_pp,
            "perm_p": h5_p,
            "perm_sampling_method": h5_method,
            "perm_success_rate": h5_success,
            "strategy_cagr_pp": strat_metrics["strategy_cagr"],
            "bnh_cagr_pp": strat_metrics["bnh_cagr"],
            "excess_cagr_pp": strat_metrics["excess_cagr"],
            "excess_cagr_ci_low": ci["excess_cagr_ci_low"],
            "excess_cagr_ci_high": ci["excess_cagr_ci_high"],
            "excess_cagr_share_negative": ci["excess_cagr_share_negative"],
            "n_trades": strat_metrics["n_trades"],
            "exposure_pct": strat_metrics["exposure_pct"],
            "target_same_sign_count": len(same_sign_targets),
            "target_same_sign_targets": ",".join(same_sign_targets),
            "short_horizon_same_sign_count": len(short_same_horizons),
            "short_horizon_same_sign_horizons": ",".join(str(h) for h in short_same_horizons),
            "long_horizon_diff": long_horizon_diff,
            "h1_freq_pass": bool(h1),
            "h2_hit_pass": bool(h2),
            "h3_target_pass": bool(h3),
            "h4_short_horizon_pass": bool(h4),
            "h5_perm_pass": bool(h5),
            "h6_strategy_pass": bool(h6),
            "passes_count_param": passes_count,
        })

    return pd.DataFrame(param_rows), table_rows


def run_verification(
    manifest: Dict[str, Any],
    daily_breadth: pd.DataFrame,
    target_prices_dict: Dict[str, pd.DataFrame],
    target_returns: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (param_summary_primary, param_summary_sensitivity, verification_table).

    Tables are pure data — caller writes CSVs and report.
    """
    primary_df, table_rows_primary = _build_param_summary(
        manifest,
        daily_breadth,
        target_prices_dict,
        target_returns,
        primary_target=manifest["primary_target"],
        primary_horizon=manifest["primary_horizon"],
    )
    sensitivity_df, _ = _build_param_summary(
        manifest,
        daily_breadth,
        target_prices_dict,
        target_returns,
        primary_target=manifest["sensitivity_target"],
        primary_horizon=manifest["sensitivity_horizon"],
    )
    table_df = pd.DataFrame(table_rows_primary).reset_index(drop=True)
    return primary_df, sensitivity_df, table_df
