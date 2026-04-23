#!/usr/bin/env python3
"""Verify broad universe historical market cap and daily price coverage."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import BROAD_UNIVERSE_FILE, MARKET_DB_PATH
from src.data.market_store import MarketStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MCAP_MIN_ROWS = 900
PRICE_MIN_ROWS = 1000
PARTIAL_MIN_ROWS = 300
EARLIEST_FIRST = "2021-06-01"
LATEST_LAST = "2026-03-01"
MCAP_FULL_THRESHOLD = 0.92
MCAP_PARTIAL_CAP = 0.05
MCAP_MISSING_CAP = 0.03
PRICE_FULL_THRESHOLD = 0.95
PRICE_PARTIAL_CAP = 0.03
PRICE_MISSING_CAP = 0.02
IPO_GRACE_DAYS = 365
SANITY_TOLERANCE = 0.10
SQLITE_CHUNK = 500

SANITY_CHECKS = [
    ("mcap", "AAPL", "2021-02-03", 2.2e12, SANITY_TOLERANCE),
    ("mcap", "MSFT", "2023-03-01", 1.8e12, SANITY_TOLERANCE),
    ("mcap", "NVDA", "2024-01-02", 1.2e12, SANITY_TOLERANCE),
    ("mcap", "SIVB", "2023-03-09", None, 0.40),
    ("price_exists", "AAPL", "2026-04-08", None, None),
]


@dataclass
class CoverageRow:
    row_count: int = 0
    first_date: str | None = None
    last_date: str | None = None


@dataclass
class AggregateReport:
    table: str
    universe_size: int
    full_count: int = 0
    partial_count: int = 0
    missing_count: int = 0
    full_ratio: float = 0.0
    missing_ratio: float = 0.0
    passed: bool = False
    failure_reasons: List[str] = field(default_factory=list)


def load_broad_symbols() -> List[str]:
    import json

    if not BROAD_UNIVERSE_FILE.exists():
        raise RuntimeError("broad_universe.json missing")
    payload = json.loads(BROAD_UNIVERSE_FILE.read_text())
    return payload.get("symbols", [])


def load_symbol_coverage(table: str, symbols: Iterable[str]) -> Dict[str, CoverageRow]:
    symbols = list(symbols)
    conn = sqlite3.connect(str(MARKET_DB_PATH))
    try:
        if not symbols:
            return {}
        result: Dict[str, CoverageRow] = {}
        for start in range(0, len(symbols), SQLITE_CHUNK):
            chunk = symbols[start : start + SQLITE_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT symbol, COUNT(*) AS row_count, MIN(date) AS first_date, MAX(date) AS last_date
                FROM {table}
                WHERE symbol IN ({placeholders})
                GROUP BY symbol
                """,
                chunk,
            ).fetchall()
            result.update(
                {
                    row[0]: CoverageRow(row_count=row[1], first_date=row[2], last_date=row[3])
                    for row in rows
                }
            )
        return result
    finally:
        conn.close()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def classify_coverage(
    coverage: CoverageRow,
    *,
    min_rows: int,
    earliest_first: str,
    latest_last: str,
    today: date,
) -> str:
    first_date = _parse_date(coverage.first_date)
    last_date = _parse_date(coverage.last_date)
    if first_date is None or last_date is None:
        return "missing"

    earliest = _parse_date(earliest_first)
    latest = _parse_date(latest_last)
    if earliest is None or latest is None:
        raise ValueError("Invalid threshold dates")

    if (
        coverage.row_count >= min_rows
        and first_date <= earliest
        and last_date >= latest
    ):
        return "full"

    # IPO grace: recent listings can count as full if their available history is dense.
    if first_date > earliest and first_date > today - timedelta(days=IPO_GRACE_DAYS):
        expected_rows = max((today - first_date).days * 5 // 7, 1)
        if last_date >= latest and coverage.row_count >= max(int(expected_rows * 0.6), 1):
            return "full"

    if coverage.row_count < PARTIAL_MIN_ROWS:
        return "missing"

    return "partial"


def aggregate_report(
    universe: List[str],
    coverages: Dict[str, CoverageRow],
    *,
    min_rows: int,
    earliest_first: str,
    latest_last: str,
    full_threshold: float,
    partial_cap: float,
    missing_cap: float,
    today: date | None = None,
    table: str,
) -> AggregateReport:
    today = today or date.today()
    report = AggregateReport(table=table, universe_size=len(universe))

    for symbol in universe:
        status = classify_coverage(
            coverages.get(symbol, CoverageRow()),
            min_rows=min_rows,
            earliest_first=earliest_first,
            latest_last=latest_last,
            today=today,
        )
        if status == "full":
            report.full_count += 1
        elif status == "partial":
            report.partial_count += 1
        else:
            report.missing_count += 1

    if report.universe_size:
        report.full_ratio = report.full_count / report.universe_size
        report.missing_ratio = report.missing_count / report.universe_size
    partial_ratio = (
        report.partial_count / report.universe_size if report.universe_size else 0.0
    )

    if report.full_ratio < full_threshold:
        report.failure_reasons.append(
            f"full coverage {report.full_ratio:.1%} < {full_threshold:.1%}"
        )
    if partial_ratio > partial_cap:
        report.failure_reasons.append(
            f"partial ratio {partial_ratio:.1%} > {partial_cap:.1%}"
        )
    if report.missing_ratio > missing_cap:
        report.failure_reasons.append(
            f"missing ratio {report.missing_ratio:.1%} > {missing_cap:.1%}"
        )
    report.passed = not report.failure_reasons
    return report


def run_sanity_checks(store: MarketStore) -> List[str]:
    failures: List[str] = []
    for kind, symbol, as_of, expected, tolerance in SANITY_CHECKS:
        if kind == "mcap":
            actual = store.get_market_cap_at(symbol, as_of)
            if expected is None:
                if actual is not None:
                    failures.append(f"{symbol} expected missing market cap on {as_of}, got {actual}")
                continue
            low = expected * (1 - (tolerance or 0))
            high = expected * (1 + (tolerance or 0))
            if actual is None or not (low <= actual <= high):
                failures.append(
                    f"{symbol} market cap {actual} outside [{low}, {high}] on {as_of}"
                )
        elif kind == "price_exists":
            rows = store.get_daily_prices(symbol, start_date=as_of, end_date=as_of, limit=1)
            if not rows or rows[0]["date"] != as_of:
                failures.append(f"{symbol} missing daily price on {as_of}")
    return failures


def _print_report(report: AggregateReport) -> None:
    partial_ratio = (
        report.partial_count / report.universe_size if report.universe_size else 0.0
    )
    logger.info(
        "%s coverage full=%d (%.1f%%) partial=%d (%.1f%%) missing=%d (%.1f%%)",
        report.table,
        report.full_count,
        report.full_ratio * 100,
        report.partial_count,
        partial_ratio * 100,
        report.missing_count,
        report.missing_ratio * 100,
    )
    for reason in report.failure_reasons:
        logger.error("%s gate failed: %s", report.table, reason)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify broad universe data coverage")
    parser.add_argument("--table", choices=["mcap", "price", "both"], default="both")
    args = parser.parse_args()

    universe = load_broad_symbols()
    reports: List[AggregateReport] = []

    if args.table in {"mcap", "both"}:
        reports.append(
            aggregate_report(
                universe,
                load_symbol_coverage("historical_market_cap", universe),
                min_rows=MCAP_MIN_ROWS,
                earliest_first=EARLIEST_FIRST,
                latest_last=LATEST_LAST,
                full_threshold=MCAP_FULL_THRESHOLD,
                partial_cap=MCAP_PARTIAL_CAP,
                missing_cap=MCAP_MISSING_CAP,
                table="historical_market_cap",
            )
        )
    if args.table in {"price", "both"}:
        reports.append(
            aggregate_report(
                universe,
                load_symbol_coverage("daily_price", universe),
                min_rows=PRICE_MIN_ROWS,
                earliest_first=EARLIEST_FIRST,
                latest_last=LATEST_LAST,
                full_threshold=PRICE_FULL_THRESHOLD,
                partial_cap=PRICE_PARTIAL_CAP,
                missing_cap=PRICE_MISSING_CAP,
                table="daily_price",
            )
        )

    for report in reports:
        _print_report(report)

    sanity_failures = run_sanity_checks(MarketStore(MARKET_DB_PATH))
    for failure in sanity_failures:
        logger.error("sanity failed: %s", failure)

    if any(not report.passed for report in reports) or sanity_failures:
        return 1
    logger.info("Broad universe verify passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
