#!/usr/bin/env python3
"""Run event-validity statistics for breadth upcross signals.

Unlike the strategy-CAGR verifier, this report does not compare against
100% buy-and-hold. It asks whether event-aligned forward returns are better
than same-sample non-event dates.

NEVER writes to market.db.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backtest.breadth_study.event_validity import run_event_validity  # noqa: E402
from backtest.breadth_study.event_validity_report import (  # noqa: E402
    write_event_validity_report,
)
from backtest.breadth_study.percentile_manifest import (  # noqa: E402
    load_manifest,
    manifest_sha256,
)
from run_breadth_pctile_verification import (  # noqa: E402
    _build_target_returns,
    _git_commit_short,
    _read_target_prices,
)


DEFAULT_MANIFEST = PROJECT_ROOT / "backtest/breadth_study/manifests/breadth_absolute_v1.json"
DEFAULT_MARKET_DB = PROJECT_ROOT / "data/market.db"
DEFAULT_DAILY_BREADTH = PROJECT_ROOT / "data/breadth_study_1b/daily_breadth.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/breadth_study_1b/event_validity"
DEFAULT_REPORT = PROJECT_ROOT / "docs/research/2026-05-01-breadth-event-validity.md"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--market-db", default=str(DEFAULT_MARKET_DB))
    parser.add_argument("--daily-breadth-csv", default=str(DEFAULT_DAILY_BREADTH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    sha = manifest_sha256(manifest_path)

    daily_breadth = pd.read_csv(Path(args.daily_breadth_csv))
    daily_breadth["date"] = pd.to_datetime(daily_breadth["date"])
    for col in (f"breadth_{ma}" for ma in manifest["ma_windows"]):
        if col not in daily_breadth.columns:
            raise SystemExit(
                f"Daily breadth CSV missing column {col!r}; have={list(daily_breadth.columns)}"
            )

    target_prices_dict = _read_target_prices(
        Path(args.market_db), manifest["targets"], manifest["from_date"]
    )
    horizons = sorted(set(manifest["horizons_short"] + manifest["horizons_long"]))
    target_returns = _build_target_returns(target_prices_dict, horizons)

    table, summary = run_event_validity(manifest, daily_breadth, target_returns)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    table_csv = output_dir / "event_validity_table.csv"
    summary_csv = output_dir / "event_validity_summary.csv"
    table.to_csv(table_csv, index=False)
    summary.to_csv(summary_csv, index=False)

    cli_command = " ".join(["python", "scripts/run_breadth_event_validity.py", *(argv or [])])
    report_path = Path(args.report_path)
    write_event_validity_report(
        report_path,
        manifest=manifest,
        manifest_sha256=sha,
        table=table,
        summary=summary,
        git_commit=_git_commit_short(),
        cli_command=cli_command,
    )

    paths = {
        "event_validity_table": table_csv,
        "event_validity_summary": summary_csv,
        "report": report_path,
    }
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
