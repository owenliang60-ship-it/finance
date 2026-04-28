#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backtest.adapters.us_stocks import USStocksAdapter
from backtest.factor_study.report import _apply_bh_fdr
from backtest.research import (
    PMARPBBWPStudyConfig,
    build_cohorts_from_feature_frames,
    build_feature_frames,
    build_t1open_excess_return_matrices,
    compare_event_groups,
    filter_events_by_date,
    run_labeled_event_study,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PMARP + BBWP daily study")
    parser.add_argument("--report-date", default="2026-04-22", help="Report date prefix")
    parser.add_argument("--study-start", default="2021-07-01")
    parser.add_argument("--is-end", default="2023-12-31")
    parser.add_argument("--oos-start", default="2024-01-01")
    parser.add_argument("--bbwp-period", type=int, default=20)
    parser.add_argument("--bbwp-std", type=float, default=2.0)
    parser.add_argument("--bbwp-lookback", type=int, default=150)
    parser.add_argument("--recent-confirm-window", type=int, default=3)
    parser.add_argument("--universes", default="pool,extended")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact directory. Default: backtest/new/pmarp_bbwp_daily_study_<YYYYMMDD>",
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
        else _PROJECT_ROOT / "backtest" / "new" / f"pmarp_bbwp_daily_study_{report_stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = PMARPBBWPStudyConfig(
        study_start_date=args.study_start,
        is_end_date=args.is_end,
        oos_start_date=args.oos_start,
        bbwp_period=args.bbwp_period,
        bbwp_std=args.bbwp_std,
        bbwp_lookback=args.bbwp_lookback,
        recent_confirm_window=args.recent_confirm_window,
    )
    universes = [u.strip() for u in args.universes.split(",") if u.strip()]

    all_event_rows: List[dict] = []
    all_compare_rows: List[dict] = []
    universe_rows: List[dict] = []
    cohort_count_rows: List[dict] = []

    for universe in universes:
        logger.info("Running universe=%s", universe)
        adapter = USStocksAdapter(universe=universe)
        price_dict = adapter.load_all()
        benchmark_df = adapter._load_prices("SPY")
        if benchmark_df is None or benchmark_df.empty:
            raise RuntimeError("SPY benchmark data unavailable")

        feature_frames = build_feature_frames(price_dict, benchmark_df, config)
        cohorts = build_cohorts_from_feature_frames(feature_frames, config)
        computation_dates = sorted(
            {
                str(date_str)
                for frame in feature_frames.values()
                for date_str in frame["date"].astype(str).tolist()
            }
        )

        return_matrices = build_t1open_excess_return_matrices(
            price_dict=price_dict,
            benchmark_df=benchmark_df,
            computation_dates=computation_dates,
            horizons=[10, 20, 30, 60],
        )

        universe_rows.append(
            {
                "universe": universe,
                "symbols_loaded": len(price_dict),
                "symbols_with_features": len(feature_frames),
                "benchmark_rows": len(benchmark_df),
                "date_start": computation_dates[0] if computation_dates else "",
                "date_end": computation_dates[-1] if computation_dates else "",
            }
        )
        for cohort_name, events in sorted(cohorts.items()):
            cohort_count_rows.append(
                {
                    "universe": universe,
                    "cohort": cohort_name,
                    "raw_events": sum(len(v) for v in events.values()),
                    "symbols": len(events),
                }
            )

        samples = {
            "Full": (config.study_start_date, None),
            "IS": (config.study_start_date, config.is_end_date),
            "OOS": (config.oos_start_date, None),
        }

        standalone_labels = [
            "bbwp_down98_after_downtrend",
            "bbwp_down98_after_uptrend",
            "bbwp_highturn_after_downtrend",
            "bbwp_highturn_after_uptrend",
        ]
        combo_labels = [
            "pmarp_up2_base",
            "pmarp_up2_accept_down98_same_day",
            "pmarp_up2_accept_down98_recent3",
            "pmarp_up2_accept_highturn_same_day",
            "pmarp_up2_accept_highturn_recent3",
        ]
        compare_defs = [
            (
                "pmarp_lift_down98_same_day",
                "pmarp_up2_accept_down98_same_day",
                "pmarp_up2_reject_down98_same_day",
            ),
            (
                "pmarp_lift_down98_recent3",
                "pmarp_up2_accept_down98_recent3",
                "pmarp_up2_reject_down98_recent3",
            ),
            (
                "pmarp_lift_highturn_same_day",
                "pmarp_up2_accept_highturn_same_day",
                "pmarp_up2_reject_highturn_same_day",
            ),
            (
                "pmarp_lift_highturn_recent3",
                "pmarp_up2_accept_highturn_recent3",
                "pmarp_up2_reject_highturn_recent3",
            ),
        ]

        for sample, (start_date, end_date) in samples.items():
            standalone_matrices = _filter_return_matrices(return_matrices, start_date, end_date, horizons=[10, 20, 30, 60])
            combo_matrices = _filter_return_matrices(return_matrices, start_date, end_date, horizons=[30, 60])

            for label in standalone_labels:
                events = filter_events_by_date(cohorts.get(label, {}), start_date=start_date, end_date=end_date)
                results = run_labeled_event_study(label, events, standalone_matrices)
                all_event_rows.extend(_event_rows(universe, sample, "standalone_bbwp", results))

            for label in combo_labels:
                events = filter_events_by_date(cohorts.get(label, {}), start_date=start_date, end_date=end_date)
                results = run_labeled_event_study(label, events, combo_matrices)
                all_event_rows.extend(_event_rows(universe, sample, "pmarp_combo", results))

            for label, accept_label, reject_label in compare_defs:
                accepted = filter_events_by_date(cohorts.get(accept_label, {}), start_date=start_date, end_date=end_date)
                rejected = filter_events_by_date(cohorts.get(reject_label, {}), start_date=start_date, end_date=end_date)
                comparisons = compare_event_groups(label, accepted, rejected, combo_matrices, sample)
                all_compare_rows.extend(_comparison_rows(universe, comparisons))

    event_df = pd.DataFrame(all_event_rows)
    compare_df = pd.DataFrame(all_compare_rows)
    universe_df = pd.DataFrame(universe_rows)
    cohort_count_df = pd.DataFrame(cohort_count_rows)

    if not event_df.empty:
        event_df["p_fdr"] = _apply_family_fdr(event_df, ["universe", "sample", "family"])
    if not compare_df.empty:
        compare_df["p_fdr"] = _apply_family_fdr(compare_df, ["universe", "sample"])

    universe_df.to_csv(output_dir / "universe_summary.csv", index=False)
    cohort_count_df.to_csv(output_dir / "cohort_counts.csv", index=False)
    event_df.to_csv(output_dir / "event_results.csv", index=False)
    compare_df.to_csv(output_dir / "comparison_results.csv", index=False)

    summary_lines = [
        "# PMARP + BBWP Daily Study Artifacts",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output dir: `{output_dir}`",
        "",
        "## Files",
        "",
        "- `universe_summary.csv`",
        "- `cohort_counts.csv`",
        "- `event_results.csv`",
        "- `comparison_results.csv`",
    ]
    (output_dir / "README.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    logger.info("Artifacts written to %s", output_dir)
    print(output_dir)


def _event_rows(universe: str, sample: str, family: str, results: Iterable) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
                "universe": universe,
                "sample": sample,
                "family": family,
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


def _comparison_rows(universe: str, results: Iterable) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
                "universe": universe,
                "sample": result.sample,
                "label": result.label,
                "horizon": result.horizon,
                "accepted_n_events": result.accepted_n_events,
                "accepted_n_effective": result.accepted_n_effective,
                "rejected_n_events": result.rejected_n_events,
                "rejected_n_effective": result.rejected_n_effective,
                "accepted_mean_return": result.accepted_mean_return,
                "rejected_mean_return": result.rejected_mean_return,
                "accepted_hit_rate": result.accepted_hit_rate,
                "rejected_hit_rate": result.rejected_hit_rate,
                "diff_mean_return": result.diff_mean_return,
                "t_stat": result.t_stat,
                "p_value": result.p_value,
            }
        )
    return rows


def _apply_family_fdr(df: pd.DataFrame, group_cols: List[str]) -> List[float]:
    adjusted = pd.Series(index=df.index, dtype=float)
    for _, group in df.groupby(group_cols, sort=False):
        p_adj = _apply_bh_fdr(group["p_value"].astype(float).tolist())
        adjusted.loc[group.index] = p_adj
    return adjusted.tolist()


def _filter_return_matrices(
    return_matrices: Dict[int, pd.DataFrame],
    start_date: Optional[str],
    end_date: Optional[str],
    horizons: List[int],
) -> Dict[int, pd.DataFrame]:
    filtered: Dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        frame = return_matrices[horizon]
        mask = pd.Series(True, index=frame.index)
        if start_date is not None:
            mask &= frame.index >= start_date
        if end_date is not None:
            mask &= frame.index <= end_date
        filtered[horizon] = frame.loc[mask]
    return filtered


if __name__ == "__main__":
    main()
