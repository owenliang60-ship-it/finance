#!/usr/bin/env python3
"""M7 S1: compare our shared statement factors with the original site's boards (read-only on market.db).

Writes the per-comparison detail, which carries the site's private values, only to
--private-dir (refused unless git ignores it), and the value-free summary.json and
summary.md to --report-dir. Plan: docs/plans/2026-09-30-prosperity-m7-s1-comparison.md.
Exit codes: 0 gate passed; 3 per-row errors or a board whose rows do not reconcile;
4 invalid arguments (private dir git would track, missing fixture, bad date); 5 gate failed.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, fields
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.research.prosperity_s1 import (GATE_SINCE, Comparison, SiteRow, compare,  # noqa: E402
                                             load_site_rows, match_index, our_side, summarize,
                                             summary_markdown, timing_status)
from src.data.market_store import MarketStore  # noqa: E402
from terminal.prosperity.config import STRICT_STATEMENTS_FROM  # noqa: E402
from terminal.prosperity.loader import load_history  # noqa: E402
from terminal.prosperity.packet import build_packet  # noqa: E402
from terminal.prosperity.types import InputPacket, QuarterInputs, SymbolHistory  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402

# Our quarter not yet visible on the board date: replay again up to this many days later (plan D-2 A)
REBUILD_DAYS = 120


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--fixture", default=str(PROJECT_ROOT / "data" / "external" / "foresight_fm" / "prosperity.json"))
    p.add_argument("--db", default=str(PROJECT_ROOT / "data" / "market.db"))
    p.add_argument("--private-dir", required=True, help="gitignored directory for rows.csv (site values)")
    p.add_argument("--report-dir", required=True, help="directory for the value-free summary")
    p.add_argument("--since", default=GATE_SINCE, help="first board date counted by the 95%% gate")
    return p.parse_args(argv)


def private_dir_problem(path: Path) -> Optional[str]:
    """Why <path>/rows.csv is unsafe for the site's values, or None when git ignores it or no repository owns it.

    Asks the repository that owns the target, not this checkout: from a worktree, the main
    checkout's reports/ lies outside PROJECT_ROOT yet is tracked there. Fails closed.
    """
    target = (Path(path) / "rows.csv").resolve()
    anchor = target.parent
    while not anchor.exists():
        anchor = anchor.parent
    unsure = f"cannot confirm that git ignores {target}"
    try:
        inside = subprocess.run(["git", "-C", str(anchor), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
        if inside.returncode != 0:
            return None if "not a git repository" in inside.stderr else f"{unsure}: {inside.stderr.strip()}"
        if inside.stdout.strip() != "true":
            return unsure
        ignored = subprocess.run(["git", "-C", str(anchor), "check-ignore", "-q", str(target)],
                                 capture_output=True, text=True)
    except OSError as exc:
        return f"{unsure}: {exc}"
    if ignored.returncode == 0:
        return None
    if ignored.returncode == 1:
        return f"{target} would be tracked by git; use a gitignored path such as data/external/..."
    return f"{unsure}: {ignored.stderr.strip()}"


def _packet(history: SymbolHistory, as_of: str) -> InputPacket:
    return build_packet(history, as_of, mode="replay", membership_basis="s1_fixture", benchmark_closes=(),
                        with_beta=False)


def _aligned(history: SymbolHistory, row: SiteRow, packet: InputPacket) -> Tuple[Sequence[QuarterInputs], str]:
    """Our quarters ending at the site's latest_q, and how they were found (D-2 A)."""
    idx = match_index(packet.quarters, row.latest_q)
    if idx is not None:
        return packet.quarters[:idx + 1], "asof" if idx == len(packet.quarters) - 1 else "trimmed"
    # Stay on the approximate replay: from STRICT_STATEMENTS_FROM on, statements come from the vintage
    last = date.fromisoformat(STRICT_STATEMENTS_FROM) - timedelta(days=1)
    later = min(date.fromisoformat(row.board) + timedelta(days=REBUILD_DAYS), last).isoformat()
    if later <= row.board:
        return (), "none"
    rebuilt = _packet(history, later)
    idx = match_index(rebuilt.quarters, row.latest_q)
    return (rebuilt.quarters[:idx + 1], "rebuilt") if idx is not None else ((), "none")


def main(argv: Optional[List[str]] = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv)
    try:
        if date.fromisoformat(args.since).isoformat() != args.since:
            raise ValueError(f"--since must be YYYY-MM-DD, got {args.since}")
        problem = private_dir_problem(Path(args.private_dir))
        if problem:
            raise ValueError(problem)
        if not Path(args.fixture).is_file():
            raise ValueError(f"fixture not found: {args.fixture}")
    except ValueError as exc:
        print(f"invalid arguments: {exc}", file=sys.stderr)
        return 4
    data = json.loads(Path(args.fixture).read_text())
    rows = load_site_rows(data)
    store = MarketStore(db_path=Path(args.db), read_only=True)
    histories = {}
    comparisons, excluded, errors, timing = [], [], [], []
    for row in rows:
        if row.skip_reason:
            excluded.append((row.board, row.site_symbol, row.skip_reason))
            continue
        try:
            if row.symbol not in histories:
                histories[row.symbol] = load_history(store, row.symbol, with_vintage=False)
            history = histories[row.symbol]
            if not history.income:
                excluded.append((row.board, row.site_symbol, "not_in_our_data"))
                continue
            packet = _packet(history, row.board)
            status = timing_status(packet.current_fiscal, row.latest_q)
            quarters, aligned = _aligned(history, row, packet)
            ours = our_side(quarters, aligned) if quarters else None
            found = compare(row, ours, missing_reason="" if ours else "quarter_not_found")
        except Exception as exc:   # isolate one bad row; surfaced in the summary and exit 3
            errors.append((row.board, row.site_symbol, f"{type(exc).__name__}: {exc}"))
            continue
        comparisons.extend(found)
        timing.append((row.board, row.symbol, status, aligned))
    summary = summarize(comparisons, site_rows=Counter(r.board for r in rows), excluded=excluded, errors=errors,
                        timing=timing, since=args.since)
    summary.update(fixture_asof=data.get("asof"), boards_in_fixture=len(data["boards"]), code_version=code_version(),
                   seconds=round(time.perf_counter() - started, 1))
    private = Path(args.private_dir)
    private.mkdir(parents=True, exist_ok=True)
    with (private / "rows.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(Comparison)])
        writer.writeheader()
        writer.writerows(asdict(c) for c in comparisons)
    report = Path(args.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    (report / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    (report / "summary.md").write_text(summary_markdown(summary))
    g = summary["gate"]
    print(f"S1 gate {'PASS' if g['pass'] else 'FAIL'}: {g['passed']}/{g['counted']} since {g['since']}; "
          f"reconciled={summary['reconciled']} errors={len(errors)} -> {report}")
    if errors or not summary["reconciled"]:
        return 3
    return 0 if g["pass"] else 5


if __name__ == "__main__":
    sys.exit(main())
