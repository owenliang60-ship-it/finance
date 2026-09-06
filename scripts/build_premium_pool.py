#!/usr/bin/env python3
"""Build and atomically publish the weekly derived Premium Pool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.broad_market_scan import load_price_frames_from_market_db
from scripts.morning_report import _compute_signal_betas, SELECTION_COMPASS_PRICE_ROWS
from src.data.market_store import MarketStore
from src.data.premium_pool import PREMIUM_POOL_PATH, build_artifact, publish_premium_pool
from src.data.universe_resolver import current_base_universe
from terminal.selection_compass import build_premium_pool


def build_weekly_artifact(store: MarketStore) -> dict:
    symbols = current_base_universe(store)
    as_of_row = store._get_conn().execute("SELECT MAX(date) FROM daily_price").fetchone()
    as_of = as_of_row[0] if as_of_row else None
    if not as_of:
        raise RuntimeError("daily_price has no as-of date")
    frames = load_price_frames_from_market_db(symbols, rows_needed=180)
    missing = sorted(set(symbols) - set(frames))
    if missing:
        frames.update(load_price_frames_from_market_db(
            missing, rows_needed=SELECTION_COMPASS_PRICE_ROWS,
        ))
    betas = _compute_signal_betas(frames, symbols)
    result = build_premium_pool(
        store=store, symbols=symbols, as_of=as_of,
        beta_observations=betas,
    )
    if not result.get("available"):
        raise RuntimeError("Premium Pool unavailable: {}".format(result.get("reason")))
    return build_artifact(result=result, as_of=as_of, universe_symbols=symbols)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build weekly Premium Pool")
    parser.add_argument("--dry-run", action="store_true", help="validate and print; do not publish")
    parser.add_argument("--output", type=Path, default=PREMIUM_POOL_PATH)
    args = parser.parse_args(argv)
    store = MarketStore(read_only=True)
    try:
        artifact = build_weekly_artifact(store)
    finally:
        store.close()
    summary = {
        "name": artifact["name"], "as_of": artifact["as_of"],
        "generated_at": artifact["generated_at"],
        "universe": artifact["universe"]["count"],
        "fundamental_ready": artifact["coverage"]["fundamental_ready"]["covered"],
        "beta_ready": artifact["coverage"]["beta_ready"]["covered"],
        "members": len(artifact["members"]),
        "symbols": [row["symbol"] for row in artifact["members"]],
    }
    if args.dry_run:
        summary["published"] = False
    else:
        path = publish_premium_pool(artifact, args.output)
        summary.update(published=True, path=str(path))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
