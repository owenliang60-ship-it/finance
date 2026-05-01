"""Exploratory recovery parameter sweep for broad breadth signals."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from backtest.breadth_study.core import (
    ALL_HORIZONS,
    DEFAULT_MARKET_DB,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OVERLAY_JSON,
    PRIMARY_TARGETS,
    TARGET_SYMBOLS,
    _aggregate_breadth,
    _apply_bh_fdr,
    _format_mcap,
    build_eligible_price_frame,
    build_forward_returns,
    build_sidecar,
    compare_event_returns,
    iter_samples,
    load_overlay_symbols,
    read_daily_prices,
    read_historical_market_caps,
    read_sidecar,
)


logger = logging.getLogger(__name__)

DEFAULT_SWEEP_WINDOWS = (10, 20, 30, 40, 50, 60, 80, 100)
DEFAULT_TRIGGER_THRESHOLDS = tuple(x / 100 for x in range(20, 61, 5))
DEFAULT_LOW_THRESHOLDS = (0.15, 0.20, 0.25, 0.30, 0.35)
DEFAULT_SWEEP_REPORT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "research"
    / "2026-04-29-broad-breadth-recovery-sweep.md"
)


@dataclass(frozen=True)
class RecoverySweepConfig:
    market_db: Path = DEFAULT_MARKET_DB
    output_dir: Path = DEFAULT_OUTPUT_DIR
    overlay_json: Path = DEFAULT_OVERLAY_JSON
    report_path: Path = DEFAULT_SWEEP_REPORT
    from_date: str = "2021-02-01"
    to_date: Optional[str] = None
    min_market_cap: float = 10_000_000_000.0
    max_staleness_days: int = 90
    oos_start: str = "2025-01-01"
    cooldown_days: int = 20
    ma_windows: Sequence[int] = DEFAULT_SWEEP_WINDOWS
    trigger_thresholds: Sequence[float] = DEFAULT_TRIGGER_THRESHOLDS
    low_thresholds: Sequence[float] = DEFAULT_LOW_THRESHOLDS
    bootstrap_samples: int = 500
    random_seed: int = 20260429
    refresh_sidecar: bool = False


def run_recovery_sweep(config: RecoverySweepConfig) -> Dict[str, Path]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_dir = config.output_dir / "sidecar"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    overlay_symbols = load_overlay_symbols(config.overlay_json)
    if config.refresh_sidecar or not (
        (sidecar_dir / "delisted_prices.parquet").exists()
        and (sidecar_dir / "delisted_market_cap.parquet").exists()
    ):
        build_sidecar(
            overlay_symbols=overlay_symbols,
            output_dir=sidecar_dir,
            from_date=config.from_date,
            to_date=config.to_date or datetime.now().strftime("%Y-%m-%d"),
        )

    active_prices = read_daily_prices(
        config.market_db,
        from_date=config.from_date,
        to_date=config.to_date,
        exclude_symbols=(),
    )
    active_caps = read_historical_market_caps(
        config.market_db,
        from_date=config.from_date,
        to_date=config.to_date,
    )
    sidecar_prices, sidecar_caps = read_sidecar(sidecar_dir)
    target_prices = active_prices[active_prices["symbol"].isin(TARGET_SYMBOLS)].copy()

    daily = build_sweep_breadth(
        active_prices=active_prices,
        active_caps=active_caps,
        sidecar_prices=sidecar_prices,
        sidecar_caps=sidecar_caps,
        overlay_symbols=overlay_symbols,
        config=config,
    )
    target_returns = build_forward_returns(target_prices, ALL_HORIZONS)
    results, events = evaluate_recovery_grid(daily, target_returns, config)
    results = apply_sweep_fdr(results)

    results_path = config.output_dir / "recovery_sweep.csv"
    events_path = config.output_dir / "recovery_sweep_events.csv"
    results.to_csv(results_path, index=False)
    events.to_csv(events_path, index=False)
    write_sweep_report(config.report_path, results, events, config)
    return {
        "recovery_sweep": results_path,
        "recovery_sweep_events": events_path,
        "recovery_sweep_report": config.report_path,
    }


def build_sweep_breadth(
    active_prices: pd.DataFrame,
    active_caps: pd.DataFrame,
    sidecar_prices: pd.DataFrame,
    sidecar_caps: pd.DataFrame,
    overlay_symbols: Sequence[str],
    config: RecoverySweepConfig,
) -> pd.DataFrame:
    overlay_set = set(overlay_symbols)
    active_prices = active_prices[~active_prices["symbol"].isin(overlay_set)].copy()
    active_caps = active_caps[~active_caps["symbol"].isin(overlay_set)].copy()
    sidecar_prices = sidecar_prices.copy()
    sidecar_caps = sidecar_caps.copy()

    active_eligible = build_eligible_price_frame(
        prices=active_prices,
        caps=active_caps,
        min_market_cap=config.min_market_cap,
        max_staleness_days=config.max_staleness_days,
        ma_windows=config.ma_windows,
    )
    partial_eligible = build_eligible_price_frame(
        prices=pd.concat([active_prices, sidecar_prices], ignore_index=True),
        caps=pd.concat([active_caps, sidecar_caps], ignore_index=True),
        min_market_cap=config.min_market_cap,
        max_staleness_days=config.max_staleness_days,
        ma_windows=config.ma_windows,
    )

    active_daily = _aggregate_breadth(active_eligible, "_active", ma_windows=config.ma_windows)
    partial_daily = _aggregate_breadth(partial_eligible, "", ma_windows=config.ma_windows)
    partial_daily = partial_daily.rename(
        columns={
            "eligible_count": "eligible_count_with_delisted_partial",
            **{
                f"ma{window}_usable_count": f"ma{window}_usable_count_with_delisted_partial"
                for window in config.ma_windows
            },
        }
    )
    dates = pd.DataFrame(
        {
            "date": sorted(
                set(pd.to_datetime(active_daily["date"]))
                | set(pd.to_datetime(partial_daily["date"]))
            )
        }
    )
    daily = dates.merge(active_daily, on="date", how="left").merge(partial_daily, on="date", how="left")
    daily = daily.sort_values("date").reset_index(drop=True)
    for window in config.ma_windows:
        for suffix in ("", "_active"):
            col = f"breadth_{window}{suffix}"
            daily[f"{col}_sma5"] = daily[col].rolling(5, min_periods=5).mean()
    return daily


def evaluate_recovery_grid(
    daily_breadth: pd.DataFrame,
    target_returns: pd.DataFrame,
    config: RecoverySweepConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df = daily_breadth.merge(target_returns, on="date", how="left")
    rows: List[Dict[str, Any]] = []
    event_rows: List[Dict[str, Any]] = []
    rng = np.random.default_rng(config.random_seed)

    for universe_variant, suffix in (
        ("active_only", "_active"),
        ("with_delisted_partial", ""),
    ):
        for ma_window in config.ma_windows:
            signal_col = f"breadth_{ma_window}{suffix}_sma5"
            comparator_col = f"breadth_{ma_window}_active_sma5"
            valid = df.dropna(subset=[signal_col, comparator_col]).copy()
            if valid.empty:
                continue
            for event_family, low_threshold, trigger_threshold in iter_event_specs(config):
                events = detect_recovery_events(
                    valid[["date", signal_col]].copy(),
                    signal_col=signal_col,
                    event_family=event_family,
                    trigger_threshold=trigger_threshold,
                    low_threshold=low_threshold,
                    cooldown_days=config.cooldown_days,
                )
                for event in events:
                    event_rows.append(
                        {
                            "universe_variant": universe_variant,
                            "event_family": event_family,
                            "ma_window": ma_window,
                            "low_threshold": low_threshold,
                            "trigger_threshold": trigger_threshold,
                            "date": event["date"],
                            "signal_value": event["signal_value"],
                        }
                    )
                event_dates = [event["date"] for event in events]
                for sample_name, sample_df in iter_samples(valid, config.oos_start):
                    sample_dates = set(sample_df["date"])
                    sample_event_dates = [d for d in event_dates if d in sample_dates]
                    for target in PRIMARY_TARGETS:
                        for horizon in ALL_HORIZONS:
                            ret_col = f"{target}_fwd_{horizon}d"
                            return_data = sample_df[["date", ret_col]].dropna().rename(
                                columns={ret_col: "forward_return"}
                            )
                            event_returns = return_data[
                                return_data["date"].isin(sample_event_dates)
                            ].copy()
                            stat = compare_event_returns(
                                event_returns=event_returns,
                                daily_returns=return_data,
                                alternative="upper",
                                bootstrap_samples=config.bootstrap_samples,
                                rng=rng,
                            )
                            rows.append(
                                {
                                    "universe_variant": universe_variant,
                                    "sample": sample_name,
                                    "event_family": event_family,
                                    "ma_window": ma_window,
                                    "low_threshold": low_threshold,
                                    "trigger_threshold": trigger_threshold,
                                    "target": target,
                                    "horizon": horizon,
                                    "event_n": len(event_returns),
                                    "mean_return": stat["event_mean"],
                                    "baseline_mean_return": stat["baseline_mean"],
                                    "diff_mean": stat["diff"],
                                    "median_return": stat["event_median"],
                                    "hit_rate": stat["hit_rate"],
                                    "t_stat": stat["t_stat"],
                                    "p_value": stat["p_value"],
                                    "bootstrap_p_value": stat["bootstrap_p"],
                                    "bootstrap_ci_low": stat["bootstrap_ci_low"],
                                    "bootstrap_ci_high": stat["bootstrap_ci_high"],
                                }
                            )
    return pd.DataFrame(rows), pd.DataFrame(event_rows)


def iter_event_specs(config: RecoverySweepConfig) -> Iterable[Tuple[str, Optional[float], float]]:
    for trigger in config.trigger_thresholds:
        yield "cross_up", np.nan, float(trigger)
    for low in config.low_thresholds:
        for trigger in config.trigger_thresholds:
            if trigger <= low:
                continue
            yield "low_to_trigger", float(low), float(trigger)


def detect_recovery_events(
    df: pd.DataFrame,
    signal_col: str,
    event_family: str,
    trigger_threshold: float,
    low_threshold: Optional[float],
    cooldown_days: int,
) -> List[Dict[str, Any]]:
    values = df.sort_values("date").reset_index(drop=True)
    events: List[Dict[str, Any]] = []
    armed = event_family == "cross_up"
    last_event_idx = -10_000
    prev_value = np.nan
    for idx, row in values.iterrows():
        value = float(row[signal_col])
        if not np.isfinite(value):
            prev_value = value
            continue
        if event_family == "low_to_trigger" and low_threshold is not None and value <= low_threshold:
            armed = True
        crossed = np.isfinite(prev_value) and prev_value < trigger_threshold <= value
        if armed and crossed and idx - last_event_idx >= cooldown_days:
            events.append({"date": row["date"], "signal_value": value})
            last_event_idx = idx
            armed = event_family == "cross_up"
        prev_value = value
    return events


def apply_sweep_fdr(results: pd.DataFrame) -> pd.DataFrame:
    if results.empty:
        results["q_value"] = []
        return results
    frames = []
    group_cols = ["universe_variant", "sample", "event_family", "target", "horizon"]
    for _, group in results.groupby(group_cols, dropna=False):
        g = group.copy()
        g["p_value"] = pd.to_numeric(g["p_value"], errors="coerce").fillna(1.0)
        g["q_value"] = _apply_bh_fdr(g["p_value"].tolist())
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def write_sweep_report(
    report_path: Path,
    results: pd.DataFrame,
    events: pd.DataFrame,
    config: RecoverySweepConfig,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    full = results[
        (results["sample"] == "Full")
        & (results["universe_variant"] == "with_delisted_partial")
        & (results["event_n"] >= 10)
    ].copy()
    oos = results[
        (results["sample"] == "OOS")
        & (results["universe_variant"] == "with_delisted_partial")
        & (results["event_n"] >= 3)
    ].copy()
    lines = [
        "# Broad Breadth Recovery Parameter Sweep",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Scope",
        "",
        "Exploratory only. This sweep searches recovery definitions after the pre-registered study failed.",
        "",
        f"- MA windows: `{list(config.ma_windows)}`",
        f"- Universe: broad `{_format_mcap(config.min_market_cap)}+` PIT eligibility",
        f"- Trigger thresholds: `{[round(x, 2) for x in config.trigger_thresholds]}`",
        f"- Low thresholds: `{[round(x, 2) for x in config.low_thresholds]}`",
        f"- Cooldown: `{config.cooldown_days}` trading days",
        f"- Bootstrap samples: `{config.bootstrap_samples}`",
        "",
        "## Full Sample Leaders",
        "",
        _markdown_table(
            top_rows(full, by=["target", "horizon"], n=8),
            [
                "target",
                "horizon",
                "event_family",
                "ma_window",
                "low_threshold",
                "trigger_threshold",
                "event_n",
                "mean_return",
                "baseline_mean_return",
                "diff_mean",
                "hit_rate",
                "p_value",
                "q_value",
                "bootstrap_p_value",
            ],
        ),
        "",
        "## OOS Leaders",
        "",
        _markdown_table(
            top_rows(oos, by=["target", "horizon"], n=5),
            [
                "target",
                "horizon",
                "event_family",
                "ma_window",
                "low_threshold",
                "trigger_threshold",
                "event_n",
                "mean_return",
                "baseline_mean_return",
                "diff_mean",
                "hit_rate",
                "p_value",
                "q_value",
                "bootstrap_p_value",
            ],
        ),
        "",
        "## Event Count",
        "",
        f"- Total event rows: `{len(events)}`",
        f"- Parameter rows: `{len(results)}`",
        "",
        "## Artifacts",
        "",
        "- `data/breadth_study/recovery_sweep.csv`",
        "- `data/breadth_study/recovery_sweep_events.csv`",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def top_rows(df: pd.DataFrame, by: Sequence[str], n: int) -> pd.DataFrame:
    if df.empty:
        return df
    frames = []
    for _, group in df.groupby(list(by), dropna=False):
        frames.append(
            group.sort_values(
                ["diff_mean", "event_n"],
                ascending=[False, False],
            ).head(n)
        )
    return pd.concat(frames, ignore_index=True).sort_values(
        ["target", "horizon", "diff_mean"],
        ascending=[True, True, False],
    )


def _markdown_table(df: pd.DataFrame, columns: Sequence[str]) -> str:
    if df.empty:
        return "_No rows._"
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = np.nan
    out = out[list(columns)].copy()
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].map(lambda value: "" if pd.isna(value) else f"{value:.4f}")
    rows = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in out.iterrows():
        rows.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(rows)
