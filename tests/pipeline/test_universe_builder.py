import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from backtest.pipeline.primitives.universe_builder import UniverseBuildError, UniverseBuilder


def _seed_company_db(path: Path, symbols: list[str], sector: str = "Technology") -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE companies (
            symbol TEXT PRIMARY KEY,
            company_name TEXT,
            sector TEXT,
            industry TEXT,
            exchange TEXT,
            market_cap REAL,
            in_pool INTEGER,
            source TEXT,
            first_seen TEXT,
            updated_at TEXT
        )
        """
    )
    cur.executemany(
        "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (sym, sym, sector, "Software", "NASDAQ", 1e11, 1, "manual", "2024-01-01", "2024-01-01")
            for sym in symbols
        ],
    )
    conn.commit()
    conn.close()


def _seed_market_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE daily_price (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            change REAL, change_pct REAL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE historical_market_cap (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            market_cap REAL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    cur.executemany("INSERT INTO daily_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _insert_mcaps(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executemany("INSERT INTO historical_market_cap VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _make_price_rows(symbols: list[str], dates: list[str]) -> list[tuple]:
    rows = []
    for sym in symbols:
        for idx, d in enumerate(dates):
            price = 100 + idx
            rows.append((sym, d, price, price, price, price, 1000, 0, 0))
    return rows


def _bdates(start: str, periods: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=periods)]


def test_reconstitutes_pit_universe_happy_path(tmp_path):
    symbols = ["AAA", "BBB", "CCC"]
    dates = _bdates("2024-01-01", 20)
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db, _make_price_rows(symbols, dates))
    _seed_company_db(company_db, symbols)
    _insert_mcaps(
        market_db,
        [(sym, dates[0], 20_000_000_000) for sym in symbols],
    )

    builder = UniverseBuilder(market_db, company_db)
    result = builder.build(
        start_date="2024-01-01",
        end_date=dates[-1],
        rebalance="weekly",
        market_cap_min_usd=10_000_000_000,
        exclude_sectors=[],
        min_names=2,
    )

    assert result.effective_start == dates[0]
    assert result.rebalance_dates[0] == dates[0]
    assert set(result.universe_df["symbol"]) == set(symbols)


def test_head_missing_shifts_effective_start(tmp_path):
    symbols = ["AAA", "BBB", "CCC"]
    dates = _bdates("2024-01-01", 20)
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db, _make_price_rows(symbols, dates))
    _seed_company_db(company_db, symbols)
    _insert_mcaps(
        market_db,
        [(sym, dates[10], 20_000_000_000) for sym in symbols],
    )

    builder = UniverseBuilder(market_db, company_db)
    result = builder.build(
        start_date="2024-01-01",
        end_date=dates[-1],
        rebalance="weekly",
        market_cap_min_usd=10_000_000_000,
        exclude_sectors=[],
        min_names=2,
    )

    assert result.effective_start == dates[10]
    assert any("moving effective_start forward" in msg for msg in result.warnings)


def test_middle_coverage_break_aborts(tmp_path):
    symbols = ["AAA", "BBB", "CCC"]
    dates = _bdates("2024-01-01", 20)
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db, _make_price_rows(symbols, dates))
    _seed_company_db(company_db, symbols)
    _insert_mcaps(
        market_db,
        [(sym, dates[0], 20_000_000_000) for sym in symbols],
    )
    # Remove all prior data by starting price dates later than mcap coverage gap in second half.
    conn = sqlite3.connect(market_db)
    conn.execute(f"DELETE FROM historical_market_cap WHERE date <= '{dates[0]}'")
    conn.executemany(
        "INSERT INTO historical_market_cap VALUES (?, ?, ?)",
        [(sym, dates[-1], 20_000_000_000) for sym in symbols],
    )
    conn.commit()
    conn.close()

    builder = UniverseBuilder(market_db, company_db)
    with pytest.raises(UniverseBuildError):
        builder.build(
            start_date="2024-01-01",
            end_date=dates[-1],
            rebalance="weekly",
            market_cap_min_usd=10_000_000_000,
            exclude_sectors=[],
            min_names=2,
        )


def test_min_names_skips_single_rebalance_with_warning(tmp_path):
    symbols = ["AAA", "BBB", "CCC"]
    dates = _bdates("2024-01-01", 60)
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db, _make_price_rows(symbols, dates))
    _seed_company_db(company_db, symbols)
    _insert_mcaps(
        market_db,
        [
            ("AAA", dates[0], 20_000_000_000),
            ("BBB", dates[0], 20_000_000_000),
            ("CCC", dates[0], 20_000_000_000),
            ("AAA", dates[10], 20_000_000_000),
            ("BBB", dates[10], 1_000_000_000),
            ("CCC", dates[10], 1_000_000_000),
            ("BBB", dates[15], 20_000_000_000),
            ("CCC", dates[15], 20_000_000_000),
        ],
    )

    builder = UniverseBuilder(market_db, company_db)
    result = builder.build(
        start_date="2024-01-01",
        end_date=dates[-1],
        rebalance="weekly",
        market_cap_min_usd=10_000_000_000,
        exclude_sectors=[],
        min_names=2,
    )

    assert any("skipped rebalance" in msg for msg in result.warnings)
    assert dates[10] not in result.rebalance_dates
    assert dates[15] in result.rebalance_dates


def test_skip_ratio_aborts_when_over_ten_percent(tmp_path):
    symbols = ["AAA", "BBB", "CCC"]
    dates = _bdates("2024-01-01", 20)
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db, _make_price_rows(symbols, dates))
    _seed_company_db(company_db, symbols)
    _insert_mcaps(
        market_db,
        [("AAA", dates[0], 20_000_000_000)],
    )

    builder = UniverseBuilder(market_db, company_db)
    with pytest.raises(UniverseBuildError):
        builder.build(
            start_date="2024-01-01",
            end_date=dates[-1],
            rebalance="weekly",
            market_cap_min_usd=10_000_000_000,
            exclude_sectors=[],
            min_names=2,
        )
