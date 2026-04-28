#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backtest.adapters.us_stocks import USStocksAdapter
from backtest.factor_study.report import _apply_bh_fdr
from backtest.research.daily_event_returns import build_close_forward_return_matrices
from backtest.research.rvol_signal_stats import (
    RVOLSignalStatsConfig,
    build_rvol_feature_frames,
    build_rvol_signal_buckets,
    build_symbol_date_index,
    run_bucket_event_stats,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run standalone RVOL(150) cross-up 2σ signal statistics"
    )
    parser.add_argument("--report-date", default="2026-04-24", help="Report date prefix")
    parser.add_argument("--study-start", default="2021-07-01")
    parser.add_argument("--rvol-lookback", type=int, default=150)
    parser.add_argument("--rvol-threshold", type=float, default=2.0)
    parser.add_argument("--pmarp-ema-period", type=int, default=20)
    parser.add_argument("--pmarp-lookback", type=int, default=150)
    parser.add_argument("--flat-move-threshold", type=float, default=0.01)
    parser.add_argument("--pmarp-low-cutoff", type=float, default=30.0)
    parser.add_argument("--pmarp-high-cutoff", type=float, default=60.0)
    parser.add_argument("--horizons", default="5,10,20,40")
    parser.add_argument("--universes", default="pool,extended")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact directory. Default: backtest/new/rvol_up2_signal_stats_<YYYYMMDD>",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    report_stamp = args.report_date.replace("-", "")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else _PROJECT_ROOT / "backtest" / "new" / f"rvol_up2_signal_stats_{report_stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = RVOLSignalStatsConfig(
        study_start_date=args.study_start,
        rvol_lookback=args.rvol_lookback,
        rvol_threshold=args.rvol_threshold,
        pmarp_ema_period=args.pmarp_ema_period,
        pmarp_lookback=args.pmarp_lookback,
        flat_move_threshold=args.flat_move_threshold,
        pmarp_low_cutoff=args.pmarp_low_cutoff,
        pmarp_high_cutoff=args.pmarp_high_cutoff,
    )
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    universes = [u.strip() for u in args.universes.split(",") if u.strip()]

    benchmark_df = _load_benchmark_df()

    all_rows: List[dict] = []
    universe_rows: List[dict] = []
    bucket_count_rows: List[dict] = []

    for universe in universes:
        logger.info("Running RVOL stats universe=%s", universe)
        adapter = USStocksAdapter(universe=universe)
        price_dict = adapter.load_all()
        feature_frames = build_rvol_feature_frames(price_dict, config)
        buckets = build_rvol_signal_buckets(feature_frames, config)
        symbol_date_index = build_symbol_date_index(feature_frames)
        computation_dates = sorted(
            {
                str(date_str)
                for frame in feature_frames.values()
                for date_str in frame["date"].astype(str).tolist()
            }
        )

        raw_returns = build_close_forward_return_matrices(
            price_dict=price_dict,
            computation_dates=computation_dates,
            horizons=horizons,
        )
        excess_returns = _build_close_excess_return_matrices(
            price_dict=price_dict,
            benchmark_df=benchmark_df,
            computation_dates=computation_dates,
            horizons=horizons,
        )

        universe_rows.append(
            {
                "universe": universe,
                "symbols_loaded": len(price_dict),
                "symbols_with_features": len(feature_frames),
                "date_start": computation_dates[0] if computation_dates else "",
                "date_end": computation_dates[-1] if computation_dates else "",
                "study_start": config.study_start_date,
                "rvol_lookback": config.rvol_lookback,
                "rvol_threshold": config.rvol_threshold,
                "pmarp_ema_period": config.pmarp_ema_period,
                "pmarp_lookback": config.pmarp_lookback,
                "flat_move_threshold": config.flat_move_threshold,
                "pmarp_low_cutoff": config.pmarp_low_cutoff,
                "pmarp_high_cutoff": config.pmarp_high_cutoff,
            }
        )

        for bucket_name, events in sorted(buckets.items()):
            bucket_count_rows.append(
                {
                    "universe": universe,
                    "bucket": bucket_name,
                    "raw_events": sum(len(v) for v in events.values()),
                    "symbols": len(events),
                }
            )

        for return_type, return_matrices in (
            ("raw", raw_returns),
            ("excess_spy", excess_returns),
        ):
            for bucket_name, events in sorted(buckets.items()):
                results = run_bucket_event_stats(
                    signal_label=bucket_name,
                    events=events,
                    return_matrices=return_matrices,
                    symbol_date_index=symbol_date_index,
                )
                all_rows.extend(
                    _result_rows(universe=universe, return_type=return_type, results=results)
                )

    stats_df = pd.DataFrame(all_rows)
    universe_df = pd.DataFrame(universe_rows)
    bucket_count_df = pd.DataFrame(bucket_count_rows)

    if not stats_df.empty:
        stats_df["p_fdr"] = _apply_family_fdr(stats_df, ["universe", "return_type"])

    universe_df.to_csv(output_dir / "universe_summary.csv", index=False)
    bucket_count_df.to_csv(output_dir / "bucket_counts.csv", index=False)
    stats_df.to_csv(output_dir / "event_stats.csv", index=False)

    summary_lines = [
        "# RVOL Cross-Up 2σ Signal Statistics Artifacts",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output dir: `{output_dir}`",
        f"- RVOL params: lookback={config.rvol_lookback}, threshold={config.rvol_threshold}",
        (
            f"- Diagnostic buckets: move flat <= {config.flat_move_threshold:.2%}, "
            f"PMARP < {config.pmarp_low_cutoff:.0f}, "
            f"{config.pmarp_low_cutoff:.0f}-{config.pmarp_high_cutoff:.0f}, "
            f">= {config.pmarp_high_cutoff:.0f}"
        ),
        "- De-overlap rule: same symbol cannot re-enter within the forward horizon window",
        "",
        "## Files",
        "",
        "- `universe_summary.csv`",
        "- `bucket_counts.csv`",
        "- `event_stats.csv`",
    ]
    (output_dir / "README.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    logger.info("Artifacts written to %s", output_dir)
    print(output_dir)


def _load_benchmark_df() -> pd.DataFrame:
    benchmark_prices = USStocksAdapter(symbols=["SPY"]).load_all()
    benchmark_df = benchmark_prices.get("SPY")
    if benchmark_df is None or benchmark_df.empty:
        raise ValueError("SPY benchmark data unavailable")
    return benchmark_df


def _build_close_excess_return_matrices(
    price_dict: dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
    computation_dates: List[str],
    horizons: List[int],
) -> dict[int, pd.DataFrame]:
    stock_raw = build_close_forward_return_matrices(price_dict, computation_dates, horizons)
    bench_raw = build_close_forward_return_matrices(
        {"__BENCH__": benchmark_df},
        computation_dates,
        horizons,
    )

    out: dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        ret_df = stock_raw[horizon].copy()
        bench_series = bench_raw[horizon]["__BENCH__"]
        for date_str in ret_df.index:
            bench_ret = bench_series.get(date_str, pd.NA)
            if pd.isna(bench_ret):
                ret_df.loc[date_str] = pd.NA
            else:
                ret_df.loc[date_str] = ret_df.loc[date_str] - float(bench_ret)
        out[horizon] = ret_df
    return out


def _result_rows(
    universe: str,
    return_type: str,
    results: Iterable,
) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
                "universe": universe,
                "return_type": return_type,
                "signal_label": result.signal_label,
                "horizon": result.horizon,
                "n_events_raw": result.n_events_raw,
                "n_events_dedup": result.n_events_dedup,
                "n_events_scored": result.n_events_scored,
                "n_effective": result.n_effective,
                "mean_event_return": result.mean_event_return,
                "median_event_return": result.median_event_return,
                "hit_rate_event": result.hit_rate_event,
                "p10_event_return": result.p10_event_return,
                "p25_event_return": result.p25_event_return,
                "p75_event_return": result.p75_event_return,
                "p90_event_return": result.p90_event_return,
                "mean_cluster_return": result.mean_cluster_return,
                "median_cluster_return": result.median_cluster_return,
                "hit_rate_cluster": result.hit_rate_cluster,
                "t_stat": result.t_stat,
                "p_value": result.p_value,
            }
        )
    return rows


def _apply_family_fdr(df: pd.DataFrame, family_cols: List[str]) -> pd.Series:
    adjusted = pd.Series(index=df.index, dtype=float)
    for _, group in df.groupby(family_cols):
        adjusted.loc[group.index] = _apply_bh_fdr(group["p_value"].tolist())
    return adjusted


if __name__ == "__main__":
    main()
