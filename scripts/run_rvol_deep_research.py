#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
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
from backtest.research.event_path_diagnostics import run_tail_diagnostics
from backtest.research.rvol_deep_research import (
    build_pmarp_rvol_lift_cohorts,
    build_strong_state_rvol_cohorts,
    comparison_pairs_from_cohorts,
    run_conditional_lift_comparisons,
)
from backtest.research.rvol_event_explainers import (
    load_earnings_dates_from_market_db,
    load_social_attention_zscores_from_market_db,
    summarize_event_explainers,
)
from backtest.research.rvol_signal_stats import (
    RVOLSignalStatsConfig,
    build_rvol_feature_frames,
    build_symbol_date_index,
    run_bucket_event_stats,
)
from config.settings import MARKET_DB_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RVOL deep research study")
    parser.add_argument("--report-date", default="2026-04-24", help="Report date prefix")
    parser.add_argument("--study-start", default="2021-07-01")
    parser.add_argument("--rvol-lookback", type=int, default=150)
    parser.add_argument("--rvol-threshold", type=float, default=2.0)
    parser.add_argument("--pmarp-ema-period", type=int, default=20)
    parser.add_argument("--pmarp-lookback", type=int, default=150)
    parser.add_argument("--pmarp-up-threshold", type=float, default=2.0)
    parser.add_argument("--pmarp-low-cutoff", type=float, default=30.0)
    parser.add_argument("--pmarp-high-cutoff", type=float, default=60.0)
    parser.add_argument("--flat-move-threshold", type=float, default=0.01)
    parser.add_argument("--horizons", default="5,10,20,40,60")
    parser.add_argument("--universes", default="pool,extended")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact directory. Default: backtest/new/rvol_deep_research_<YYYYMMDD>",
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
        else _PROJECT_ROOT / "backtest" / "new" / f"rvol_deep_research_{report_stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = RVOLSignalStatsConfig(
        study_start_date=args.study_start,
        rvol_lookback=args.rvol_lookback,
        rvol_threshold=args.rvol_threshold,
        pmarp_ema_period=args.pmarp_ema_period,
        pmarp_lookback=args.pmarp_lookback,
        pmarp_up_threshold=args.pmarp_up_threshold,
        pmarp_low_cutoff=args.pmarp_low_cutoff,
        pmarp_high_cutoff=args.pmarp_high_cutoff,
        flat_move_threshold=args.flat_move_threshold,
    )
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    universes = [u.strip() for u in args.universes.split(",") if u.strip()]

    benchmark_df = _load_benchmark_df()
    earnings_dates = load_earnings_dates_from_market_db(MARKET_DB_PATH)
    social_scores = load_social_attention_zscores_from_market_db(MARKET_DB_PATH)

    all_universe_rows: List[dict] = []
    all_count_rows: List[dict] = []
    all_event_rows: List[dict] = []
    all_lift_rows: List[dict] = []
    all_tail_rows: List[dict] = []
    all_explainer_rows: List[dict] = []

    for universe in universes:
        logger.info("Running RVOL deep research universe=%s", universe)
        adapter = USStocksAdapter(universe=universe)
        price_dict = adapter.load_all()
        feature_frames = build_rvol_feature_frames(price_dict, config)
        symbol_date_index = build_symbol_date_index(feature_frames)
        coverage = _price_coverage(price_dict)
        extended_volume_failed = universe == "extended" and coverage["volume_non_null_rate"] < 0.95

        computation_dates = sorted(
            {
                str(date_str)
                for frame in feature_frames.values()
                for date_str in frame["date"].astype(str).tolist()
            }
        )
        raw_returns = build_close_forward_return_matrices(price_dict, computation_dates, horizons)
        excess_returns = _build_close_excess_return_matrices(
            price_dict=price_dict,
            benchmark_df=benchmark_df,
            computation_dates=computation_dates,
            horizons=horizons,
        )

        pmarp_cohorts = build_pmarp_rvol_lift_cohorts(feature_frames, config)
        strong_cohorts = build_strong_state_rvol_cohorts(feature_frames, config)
        event_cohorts = {**pmarp_cohorts, **strong_cohorts}

        all_universe_rows.append(
            {
                "universe": universe,
                "symbols_loaded": len(price_dict),
                "symbols_with_features": len(feature_frames),
                "date_start": computation_dates[0] if computation_dates else "",
                "date_end": computation_dates[-1] if computation_dates else "",
                "study_start": config.study_start_date,
                "rvol_lookback": config.rvol_lookback,
                "rvol_threshold_zscore": config.rvol_threshold,
                "pmarp_ema_period": config.pmarp_ema_period,
                "pmarp_lookback": config.pmarp_lookback,
                "pmarp_up_threshold": config.pmarp_up_threshold,
                "pmarp_low_cutoff": config.pmarp_low_cutoff,
                "pmarp_high_cutoff": config.pmarp_high_cutoff,
                "extended_volume_coverage_failed": extended_volume_failed,
                **coverage,
            }
        )

        for label, events in sorted(event_cohorts.items()):
            all_count_rows.append(
                {
                    "universe": universe,
                    "cohort": label,
                    "raw_events": sum(len(v) for v in events.values()),
                    "symbols": len(events),
                    "research_block": _research_block(label),
                }
            )

        for return_type, matrices in (("raw", raw_returns), ("excess_spy", excess_returns)):
            for label, events in sorted(event_cohorts.items()):
                results = run_bucket_event_stats(
                    signal_label=label,
                    events=events,
                    return_matrices=matrices,
                    symbol_date_index=symbol_date_index,
                )
                all_event_rows.extend(_event_rows(universe, return_type, _research_block(label), results))

            lift_results = run_conditional_lift_comparisons(
                comparisons=comparison_pairs_from_cohorts(pmarp_cohorts),
                return_matrices=matrices,
                symbol_date_index=symbol_date_index,
            )
            all_lift_rows.extend(_lift_rows(universe, return_type, lift_results))

        tail_cohorts = _tail_cohort_subset(event_cohorts)
        tail_results = run_tail_diagnostics(tail_cohorts, price_dict, horizons)
        all_tail_rows.extend(_tail_rows(universe, tail_results))

        explainer_results = summarize_event_explainers(
            tail_cohorts,
            earnings_dates=earnings_dates,
            social_scores=social_scores,
        )
        all_explainer_rows.extend(_explainer_rows(universe, explainer_results))

    universe_df = pd.DataFrame(all_universe_rows)
    count_df = pd.DataFrame(all_count_rows)
    event_df = pd.DataFrame(all_event_rows)
    lift_df = pd.DataFrame(all_lift_rows)
    tail_df = pd.DataFrame(all_tail_rows)
    explainer_df = pd.DataFrame(all_explainer_rows)

    if not event_df.empty:
        event_df["p_fdr"] = _apply_family_fdr(
            event_df,
            ["universe", "return_type", "research_block"],
        )
    if not lift_df.empty:
        lift_df["p_fdr"] = _apply_family_fdr(
            lift_df,
            ["universe", "return_type", "sample", "research_block"],
        )

    universe_df.to_csv(output_dir / "universe_summary.csv", index=False)
    count_df.to_csv(output_dir / "cohort_counts.csv", index=False)
    event_df.to_csv(output_dir / "event_stats.csv", index=False)
    lift_df.to_csv(output_dir / "conditional_lift.csv", index=False)
    tail_df.to_csv(output_dir / "tail_diagnostics.csv", index=False)
    explainer_df.to_csv(output_dir / "event_explainers.csv", index=False)

    _write_readme(output_dir, config, horizons, universes)
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


