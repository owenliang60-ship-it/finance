import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.primitives.signal_engine import SignalEngine
from backtest.pipeline.spec import ComboSpec, FactorInput


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

    dates = pd.bdate_range("2024-01-01", periods=90)
    symbols = {
        "AAA": 0.012,
        "BBB": 0.005,
        "CCC": -0.004,
    }
    for symbol, drift in symbols.items():
        price = 100.0
        for idx, d in enumerate(dates):
            price *= np.exp(drift)
            open_price = price * 0.99
            cur.execute(
                "INSERT INTO daily_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, d.strftime("%Y-%m-%d"), open_price, price, open_price, price, 1000 + idx, 0.0, 0.0),
            )

    social_template = {
        "AAA": [60, 25, 22, 20, 19, 18, 17, 16, 15, 14],
        "BBB": [20, 20, 20, 20, 20, 20, 20, 20, 20, 20],
        "CCC": [5, 6, 5, 5, 6, 5, 6, 5, 5, 6],
    }
    social_dates = [d.strftime("%Y-%m-%d") for d in dates[-10:]][::-1]
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
    conn.commit()
    conn.close()


def _make_universe_df(date_str: str = "2024-05-03") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": date_str, "symbol": "AAA"},
            {"date": date_str, "symbol": "BBB"},
            {"date": date_str, "symbol": "CCC"},
        ]
    )


def test_rank_pct_and_zscore_are_cross_sectional_per_date(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db)
    _seed_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = _make_universe_df()

    factor = FactorInput(name="RS_Rating_B", params={}, transform="rank_pct")
    result = engine.compute([factor], ComboSpec(method="single"), universe_df)
    values = result.factor_frames["RS_Rating_B"].loc["2024-05-03"].dropna().sort_values()
    assert values.tolist() == sorted(values.tolist())
    assert values.min() > 0
    assert values.max() <= 1

    z_factor = replace(factor, transform="zscore")
    z_result = engine.compute([z_factor], ComboSpec(method="single"), universe_df)
    z_values = z_result.factor_frames["RS_Rating_B"].loc["2024-05-03"].dropna()
    assert abs(float(z_values.mean())) < 1e-9


def test_weighted_sum_and_rank_average_combo(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db)
    _seed_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = _make_universe_df()

    factors = [
        FactorInput(name="RS_Rating_B", params={}, transform="rank_pct", weight=0.7),
        FactorInput(name="PMARP", params={}, transform="rank_pct", weight=0.3),
    ]
    weighted = engine.compute(factors, ComboSpec(method="weighted_sum"), universe_df)
    rank_avg = engine.compute(factors, ComboSpec(method="rank_average"), universe_df)

    assert not weighted.combo_frame.empty
    assert not rank_avg.combo_frame.empty
    assert set(weighted.combo_frame.columns) == {"AAA", "BBB", "CCC"}
    assert set(rank_avg.combo_frame.columns) == {"AAA", "BBB", "CCC"}


def test_builtin_factors_produce_scores(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_market_db(market_db)
    _seed_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = _make_universe_df()

    factors = [
        FactorInput(name="RS_Rating_B", params={}, transform="raw"),
        FactorInput(
            name="PMARP",
            params={"ema_period": 5, "lookback": 20},
            transform="raw",
        ),
        FactorInput(name="Attention_ZScore", params={}, transform="raw"),
    ]
    result = engine.compute(factors, ComboSpec(method="weighted_sum"), universe_df)

    assert set(result.factor_frames.keys()) == {"RS_Rating_B", "PMARP", "Attention_ZScore"}
    for frame in result.factor_frames.values():
        values = frame.loc["2024-05-03"].dropna()
        assert len(values) >= 1
