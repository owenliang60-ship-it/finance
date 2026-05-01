#!/usr/bin/env python3
"""Run broad breadth absolute upcross verification.

This is the no-percentile follow-up run:

  - targets: SPY, QQQ, SOXX
  - thresholds: 20/25/30 and 70/75/80 absolute breadth
  - signal: raw daily breadth fraction, not rolling percentile

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

from backtest.breadth_study.percentile_manifest import (  # noqa: E402
    load_manifest,
    manifest_sha256,
)
from run_breadth_pctile_verification import (  # noqa: E402
    _build_target_returns,
    _git_commit_short,
    _read_target_prices,
    run_pipeline,
)


DEFAULT_MANIFEST = PROJECT_ROOT / "backtest/breadth_study/manifests/breadth_absolute_v1.json"
DEFAULT_MARKET_DB = PROJECT_ROOT / "data/market.db"
DEFAULT_DAILY_BREADTH = PROJECT_ROOT / "data/breadth_study_1b/daily_breadth.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/breadth_study_1b/absolute_verification"
DEFAULT_REPORT = PROJECT_ROOT / "docs/research/2026-05-01-breadth-absolute-upcross-verification.md"


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
    if manifest.get("signal_mode") != "absolute":
        raise SystemExit(
            f"Expected absolute manifest, got signal_mode={manifest.get('signal_mode')!r}"
        )

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

    cli_command = " ".join(["python", "scripts/run_breadth_absolute_verification.py", *(argv or [])])
    paths = run_pipeline(
        manifest=manifest,
        manifest_sha=sha,
        daily_breadth=daily_breadth,
        target_prices_dict=target_prices_dict,
        target_returns=target_returns,
        output_dir=Path(args.output_dir),
        report_path=Path(args.report_path),
        git_commit=_git_commit_short(),
        cli_command=cli_command,
    )
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