def _price_coverage(price_dict: dict[str, pd.DataFrame]) -> dict:
    total_rows = sum(len(frame) for frame in price_dict.values())
    if total_rows == 0:
        return {
            "price_rows": 0,
            "close_non_null_rate": 0.0,
            "volume_non_null_rate": 0.0,
            "high_low_non_null_rate": 0.0,
        }

    close_ok = 0
    volume_ok = 0
    high_low_ok = 0
    for frame in price_dict.values():
        close_ok += int(frame["close"].notna().sum()) if "close" in frame else 0
        volume_ok += int(frame["volume"].notna().sum()) if "volume" in frame else 0
        if "high" in frame and "low" in frame:
            high_low_ok += int((frame["high"].notna() & frame["low"].notna()).sum())

    return {
        "price_rows": total_rows,
        "close_non_null_rate": close_ok / total_rows,
        "volume_non_null_rate": volume_ok / total_rows,
        "high_low_non_null_rate": high_low_ok / total_rows,
    }


def _event_rows(universe: str, return_type: str, research_block: str, results: Iterable) -> List[dict]:
    rows = []
    for result in results:
        row = asdict(result)
        row.update(
            {
                "universe": universe,
                "return_type": return_type,
                "research_block": research_block,
            }
        )
        rows.append(row)
    return rows


