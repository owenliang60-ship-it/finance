#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backtest.adapters.us_stocks import USStocksAdapter
from backtest.factor_study.event_study import EventStudyResult, run_event_study
from backtest.factor_study.forward_returns import build_excess_return_matrix
from backtest.factor_study.report import _apply_bh_fdr
from backtest.factor_study.signals import SignalDefinition, SignalType, detect_signals
from src.indicators.pmarp import calculate_pmarp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run preregistered PMARP cross_up 2%% hardening study (daily)",
    )
    parser.add_argument("--report-date", default="2026-04-22")
    parser.add_argument("--study-start", default="2021-07-01")
    parser.add_argument("--oos-start", default="2025-01-01")
    parser.add_argument(
        "--universe",
        choices=["pool", "extended", "extended_true"],
        default="extended",
    )
    parser.add_argument("--mcap-threshold", type=float, default=10e9)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--horizons", default="7,30,60")
    parser.add_argument("--ema-period", type=int, default=20)
    parser.add_argument("--lookback", type=int, default=150)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact directory. Default: backtest/new/pmarp_crossup_hardening_<YYYYMMDD>",
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
        else _PROJECT_ROOT / "backtest" / "new" / f"pmarp_crossup_hardening_{report_stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    adapter = USStocksAdapter(
        universe=args.universe,
        mcap_threshold=args.mcap_threshold,
    )
    price_dict = adapter.load_all()
    benchmark_df = adapter._load_prices(args.benchmark)
    if benchmark_df is None or benchmark_df.empty:
        raise RuntimeError(f"{args.benchmark} benchmark data unavailable")

    computation_dates = [
        d for d in adapter.get_trading_dates()
        if d >= args.study_start
    ]
    score_history = build_pmarp_score_history(
        price_dict=price_dict,
        study_start=args.study_start,
        ema_period=args.ema_period,
        lookback=args.lookback,
    )
    signal_def = SignalDefinition(
        signal_type=SignalType.CROSS_UP,
        threshold=2.0,
    )
    all_events = detect_signals(score_history, signal_def)
    return_matrices = build_excess_return_matrix(
        price_dict=price_dict,
        benchmark_df=benchmark_df,
        computation_dates=computation_dates,
        horizons=horizons,
    )

    samples = {
        "Full": None,
        "IS": lambda d: d < args.oos_start,
        "OOS": lambda d: d >= args.oos_start,
    }

    rows: List[dict] = []
    sample_counts: List[dict] = []
    for sample_name, predicate in samples.items():
        sample_events = filter_events(all_events, predicate)
        sample_counts.append(
            {
                "sample": sample_name,
                "raw_events": sum(len(v) for v in sample_events.values()),
                "symbols": len(sample_events),
            }
        )
        results = run_event_study("PMARP", signal_def, sample_events, return_matrices)
        rows.extend(result_rows(sample_name, results))

    event_df = pd.DataFrame(rows)
    if not event_df.empty:
        event_df["p_fdr"] = apply_sample_fdr(event_df)

    universe_df = pd.DataFrame(
        [
            {
                "universe": args.universe,
                "symbols_loaded": len(price_dict),
                "symbols_with_scores": len(score_history),
                "date_start": computation_dates[0] if computation_dates else "",
                "date_end": computation_dates[-1] if computation_dates else "",
                "study_start": args.study_start,
                "oos_start": args.oos_start,
                "benchmark": args.benchmark,
                "ema_period": args.ema_period,
                "lookback": args.lookback,
                "mcap_threshold": args.mcap_threshold,
            }
        ]
    )

    universe_df.to_csv(output_dir / "universe_summary.csv", index=False)
    pd.DataFrame(sample_counts).to_csv(output_dir / "sample_counts.csv", index=False)
    event_df.to_csv(output_dir / "event_results.csv", index=False)
    write_summary(
        output_dir=output_dir,
        event_df=event_df,
        universe_df=universe_df,
    )

    logger.info("Artifacts written to %s", output_dir)
    print(output_dir)
    print()
    print(event_df.to_string(index=False))


def build_pmarp_score_history(
    price_dict: Dict[str, pd.DataFrame],
    study_start: str,
    ema_period: int,
    lookback: int,
) -> Dict[str, List[Tuple[str, float]]]:
    score_history: Dict[str, List[Tuple[str, float]]] = {}
    for symbol, raw in price_dict.items():
        ordered = raw.sort_values("date").reset_index(drop=True).copy()
        ordered["date"] = ordered["date"].astype(str).str[:10]
        pmarp = calculate_pmarp(
            ordered["close"].astype(float),
            ema_period=ema_period,
            lookback=lookback,
        )
        history = [
            (date_str, float(score))
            for date_str, score in zip(ordered["date"], pmarp)
            if pd.notna(score) and date_str >= study_start
        ]
        if history:
            score_history[symbol] = history
    return score_history


def filter_events(
    events: Dict[str, List[str]],
    predicate,
) -> Dict[str, List[str]]:
    if predicate is None:
        return {symbol: list(dates) for symbol, dates in events.items()}

    filtered: Dict[str, List[str]] = {}
    for symbol, dates in events.items():
        kept = [date_str for date_str in dates if predicate(date_str)]
        if kept:
            filtered[symbol] = kept
    return filtered


def result_rows(sample: str, results: Iterable[EventStudyResult]) -> List[dict]:
    rows: List[dict] = []
    for result in results:
        rows.append(
            {
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


def apply_sample_fdr(event_df: pd.DataFrame) -> List[float]:
    adjusted = pd.Series(index=event_df.index, dtype=float)
    for sample_name, group in event_df.groupby("sample", sort=False):
        del sample_name
        adjusted.loc[group.index] = _apply_bh_fdr(group["p_value"].astype(float).tolist())
    return adjusted.tolist()


def write_summary(
    output_dir: Path,
    event_df: pd.DataFrame,
    universe_df: pd.DataFrame,
) -> None:
    lines = [
        "# PMARP Cross-Up 2% Hardening Artifacts",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output dir: `{output_dir}`",
        f"- Universe: `{universe_df.iloc[0]['universe']}`",
        f"- Symbols loaded: `{int(universe_df.iloc[0]['symbols_loaded'])}`",
        f"- Study range: `{universe_df.iloc[0]['date_start']}` → `{universe_df.iloc[0]['date_end']}`",
        f"- OOS start: `{universe_df.iloc[0]['oos_start']}`",
        "",
        "## Files",
        "",
        "- `universe_summary.csv`",
        "- `sample_counts.csv`",
        "- `event_results.csv`",
        "",
        "## OOS Snapshot",
        "",
    ]

    if event_df.empty:
        lines.append("- No event results produced.")
    else:
        oos = event_df[event_df["sample"] == "OOS"].sort_values("horizon")
        for _, row in oos.iterrows():
            lines.append(
                "- "
                f"{int(row['horizon'])}d: "
                f"N={int(row['n_events'])}, "
                f"Neff={int(row['n_effective'])}, "
                f"mean={row['mean_return']:.4f}, "
                f"hit={row['hit_rate']:.1%}, "
                f"t={row['t_stat']:.2f}, "
                f"p={row['p_value']:.4f}, "
                f"p-FDR={row['p_fdr']:.4f}"
            )

    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
