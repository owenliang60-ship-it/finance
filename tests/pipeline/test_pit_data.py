import sqlite3
from pathlib import Path

from backtest.pipeline.primitives.pit_data import PitData


def _seed_market_db(path: Path) -> None:
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
        CREATE TABLE social_sentiment (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            buzz_score REAL,
            total_mentions INTEGER,
            sentiment_score REAL
        )
        """
    )

    prices = [
        ("AAA", "2024-01-01", 10, 11, 9, 10.5, 1000, 0, 0),
        ("AAA", "2024-01-02", 10.5, 11.5, 10, 11, 1100, 0, 0),
        ("AAA", "2024-01-03", 11, 12, 10.5, 11.8, 1200, 0, 0),
        ("AAA", "2024-01-04", 11.8, 12.2, 11.6, 12.0, 1400, 0, 0),
        ("SPY", "2024-01-01", 100, 101, 99, 100.5, 10000, 0, 0),
        ("SPY", "2024-01-02", 100.5, 101.5, 100, 101, 10000, 0, 0),
        ("SPY", "2024-01-03", 101, 102, 100.5, 101.6, 10000, 0, 0),
        ("SPY", "2024-01-04", 101.6, 102.2, 101.2, 102, 10000, 0, 0),
    ]
    cur.executemany("INSERT INTO daily_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", prices)

    social_rows = [
        ("AAA", "2024-01-04", "reddit", 10.0, 20, 0.2),
        ("AAA", "2024-01-04", "x", 11.0, 10, 0.1),
        ("AAA", "2024-01-03", "reddit", 8.0, 5, 0.1),
        ("AAA", "2024-01-02", "reddit", 7.0, 3, 0.1),
    ]
    cur.executemany("INSERT INTO social_sentiment VALUES (?, ?, ?, ?, ?, ?)", social_rows)
    conn.commit()
    conn.close()


def test_window_and_as_of_respect_end_date(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db)
    sqlite3.connect(company_db).close()

    pit = PitData(market_db, company_db)
    window = pit.window("AAA", end_date="2024-01-03", lookback_days=2)
    as_of = pit.as_of("AAA", end_date="2024-01-03")

    assert window["date"].tolist() == ["2024-01-02", "2024-01-03"]
    assert as_of["date"].tolist() == ["2024-01-01", "2024-01-02", "2024-01-03"]


def test_social_mentions_history_is_truncated_as_of(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db)
    sqlite3.connect(company_db).close()

    pit = PitData(market_db, company_db)
    history = pit.social_mentions_history_as_of("AAA", end_date="2024-01-03", lookback_days=5)

    assert history == [5, 3]


def test_trading_calendar_returns_sorted_distinct_dates(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db)
    sqlite3.connect(company_db).close()

    pit = PitData(market_db, company_db)
    dates = pit.trading_calendar("2024-01-01", "2024-01-04")
    assert dates == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
