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
from backtest.research.bbwp_signal_stats import (
    BBWPSignalStatsConfig,
    build_bbwp_feature_frames,
    build_bbwp_signal_buckets,
    compare_trend_buckets,
    filter_events_by_date,
    run_bucket_event_stats,
    run_reversal_score_stats,
)
from backtest.research.daily_event_returns import build_close_forward_return_matrices


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run standalone BBWP downcross 98 signal statistics")
    parser.add_argument("--report-date", default="2026-04-22", help="Report date prefix")
    parser.add_argument("--study-start", default="2021-07-01")
    parser.add_argument("--is-end", default="2023-12-31")
    parser.add_argument("--oos-start", default="2024-01-01")
    parser.add_argument("--bb-period", type=int, default=20)
    parser.add_argument("--bb-std", type=float, default=2.0)
    parser.add_argument("--bbwp-lookback", type=int, default=150)
    parser.add_argument("--universes", default="pool,extended")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact directory. Default: backtest/new/bbwp_down98_signal_stats_<YYYYMMDD>",
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
        else _PROJECT_ROOT / "backtest" / "new" / f"bbwp_down98_signal_stats_{report_stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = BBWPSignalStatsConfig(
        study_start_date=args.study_start,
        is_end_date=args.is_end,
        oos_start_date=args.oos_start,
        bb_period=args.bb_period,
        bb_std=args.bb_std,
        bbwp_lookback=args.bbwp_lookback,
    )
    universes = [u.strip() for u in args.universes.split(",") if u.strip()]

    all_bucket_rows: List[dict] = []
    all_compare_rows: List[dict] = []
    all_reversal_rows: List[dict] = []
    universe_rows: List[dict] = []
    bucket_count_rows: List[dict] = []

    for universe in universes:
        logger.info("Running BBWP stats universe=%s", universe)
        adapter = USStocksAdapter(universe=universe)
        price_dict = adapter.load_all()

        feature_frames = build_bbwp_feature_frames(price_dict, config)
        buckets = build_bbwp_signal_buckets(feature_frames, config)
        computation_dates = sorted(
            {
                str(date_str)
                for frame in feature_frames.values()
                for date_str in frame["date"].astype(str).tolist()
            }
        )
        return_matrices = build_close_forward_return_matrices(
            price_dict=price_dict,
            computation_dates=computation_dates,
            horizons=[3, 7, 10],
        )

        universe_rows.append(
            {
                "universe": universe,
                "symbols_loaded": len(price_dict),
                "symbols_with_features": len(feature_frames),
                "date_start": computation_dates[0] if computation_dates else "",
                "date_end": computation_dates[-1] if computation_dates else "",
                "bb_period": config.bb_period,
                "bb_std": config.bb_std,
                "bbwp_lookback": config.bbwp_lookback,
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

        samples = {
            "Full": (config.study_start_date, None),
            "IS": (config.study_start_date, config.is_end_date),
            "OOS": (config.oos_start_date, None),
        }

        for sample, (start_date, end_date) in samples.items():
            above_events = filter_events_by_date(
                buckets.get("bbwp_down98_above_mid", {}),
                start_date=start_date,
                end_date=end_date,
            )
            below_events = filter_events_by_date(
                buckets.get("bbwp_down98_below_mid", {}),
                start_date=start_date,
                end_date=end_date,
            )
            all_events = filter_events_by_date(
                buckets.get("bbwp_down98_all", {}),
                start_date=start_date,
                end_date=end_date,
            )

            for label, events in (
                ("bbwp_down98_all", all_events),
                ("bbwp_down98_above_mid", above_events),
                ("bbwp_down98_below_mid", below_events),
            ):
                results = run_bucket_event_stats(label, events, return_matrices)
                all_bucket_rows.extend(_bucket_rows(universe, sample, results))

            comparisons = compare_trend_buckets(above_events, below_events, return_matrices)
            all_compare_rows.extend(_comparison_rows(universe, sample, comparisons))

            reversal = run_reversal_score_stats(above_events, below_events, return_matrices)
            all_reversal_rows.extend(_reversal_rows(universe, sample, reversal))

    bucket_df = pd.DataFrame(all_bucket_rows)
    compare_df = pd.DataFrame(all_compare_rows)
    reversal_df = pd.DataFrame(all_reversal_rows)
    universe_df = pd.DataFrame(universe_rows)
    bucket_count_df = pd.DataFrame(bucket_count_rows)

    if not bucket_df.empty:
        bucket_df["p_fdr"] = _apply_family_fdr(bucket_df, ["universe", "sample"])
    if not compare_df.empty:
        compare_df["p_fdr"] = _apply_family_fdr(compare_df, ["universe", "sample"])
    if not reversal_df.empty:
        reversal_df["p_fdr"] = _apply_family_fdr(reversal_df, ["universe", "sample"])

    universe_df.to_csv(output_dir / "universe_summary.csv", index=False)
    bucket_count_df.to_csv(output_dir / "bucket_counts.csv", index=False)
    bucket_df.to_csv(output_dir / "bucket_stats.csv", index=False)
    compare_df.to_csv(output_dir / "comparison_stats.csv", index=False)
    reversal_df.to_csv(output_dir / "reversal_stats.csv", index=False)

    summary_lines = [
        "# BBWP Downcross 98 Signal Statistics Artifacts",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output dir: `{output_dir}`",
        f"- BB params: period={config.bb_period}, std={config.bb_std}, lookback={config.bbwp_lookback}",
        "",
        "## Files",
        "",
        "- `universe_summary.csv`",
        "- `bucket_counts.csv`",
        "- `bucket_stats.csv`",
        "- `comparison_stats.csv`",
        "- `reversal_stats.csv`",
    ]
    (output_dir / "README.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    logger.info("Artifacts written to %s", output_dir)
    print(output_dir)


def _bucket_rows(universe: str, sample: str, results: Iterable) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
                "universe": universe,
                "sample": sample,
                "signal_label": result.signal_label,
                "horizon": result.horizon,
                "n_events": result.n_events,
                "n_effective": result.n_effective,
                "mean_return": result.mean_return,
                "median_return": result.median_return,
                "hit_rate": result.hit_rate,
                "t_stat": result.t_stat,
                "p_value": result.p_value,
            }
        )
    return rows


def _comparison_rows(universe: str, sample: str, results: Iterable) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
                "universe": universe,
                "sample": sample,
                "label": result.label,
                "horizon": result.horizon,
                "above_n_events": result.above_n_events,
                "above_n_effective": result.above_n_effective,
                "below_n_events": result.below_n_events,
                "below_n_effective": result.below_n_effective,
                "above_mean_return": result.above_mean_return,
                "below_mean_return": result.below_mean_return,
                "diff_below_minus_above": result.diff_below_minus_above,
                "t_stat": result.t_stat,
                "p_value": result.p_value,
            }
        )
    return rows


def _reversal_rows(universe: str, sample: str, results: Iterable) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
                "universe": universe,
                "sample": sample,
                "label": result.label,
                "horizon": result.horizon,
                "n_events": result.n_events,
                "n_effective": result.n_effective,
                "mean_score": result.mean_score,
                "median_score": result.median_score,
                "positive_rate": result.positive_rate,
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
