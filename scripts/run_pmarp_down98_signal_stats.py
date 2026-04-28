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
from backtest.research.pmarp_signal_stats import (
    PMARPSignalStatsConfig,
    build_pmarp_feature_frames,
    build_pmarp_signal_events,
    filter_events_by_date,
    run_signal_event_stats,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run standalone PMARP downcross 98 signal statistics")
    parser.add_argument("--report-date", default="2026-04-22", help="Report date prefix")
    parser.add_argument("--study-start", default="2021-07-01")
    parser.add_argument("--is-end", default="2023-12-31")
    parser.add_argument("--oos-start", default="2024-01-01")
    parser.add_argument("--ema-period", type=int, default=20)
    parser.add_argument("--pmarp-lookback", type=int, default=150)
    parser.add_argument("--universes", default="pool,extended")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact directory. Default: backtest/new/pmarp_down98_signal_stats_<YYYYMMDD>",
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
        else _PROJECT_ROOT / "backtest" / "new" / f"pmarp_down98_signal_stats_{report_stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = PMARPSignalStatsConfig(
        study_start_date=args.study_start,
        is_end_date=args.is_end,
        oos_start_date=args.oos_start,
        ema_period=args.ema_period,
        pmarp_lookback=args.pmarp_lookback,
    )
    universes = [u.strip() for u in args.universes.split(",") if u.strip()]

    all_event_rows: List[dict] = []
    universe_rows: List[dict] = []
    count_rows: List[dict] = []

    for universe in universes:
        logger.info("Running PMARP down98 stats universe=%s", universe)
        adapter = USStocksAdapter(universe=universe)
        price_dict = adapter.load_all()

        feature_frames = build_pmarp_feature_frames(price_dict, config)
        events = build_pmarp_signal_events(feature_frames, config)
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
            horizons=[7, 14, 21],
        )

        universe_rows.append(
            {
                "universe": universe,
                "symbols_loaded": len(price_dict),
                "symbols_with_features": len(feature_frames),
                "date_start": computation_dates[0] if computation_dates else "",
                "date_end": computation_dates[-1] if computation_dates else "",
                "ema_period": config.ema_period,
                "pmarp_lookback": config.pmarp_lookback,
            }
        )

        signal_events = events.get("pmarp_down98", {})
        count_rows.append(
            {
                "universe": universe,
                "signal": "pmarp_down98",
                "raw_events": sum(len(v) for v in signal_events.values()),
                "symbols": len(signal_events),
            }
        )

        samples = {
            "Full": (config.study_start_date, None),
            "IS": (config.study_start_date, config.is_end_date),
            "OOS": (config.oos_start_date, None),
        }

        for sample, (start_date, end_date) in samples.items():
            filtered_events = filter_events_by_date(signal_events, start_date=start_date, end_date=end_date)
            results = run_signal_event_stats("pmarp_down98", filtered_events, return_matrices)
            all_event_rows.extend(_event_rows(universe, sample, results))

    event_df = pd.DataFrame(all_event_rows)
    universe_df = pd.DataFrame(universe_rows)
    count_df = pd.DataFrame(count_rows)

    if not event_df.empty:
        event_df["p_fdr"] = _apply_family_fdr(event_df, ["universe", "sample"])

    universe_df.to_csv(output_dir / "universe_summary.csv", index=False)
    count_df.to_csv(output_dir / "signal_counts.csv", index=False)
    event_df.to_csv(output_dir / "event_stats.csv", index=False)

    summary_lines = [
        "# PMARP Downcross 98 Signal Statistics Artifacts",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output dir: `{output_dir}`",
        f"- PMARP params: ema_period={config.ema_period}, lookback={config.pmarp_lookback}",
        "",
        "## Files",
        "",
        "- `universe_summary.csv`",
        "- `signal_counts.csv`",
        "- `event_stats.csv`",
    ]
    (output_dir / "README.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    logger.info("Artifacts written to %s", output_dir)
    print(output_dir)


def _event_rows(universe: str, sample: str, results: Iterable) -> List[dict]:
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
