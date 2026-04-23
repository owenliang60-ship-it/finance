"""Tests for scripts.verify_broad_data."""

from datetime import date

from scripts.verify_broad_data import (
    AggregateReport,
    CoverageRow,
    aggregate_report,
    classify_coverage,
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
