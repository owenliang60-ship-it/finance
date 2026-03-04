#!/usr/bin/env python3
"""
Run Codex1.0 autonomous candidate generation + fast-lane evaluation.

Example:
    python3 scripts/run_codex1_fast_mining.py --top-k 20
"""

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.adapters.us_stocks import USStocksAdapter
from factor_research_codex1.evaluation.fast_lane import FastLaneConfig, FastLaneEvaluator
from factor_research_codex1.miner.orchestrator import MiningOrchestrator, MiningRunConfig


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex1.0 Fast Mining")
    parser.add_argument("--start", type=str, default=None, help="start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="end date YYYY-MM-DD")
    parser.add_argument("--freq", type=str, default="W", choices=["D", "W", "M"], help="computation frequency")
    parser.add_argument("--max-grid", type=int, default=120, help="max grid candidates")
    parser.add_argument("--max-random", type=int, default=160, help="max random candidates")
    parser.add_argument("--random-depth", type=int, default=3, help="max random expression depth")
    parser.add_argument("--top-k", type=int, default=20, help="display top-k candidates")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    miner = MiningOrchestrator()
    mine_cfg = MiningRunConfig(
        max_grid_candidates=args.max_grid,
        max_random_candidates=args.max_random,
        random_max_depth=args.random_depth,
    )
    candidates = miner.run(config=mine_cfg)
    print(f"\nGenerated candidates: {len(candidates)}")

    adapter = USStocksAdapter()
    eval_cfg = FastLaneConfig(computation_freq=args.freq)
    evaluator = FastLaneEvaluator(adapter=adapter, config=eval_cfg)
    metrics = evaluator.evaluate_candidates(
        candidates=candidates,
        start_date=args.start,
        end_date=args.end,
    )
    df = evaluator.to_dataframe(metrics)
    if df.empty:
        print("No evaluated candidates.")
        return

    show_cols = [
        "candidate_id",
        "quality_score",
        "mean_ic",
        "mean_ic_ir",
        "mean_spread",
        "stability",
        "expression",
    ]
    top_k = max(1, min(args.top_k, len(df)))
    print(f"\nTop {top_k} candidates:")
    print(df[show_cols].head(top_k).to_string(index=False))


if __name__ == "__main__":
    main()

