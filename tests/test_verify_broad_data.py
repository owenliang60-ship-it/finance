"""Tests for scripts.verify_broad_data."""

from datetime import date

from scripts.verify_broad_data import (
    CoverageRow,
    aggregate_report,
    classify_coverage,
    run_sanity_checks,
)


def test_classify_full_coverage():
    status = classify_coverage(
        CoverageRow(row_count=1000, first_date="2021-02-01", last_date="2026-04-01"),
        min_rows=900,
        earliest_first="2021-06-01",
        latest_last="2026-03-01",
        today=date(2026, 4, 23),
    )
    assert status == "full"


def test_classify_partial_when_rows_short():
    status = classify_coverage(
        CoverageRow(row_count=450, first_date="2021-02-01", last_date="2026-04-01"),
        min_rows=900,
        earliest_first="2021-06-01",
        latest_last="2026-03-01",
        today=date(2026, 4, 23),
    )
    assert status == "partial"


def test_classify_missing_when_rows_too_small():
    status = classify_coverage(
        CoverageRow(row_count=100, first_date="2024-01-01", last_date="2024-04-01"),
        min_rows=900,
        earliest_first="2021-06-01",
        latest_last="2026-03-01",
        today=date(2026, 4, 23),
    )
    assert status == "missing"


def test_classify_recent_ipo_dense_history_as_full():
    status = classify_coverage(
        CoverageRow(row_count=120, first_date="2025-12-01", last_date="2026-04-01"),
        min_rows=900,
        earliest_first="2021-06-01",
        latest_last="2026-03-01",
        today=date(2026, 4, 23),
    )
    assert status == "full"


class _SanityStore:
    def __init__(self, sivb_cap=None):
        self.sivb_cap = sivb_cap

    def get_market_cap_at(self, symbol, as_of):
        mapping = {
            ("AAPL", "2021-02-03"): 2.2e12,
            ("MSFT", "2023-03-01"): 1.8e12,
            ("NVDA", "2024-01-02"): 1.2e12,
            ("SIVB", "2023-03-09"): self.sivb_cap,
        }
        return mapping[(symbol, as_of)]

    def get_daily_prices(self, symbol, start_date=None, end_date=None, limit=0):
        if symbol != "AAPL":
            return []
        if start_date == "2026-04-08" and end_date == "2026-04-08":
            return [{"date": "2026-04-08"}]
        if start_date == "2026-04-08" and end_date is None:
            return [{"date": "2026-04-09"}]
        return []


def test_run_sanity_checks_accepts_expected_missing_mcap_and_exact_price_date():
    assert run_sanity_checks(_SanityStore()) == []


def test_run_sanity_checks_flags_unexpected_market_cap_when_expected_missing():
    failures = run_sanity_checks(_SanityStore(sivb_cap=1.0))
    assert any("expected missing market cap" in failure for failure in failures)


def test_aggregate_fail_on_partial_dominance():
    universe = ["A", "B", "C", "D"]
    coverages = {
        "A": CoverageRow(row_count=1000, first_date="2021-02-01", last_date="2026-04-01"),
        "B": CoverageRow(row_count=500, first_date="2021-02-01", last_date="2026-04-01"),
        "C": CoverageRow(row_count=500, first_date="2021-02-01", last_date="2026-04-01"),
        "D": CoverageRow(row_count=1000, first_date="2021-02-01", last_date="2026-04-01"),
    }

    report = aggregate_report(
        universe,
        coverages,
        min_rows=900,
        earliest_first="2021-06-01",
        latest_last="2026-03-01",
        full_threshold=0.40,
        partial_cap=0.25,
        missing_cap=0.25,
        today=date(2026, 4, 23),
        table="historical_market_cap",
    )

    assert report.full_count == 2
    assert report.partial_count == 2
    assert not report.passed
    assert any("partial ratio" in reason for reason in report.failure_reasons)
