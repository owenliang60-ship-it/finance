#!/usr/bin/env python3
"""Run the broad breadth QQQ/SOXX research study."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.breadth_study import StudyConfig, run_study


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "market.db",
        help="Read-only market.db path.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "breadth_study",
    )
    parser.add_argument(
        "--overlay-json",
        type=Path,
        default=PROJECT_ROOT / "data" / "pool" / "delisted_large_caps.json",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=PROJECT_ROOT / "docs" / "research" / "2026-04-28-broad-breadth-qqq-soxx-study.md",
    )
    parser.add_argument("--from-date", default="2021-02-01")
    parser.add_argument("--to-date")
    parser.add_argument("--oos-start", default="2025-01-01")
    parser.add_argument("--min-market-cap", type=float, default=10_000_000_000.0)
    parser.add_argument("--max-staleness-days", type=int, default=90)
    parser.add_argument("--cooldown-days", type=int, default=20)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--bootstrap-block-days", type=int, default=20)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument(
        "--refresh-sidecar",
        action="store_true",
        help="Fetch delisted overlay prices/caps into sidecar parquet before running.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = StudyConfig(
        market_db=args.market_db,
        output_dir=args.output_dir,
        report_path=args.report_path,
        overlay_json=args.overlay_json,
        from_date=args.from_date,
        to_date=args.to_date,
        min_market_cap=args.min_market_cap,
        max_staleness_days=args.max_staleness_days,
        oos_start=args.oos_start,
        cooldown_days=args.cooldown_days,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_block_days=args.bootstrap_block_days,
        random_seed=args.random_seed,
        refresh_sidecar=args.refresh_sidecar,
    )
    paths = run_study(config)
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
