"""Prosperity D9 history: frozen backfill targets + read-only coverage report.

Subcommands:
    targets  — union of the as-of $10B+ members at every quarter end → JSON,
               the frozen `--targets-file` for the backfill runners.

Read-only: opens market.db with `MarketStore(read_only=True)`; writes only
the output files it is told to.

CLI:
    python scripts/verify_prosperity_history.py targets \\
        [--start 2021-09-30] [--end 2026-06-30] [--out data/prosperity/d9_targets.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_history import members_by_quarter_end, quarter_ends  # noqa: E402

DEFAULT_TARGETS_PATH = PROJECT_ROOT / "data" / "prosperity" / "d9_targets.json"


def _open_store() -> MarketStore:
    return MarketStore(read_only=True)


def _code_sha() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def cmd_targets(args) -> int:
    qes = quarter_ends(args.start, args.end)
    store = _open_store()
    by_qe = members_by_quarter_end(store, qes)
    union = sorted({s for members in by_qe.values() for s in members})
    doc = {
        "symbols": union,
        "quarter_ends": qes,
        "by_quarter_end": by_qe,
        "source": "approximate_members_as_of (10d mcap freshness + symbol_aliases)",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_sha": _code_sha(),
    }
    _write_json(Path(args.out), doc)
    sizes = [len(v) for v in by_qe.values()]
    print("targets: {} symbols over {} quarter ends (per-quarter {}..{}) -> {}".format(
        len(union), len(qes), min(sizes), max(sizes), args.out))
    return 0 if union else 2


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("targets", help="freeze the quarter-end member union")
    t.add_argument("--start", default="2021-09-30")
    t.add_argument("--end", default="2026-06-30")
    t.add_argument("--out", default=str(DEFAULT_TARGETS_PATH))
    t.set_defaults(func=cmd_targets)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
