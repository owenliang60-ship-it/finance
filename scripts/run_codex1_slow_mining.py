#!/usr/bin/env python3
"""
Run Codex1.0 mining pipeline:
1) candidate generation
2) fast-lane ranking
3) slow-lane strategy validation vs SPY/QQQ/VOO
"""

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.adapters.us_stocks import USStocksAdapter
from factor_research_codex1.evaluation.fast_lane import FastLaneConfig, FastLaneEvaluator
from factor_research_codex1.evaluation.slow_lane import SlowLaneConfig, SlowLaneEvaluator
from factor_research_codex1.miner.orchestrator import MiningOrchestrator, MiningRunConfig
from factor_research_codex1.registry import CandidateRegistry, RegistryRules
from factor_research_codex1.reporting import MiningOutputStore


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex1.0 Slow Mining")
    parser.add_argument("--start", type=str, default=None, help="start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="end date YYYY-MM-DD")
    parser.add_argument("--fast-freq", type=str, default="W", choices=["D", "W", "M"])
    parser.add_argument("--slow-freq", type=str, default="W", choices=["D", "W", "M"])
    parser.add_argument("--max-grid", type=int, default=160)
    parser.add_argument("--max-random", type=int, default=220)
    parser.add_argument("--random-depth", type=int, default=3)
    parser.add_argument("--top-fast", type=int, default=25, help="top fast-lane candidates for slow lane")
    parser.add_argument("--top-show", type=int, default=15, help="top rows to print")
    parser.add_argument("--top-n", type=int, default=20, help="slow-lane strategy top-n holdings")
    parser.add_argument("--weighting", type=str, default="equal", choices=["equal", "score_weighted"])
    parser.add_argument("--cost-bps", type=float, default=5.0)
    parser.add_argument("--registry-path", type=str, default="", help="optional registry json path")
    parser.add_argument("--approve-excess-cagr", type=float, default=0.03)
    parser.add_argument("--approve-ir", type=float, default=0.4)
    parser.add_argument("--approve-max-dd", type=float, default=0.35)
    parser.add_argument("--watchlist-excess-cagr", type=float, default=0.0)
    parser.add_argument("--watchlist-ir", type=float, default=0.1)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    adapter = USStocksAdapter()

    # Step 1: candidate generation
    miner = MiningOrchestrator()
    mine_cfg = MiningRunConfig(
        max_grid_candidates=args.max_grid,
        max_random_candidates=args.max_random,
        random_max_depth=args.random_depth,
    )
    candidates = miner.run(config=mine_cfg)
    print(f"\nGenerated candidates: {len(candidates)}")
    if not candidates:
        print("No candidates generated.")
        return

    # Step 2: fast lane
    fast_eval = FastLaneEvaluator(
        adapter=adapter,
        config=FastLaneConfig(computation_freq=args.fast_freq),
    )
    fast_metrics = fast_eval.evaluate_candidates(
        candidates=candidates,
        start_date=args.start,
        end_date=args.end,
    )
    fast_df = fast_eval.to_dataframe(fast_metrics)
    if fast_df.empty:
        print("No fast-lane metrics.")
        return

    top_fast = max(1, min(args.top_fast, len(fast_metrics)))
    fast_shortlist = fast_metrics[:top_fast]
    shortlist_ids = {m.candidate_id for m in fast_shortlist}
    shortlist_candidates = [c for c in candidates if c.candidate_id in shortlist_ids]
    print(f"Fast-lane shortlisted: {len(shortlist_candidates)}")

    # Step 3: slow lane
    slow_eval = SlowLaneEvaluator(
        adapter=adapter,
        config=SlowLaneConfig(
            top_n=args.top_n,
            rebalance_freq=args.slow_freq,
            weighting=args.weighting,
            transaction_cost_bps=args.cost_bps,
        ),
    )
    slow_metrics = slow_eval.evaluate_candidates(
        candidates=shortlist_candidates,
        start_date=args.start,
        end_date=args.end,
    )
    slow_df = slow_eval.to_dataframe(slow_metrics)
    if slow_df.empty:
        print("No slow-lane metrics.")
        return

    cols = [
        "candidate_id",
        "quality_score",
        "strategy_sharpe",
        "strategy_cagr",
        "strategy_max_drawdown",
        "avg_excess_cagr",
        "avg_information_ratio",
        "expression",
    ]
    top_show = max(1, min(args.top_show, len(slow_df)))
    print(f"\nTop {top_show} slow-lane candidates:")
    print(slow_df[cols].head(top_show).to_string(index=False))

    # Step 4: persist run artifacts
    store = MiningOutputStore()
    artifacts = store.save_run(
        fast_df=fast_df,
        slow_df=slow_df,
        slow_metrics=slow_metrics,
        metadata={
            "start": args.start,
            "end": args.end,
            "fast_freq": args.fast_freq,
            "slow_freq": args.slow_freq,
            "max_grid": args.max_grid,
            "max_random": args.max_random,
            "random_depth": args.random_depth,
            "top_fast": args.top_fast,
            "top_n": args.top_n,
            "weighting": args.weighting,
            "cost_bps": args.cost_bps,
        },
        top_n_json=max(10, args.top_show),
    )

    # Step 5: update candidate registry
    registry_path = Path(args.registry_path) if args.registry_path else None
    registry = CandidateRegistry(path=registry_path)
    rules = RegistryRules(
        min_approved_excess_cagr=args.approve_excess_cagr,
        min_approved_ir=args.approve_ir,
        max_approved_drawdown=args.approve_max_dd,
        min_watchlist_excess_cagr=args.watchlist_excess_cagr,
        min_watchlist_ir=args.watchlist_ir,
    )
    registry.upsert_from_slow_metrics(slow_metrics, rules=rules)
    reg_path = registry.save()
    counts = registry.counts()

    print("\nArtifacts:")
    print(f"  run_id: {artifacts.run_id}")
    print(f"  run_dir: {artifacts.run_dir}")
    print(f"  fast_csv: {artifacts.fast_csv}")
    print(f"  slow_csv: {artifacts.slow_csv}")
    print(f"  metadata: {artifacts.metadata_json}")
    print(f"  top_json: {artifacts.top_json}")
    print("Registry:")
    print(f"  path: {reg_path}")
    print(f"  approved={counts.get('approved', 0)} "
          f"watchlist={counts.get('watchlist', 0)} "
          f"rejected={counts.get('rejected', 0)}")


if __name__ == "__main__":
    main()
