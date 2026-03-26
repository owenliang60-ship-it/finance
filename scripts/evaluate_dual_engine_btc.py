"""
Evaluate the latest BTC dual-engine target position and persist state.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the BTC dual-engine timing system.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--risk-mode",
        choices=["surf", "balanced", "fortress"],
        default="balanced",
        help="Risk mode to embed into state before evaluation.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    from backtest.adapters.crypto import CryptoAdapter
    from src.timing.dual_engine import DualEngineConfig, evaluate_dual_engine
    from src.timing.state_store import DualEngineStateStore

    daily_adapter = CryptoAdapter(symbols=[args.symbol], interval="1d")
    intraday_adapter = CryptoAdapter(symbols=[args.symbol], interval="4h")

    daily_data = daily_adapter.load_all().get(args.symbol)
    intraday_data = intraday_adapter.load_all().get(args.symbol)
    if daily_data is None or intraday_data is None:
        raise SystemExit(f"Missing cache data for {args.symbol}; fetch 1d and 4h klines first.")

    store = DualEngineStateStore()
    state = store.load(system_name=f"{args.symbol.lower()}_dual_engine")
    state.risk_mode = args.risk_mode

    result = evaluate_dual_engine(
        intraday_data,
        daily_data,
        state=state,
        config=DualEngineConfig(risk_mode=args.risk_mode),
    )
    store.save(result.state, system_name=f"{args.symbol.lower()}_dual_engine")
    store.save_evaluation(result, system_name=f"{args.symbol.lower()}_dual_engine")

    payload = {
        "symbol": args.symbol,
        "timestamp": result.timestamp,
        "target_position_pct": result.target_position_pct,
        "right_raw_position_pct": result.right_raw_position_pct,
        "right_risked_position_pct": result.right_risked_position_pct,
        "left_position_pct": result.left_position_pct,
        "k": result.k,
        "reasons": result.reasons,
        "state": asdict(result.state),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
