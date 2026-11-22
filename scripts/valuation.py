#!/usr/bin/env python3
"""Valuation scenario page: market.db (read-only) + one hand-written ledger → one self-contained HTML.

Plans: docs/plans/2026-11-22-valuation-scenario-workflow.md (v1) and
docs/plans/2026-11-22-valuation-dashboard-v2.md (four-panel dashboard). stdout carries one JSON line.
Exit codes: 0 every gate VERIFIED; 10 degraded (non-VERIFIED items are excluded and listed);
20 page blocked (the HTML holds only the banner and the gate report); 1 hard failure (ledger,
database, symbol mismatch or a non-finite payload: no HTML is written, the reason goes to
stderr); 2 invalid arguments (argparse).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from terminal.valuation.config import VERIFIED  # noqa: E402
from terminal.valuation.engine import build_payload  # noqa: E402
from terminal.valuation.facts import load_facts  # noqa: E402
from terminal.valuation.gates import run_gates, summarize  # noqa: E402
from terminal.valuation.ledger import load_ledger  # noqa: E402
from terminal.valuation.render import render_html  # noqa: E402

DEFAULT_OUT_DIR = PROJECT_ROOT / "reports" / "rendered" / "valuation"
EXIT_HARD = 1


def _day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}") from None


def _parse(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the valuation scenario page for one symbol.")
    parser.add_argument("symbol")
    parser.add_argument("--ledger", required=True, type=Path, help="terminal/valuation/ledgers/<SYMBOL>/ledger.toml")
    parser.add_argument("--db", type=Path, default=None, help="market.db path (default: MarketStore default)")
    parser.add_argument("--as-of", type=_day, default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--out", type=Path, default=None, help="HTML path (default: reports/rendered/valuation/)")
    return parser.parse_args(argv)


def main(argv: list) -> int:
    args = _parse(argv)
    symbol = args.symbol.upper()
    as_of = args.as_of or date.today().isoformat()
    out = args.out or DEFAULT_OUT_DIR / f"{symbol}-{as_of}.html"
    try:
        ledger = load_ledger(args.ledger)
        if ledger.symbol != symbol:
            raise ValueError(f"ledger symbol {ledger.symbol} does not match {symbol}")
        store = MarketStore(db_path=args.db, read_only=True)
        try:
            facts = load_facts(store, symbol, as_of)
        finally:
            store.close()
        summary = summarize(run_gates(facts, ledger), facts, ledger)
        page = render_html(build_payload(facts, ledger, summary))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_HARD
    print(json.dumps({
        "symbol": symbol, "as_of": as_of, "overall": summary.overall, "page_blocked": summary.page_blocked,
        "exit_code": summary.exit_code, "html": str(out),
        "non_verified": [{"id": r.id, "status": r.status, "detail": r.detail}
                         for r in summary.results if r.status != VERIFIED],
        "g4_reextract": list(summary.g4_reextract),
    }, ensure_ascii=False))
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
