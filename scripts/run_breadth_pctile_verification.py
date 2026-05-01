#!/usr/bin/env python3
"""Run breadth percentile upcross verification (Task 13).

Loads a frozen manifest, reads daily_breadth from CSV (default: artifact from
the recovery sweep) and target prices from ``market.db``, then runs:

  1. ``run_verification`` → primary / sensitivity / 240-row diagnostic
  2. ``detect_cluster_patterns`` on each surface
  3. ``write_report`` → markdown verdict report

CSV outputs:
  ``param_summary.csv`` (12 rows, primary cell SPY 10d)
  ``param_summary_qqq10d.csv`` (12 rows, sensitivity cell QQQ 10d)
  ``verification_table.csv`` (240 rows)

NEVER writes to market.db.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.breadth_study.percentile_clusters import detect_cluster_patterns
from backtest.breadth_study.percentile_manifest import (
    load_manifest,
    manifest_sha256,
)
from backtest.breadth_study.percentile_report import write_report
from backtest.breadth_study.percentile_verifier import run_verification


DEFAULT_MANIFEST = PROJECT_ROOT / "backtest/breadth_study/manifests/breadth_pctile_v1.json"
DEFAULT_MARKET_DB = PROJECT_ROOT / "data/market.db"
DEFAULT_DAILY_BREADTH = PROJECT_ROOT / "data/breadth_study_1b/daily_breadth.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/breadth_study_1b/percentile_verification"
DEFAULT_REPORT = PROJECT_ROOT / "docs/research/2026-04-29-breadth-pctile-verification.md"


def _read_target_prices(
    market_db: Path, targets: List[str], from_date: str
) -> Dict[str, pd.DataFrame]:
    """Return {symbol: DataFrame[date, open, close]} for each target.

    market.db is authoritative when present. Some ETF benchmarks are not in the
    broad universe store, so the runner falls back to yfinance for missing
    target-only price series without writing anything back to market.db.
    """
    conn = sqlite3.connect(str(market_db))
    try:
        out: Dict[str, pd.DataFrame] = {}
        for sym in targets:
            df = pd.read_sql_query(
                "SELECT date, open, close FROM daily_price "
                "WHERE symbol = ? AND date >= ? ORDER BY date",
                conn, params=(sym, from_date),
            )
            df["date"] = pd.to_datetime(df["date"])
            if df.empty:
                df = _fetch_yfinance_target_prices(sym, from_date)
                time.sleep(1)
            if df.empty:
                raise SystemExit(f"Target price data missing for {sym}")
            out[sym] = df
    finally:
        conn.close()
    return out


def _fetch_yfinance_target_prices(symbol: str, from_date: str) -> pd.DataFrame:
    """Fetch target ETF prices via yfinance fallback."""
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame(columns=["date", "open", "close"])

    try:
        df = yf.Ticker(symbol).history(start=from_date, auto_adjust=False)
    except Exception:
        return pd.DataFrame(columns=["date", "open", "close"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "close"])

    out = df.reset_index()
    date_col = "Date" if "Date" in out.columns else out.columns[0]
    return pd.DataFrame(
        {
            "date": pd.to_datetime(out[date_col]).dt.tz_localize(None),
            "open": pd.to_numeric(out["Open"], errors="coerce"),
            "close": pd.to_numeric(out["Close"], errors="coerce"),
        }
    ).dropna(subset=["date", "open", "close"])


def _build_target_returns(
    target_prices_dict: Dict[str, pd.DataFrame], horizons: List[int]
) -> pd.DataFrame:
    """T+1 open → T+H close forward returns for each (target, horizon)."""
    # Align on the first target's date axis (intersection)
    first = next(iter(target_prices_dict.values()))
    out = pd.DataFrame({"date": pd.to_datetime(first["date"]).reset_index(drop=True)})
    for sym, df in target_prices_dict.items():
        df = df.sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"])
        merged = out.merge(df[["date", "open", "close"]], on="date", how="left")
        opens = merged["open"]
        closes = merged["close"]
        entry = opens.shift(-1)
        for h in horizons:
            shifted = closes.shift(-h)
            out[f"{sym}_fwd_{h}d"] = (shifted / entry - 1.0).to_numpy()
    return out


def _git_commit_short() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, text=True,
        ).strip()
    except Exception:  # pragma: no cover
        return "unknown"


def run_pipeline(
    *,
    manifest: Dict[str, Any],
    manifest_sha: str,
    daily_breadth: pd.DataFrame,
    target_prices_dict: Dict[str, pd.DataFrame],
    target_returns: pd.DataFrame,
    output_dir: Path,
    report_path: Path,
    git_commit: str = "unknown",
    cli_command: str = "",
) -> Dict[str, Path]:
    """Pure pipeline used by both CLI and integration test."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    primary, sensitivity, table = run_verification(
        manifest, daily_breadth, target_prices_dict, target_returns,
    )
    primary_csv = output_dir / "param_summary.csv"
    sensitivity_csv = output_dir / "param_summary_qqq10d.csv"
    table_csv = output_dir / "verification_table.csv"
    primary.to_csv(primary_csv, index=False)
    sensitivity.to_csv(sensitivity_csv, index=False)
    table.to_csv(table_csv, index=False)

    write_report(
        report_path,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        primary_summary=primary,
        sensitivity_summary=sensitivity,
        verification_table=table,
        git_commit=git_commit,
        cli_command=cli_command,
    )
    return {
        "param_summary": primary_csv,
        "param_summary_qqq10d": sensitivity_csv,
        "verification_table": table_csv,
        "report": report_path,
    }


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

    daily_breadth_csv = Path(args.daily_breadth_csv)
    daily_breadth = pd.read_csv(daily_breadth_csv)
    daily_breadth["date"] = pd.to_datetime(daily_breadth["date"])
    # Validate required breadth columns are present
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

    cli_command = " ".join(["python", "scripts/run_breadth_pctile_verification.py", *(argv or [])])
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
