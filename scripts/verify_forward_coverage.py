"""Verify forward_estimates coverage in market.db (with optional date window).

Usage:
    python scripts/verify_forward_coverage.py --scope all --min-date 2026-05-09
    python scripts/verify_forward_coverage.py --scope core --min-core-pct 99

Exit 0 if all checked scopes meet thresholds, 1 otherwise.
--min-date filters out rows with date < min_date so stale rows don't mask
the verification of the most recent cron run.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import MARKET_DB_PATH as MARKET_DB  # noqa: E402
from src.data.pool_manager import get_symbols as get_pool_symbols  # noqa: E402
from src.data.extended_universe_manager import get_extended_only_symbols  # noqa: E402


def _covered_symbols(db_path, min_date) -> set:
    con = sqlite3.connect(db_path)
    try:
        if min_date:
            rows = con.execute(
                "SELECT DISTINCT symbol FROM forward_estimates WHERE date >= ?",
                (min_date,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT DISTINCT symbol FROM forward_estimates"
            ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def _bucket_report(name: str, expected: list, covered: set, min_pct: float) -> dict:
    expected_set = set(expected)
    hit = expected_set & covered
    miss = sorted(expected_set - covered)
    pct = (len(hit) / len(expected_set) * 100) if expected_set else 100.0
    return {
        "name": name,
        "expected": len(expected_set),
        "covered": len(hit),
        "pct": round(pct, 2),
        "min_pct": min_pct,
        "ok": pct >= min_pct,
        "missing": miss,
    }


def run(scope: str, min_core_pct: float, min_extended_pct: float,
        min_date) -> tuple:
    """Run verification. Returns (exit_code, report)."""
    covered = _covered_symbols(MARKET_DB, min_date)
    report = {}
    if scope in ("core", "all"):
        report["core"] = _bucket_report("core", get_pool_symbols(), covered, min_core_pct)
    if scope in ("extended", "all"):
        report["extended"] = _bucket_report(
            "extended", get_extended_only_symbols(), covered, min_extended_pct
        )
    rc = 0 if all(b["ok"] for b in report.values()) else 1
    return rc, report


def _print_report(report: dict, min_date) -> None:
    if min_date:
        print(f"Coverage filter: date >= {min_date}")
    for name, b in report.items():
        marker = "OK" if b["ok"] else "FAIL"
        print(f"[{marker}] {name}: {b['covered']}/{b['expected']} "
              f"({b['pct']}%, threshold {b['min_pct']}%)")
        if b["missing"]:
            print(f"   missing (top 20): {b['missing'][:20]}")
            if len(b["missing"]) > 20:
                print(f"   ... and {len(b['missing']) - 20} more")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["core", "extended", "all"], default="all")
    parser.add_argument("--min-core-pct", type=float, default=99.0)
    parser.add_argument("--min-extended-pct", type=float, default=95.0)
    parser.add_argument(
        "--min-date",
        default=None,
        help="ISO date (YYYY-MM-DD); only rows with date>=this count as covered. "
             "Without it, all-time data counts (旧数据可能误判为通过)。",
    )
    args = parser.parse_args()
    rc, report = run(args.scope, args.min_core_pct, args.min_extended_pct, args.min_date)
    _print_report(report, args.min_date)
    sys.exit(rc)


if __name__ == "__main__":
    main()
