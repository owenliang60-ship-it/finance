from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def seed_pipeline_dbs(
    market_db: Path,
    company_db: Path,
    periods: int = 220,
) -> None:
    _seed_market_db(market_db, periods=periods)
    _seed_company_db(company_db)


def _seed_market_db(path: Path, periods: int = 220) -> None:
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

    dates = pd.bdate_range("2024-01-02", periods=periods)
    symbols = {
        "AAA": {"drift": 0.010, "amp": 0.0015, "period": 11.0, "phase": 0.0},
        "BBB": {"drift": 0.006, "amp": 0.0030, "period": 7.0, "phase": 1.0},
        "CCC": {"drift": -0.003, "amp": 0.0040, "period": 5.0, "phase": 2.0},
        "SPY": {"drift": 0.004, "amp": 0.0010, "period": 13.0, "phase": 0.5},
    }
    base_caps = {
        "AAA": 250_000_000_000.0,
        "BBB": 180_000_000_000.0,
        "CCC": 130_000_000_000.0,
    }

    for symbol, config in symbols.items():
        price = 100.0
        for idx, d in enumerate(dates):
            step = (
                config["drift"]
                + config["amp"] * np.sin(idx / config["period"] + config["phase"])
            )
            price *= np.exp(step)
            open_price = price * (0.995 if idx % 2 == 0 else 1.005)
            cur.execute(
                "INSERT INTO daily_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol,
                    d.strftime("%Y-%m-%d"),
                    float(open_price),
                    float(max(open_price, price)),
                    float(min(open_price, price)),
                    float(price),
                    1_000_000 + idx,
                    0.0,
                    0.0,
                ),
            )

    for symbol, base_cap in base_caps.items():
        for idx, d in enumerate(dates[::5]):
            cur.execute(
                "INSERT INTO historical_market_cap VALUES (?, ?, ?)",
                (
                    symbol,
                    d.strftime("%Y-%m-%d"),
                    float(base_cap * (1 + 0.002 * idx)),
                ),
            )

    social_template = {
        "AAA": [120] + [30 - min(i, 20) for i in range(1, 40)],
        "BBB": [22 + (i % 2) for i in range(40)],
        "CCC": [8 + (i % 3) for i in range(40)],
    }
    social_dates = [d.strftime("%Y-%m-%d") for d in dates[-40:]][::-1]
    for symbol, history in social_template.items():
        for date_str, mentions in zip(social_dates, history):
            cur.execute(
                "INSERT INTO social_sentiment VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, date_str, "reddit", 10.0, mentions, 0.1),
            )

    conn.commit()
    conn.close()


def _seed_company_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
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
    conn.executemany(
        "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("AAA", "AAA Corp", "Technology", "Software", "NASDAQ", 250_000_000_000.0, 1, "test", "2024-01-01", "2024-01-01"),
            ("BBB", "BBB Corp", "Technology", "Semiconductors", "NASDAQ", 180_000_000_000.0, 1, "test", "2024-01-01", "2024-01-01"),
            ("CCC", "CCC Corp", "Industrials", "Machinery", "NYSE", 130_000_000_000.0, 1, "test", "2024-01-01", "2024-01-01"),
        ],
    )
    conn.commit()
    conn.close()