def _lift_rows(universe: str, return_type: str, results: Iterable) -> List[dict]:
    rows = []
    for result in results:
        row = asdict(result)
        row.update(
            {
                "universe": universe,
                "return_type": return_type,
                "sample": "Full",
                "research_block": "pmarp_rvol_lift",
            }
        )
        rows.append(row)
    return rows


def _tail_rows(universe: str, results: Iterable) -> List[dict]:
    rows = []
    for result in results:
        row = asdict(result)
        row["universe"] = universe
        rows.append(row)
    return rows


def _explainer_rows(universe: str, results: Iterable) -> List[dict]:
    rows = []
    for result in results:
        row = asdict(result)
        row["universe"] = universe
        rows.append(row)
    return rows


def _apply_family_fdr(df: pd.DataFrame, family_cols: List[str]) -> pd.Series:
    adjusted = pd.Series(index=df.index, dtype=float)
    for _, group in df.groupby(family_cols):
        adjusted.loc[group.index] = _apply_bh_fdr(group["p_value"].tolist())
    return adjusted


def _research_block(label: str) -> str:
    if label.startswith("pmarp_up2_"):
        return "pmarp_rvol_lift"
    if label.startswith("rvol_up2_pmarp_gte60"):
        return "strong_state_confirmation"
    if label.startswith("rvol_up2_pmarp_lt30"):
        return "low_state_rebound"
    return "other"


def _tail_cohort_subset(cohorts: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    labels = [
        "pmarp_up2_base",
        "pmarp_up2_accept_rvol_recent3",
        "pmarp_up2_reject_rvol_recent3",
        "rvol_up2_pmarp_gte60",
        "rvol_up2_pmarp_gte60_sign_pos_close_near_high",
        "rvol_up2_pmarp_lt30_sign_neg",
        "rvol_up2_pmarp_lt30_close_near_low",
    ]
    return {label: cohorts[label] for label in labels if label in cohorts}


def _write_readme(
    output_dir: Path,
    config: RVOLSignalStatsConfig,
    horizons: List[int],
    universes: List[str],
) -> None:
    lines = [
        "# RVOL Deep Research Artifacts",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output dir: `{output_dir}`",
        f"- Universes: {', '.join(universes)}",
        f"- Horizons: {', '.join(str(h) for h in horizons)}",
        (
            f"- RVOL: lookback={config.rvol_lookback}, "
            f"threshold={config.rvol_threshold}σ z-score"
        ),
        (
            f"- PMARP: ema={config.pmarp_ema_period}, "
            f"lookback={config.pmarp_lookback}, upcross={config.pmarp_up_threshold}"
        ),
        "",
        "## Files",
        "",
        "- `universe_summary.csv`",
        "- `cohort_counts.csv`",
        "- `event_stats.csv`",
        "- `conditional_lift.csv`",
        "- `tail_diagnostics.csv`",
        "- `event_explainers.csv`",
    ]
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
