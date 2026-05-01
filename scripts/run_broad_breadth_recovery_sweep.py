#!/usr/bin/env python3
"""Run exploratory broad breadth recovery parameter sweep."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.breadth_study.recovery_sweep import RecoverySweepConfig, run_recovery_sweep


def _parse_ints(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _parse_thresholds(value: str) -> list[float]:
    raw = [float(item.strip()) for item in value.split(",") if item.strip()]
    return [item / 100 if item > 1 else item for item in raw]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-db", type=Path, default=PROJECT_ROOT / "data" / "market.db")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "breadth_study")
    parser.add_argument(
        "--overlay-json",
        type=Path,
        default=PROJECT_ROOT / "data" / "pool" / "delisted_large_caps.json",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=PROJECT_ROOT
        / "docs"
        / "research"
        / "2026-04-29-broad-breadth-recovery-sweep.md",
    )
    parser.add_argument("--from-date", default="2021-02-01")
    parser.add_argument("--to-date")
    parser.add_argument("--oos-start", default="2025-01-01")
    parser.add_argument("--min-market-cap", type=float, default=10_000_000_000.0)
    parser.add_argument("--max-staleness-days", type=int, default=90)
    parser.add_argument("--cooldown-days", type=int, default=20)
    parser.add_argument("--ma-windows", type=_parse_ints, default=[10, 20, 30, 40, 50, 60, 80, 100])
    parser.add_argument(
        "--trigger-thresholds",
        type=_parse_thresholds,
        default=[0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
    )
    parser.add_argument("--low-thresholds", type=_parse_thresholds, default=[0.15, 0.20, 0.25, 0.30, 0.35])
    parser.add_argument("--bootstrap-samples", type=int, default=500)
    parser.add_argument("--random-seed", type=int, default=20260429)
    parser.add_argument("--refresh-sidecar", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = RecoverySweepConfig(
        market_db=args.market_db,
        output_dir=args.output_dir,
        overlay_json=args.overlay_json,
        report_path=args.report_path,
        from_date=args.from_date,
        to_date=args.to_date,
        min_market_cap=args.min_market_cap,
        max_staleness_days=args.max_staleness_days,
        oos_start=args.oos_start,
        cooldown_days=args.cooldown_days,
        ma_windows=args.ma_windows,
        trigger_thresholds=args.trigger_thresholds,
        low_thresholds=args.low_thresholds,
        bootstrap_samples=args.bootstrap_samples,
        random_seed=args.random_seed,
        refresh_sidecar=args.refresh_sidecar,
    )
    paths = run_recovery_sweep(config)
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
