import sqlite3

import pandas as pd

from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.primitives.portfolio_builder import PortfolioBuilder
from backtest.pipeline.spec import PortfolioSpec
from tests.pipeline.helpers import seed_pipeline_dbs


def test_equal_weight_top_n(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    builder = PortfolioBuilder(PitData(market_db, company_db))
    score_frame = pd.DataFrame(
        [{"AAA": 3.0, "BBB": 2.0, "CCC": 1.0}],
        index=["2024-09-02"],
    )
    portfolio = PortfolioSpec(
        selection="top_n",
        top_n=2,
        rebalance="weekly",
        weighting="equal",
        max_position_weight=0.6,
    )

    weights = builder.build_target_weights(score_frame, portfolio)
    row = weights.loc["2024-09-02"].dropna()
    assert row.to_dict() == {"AAA": 0.5, "BBB": 0.5}


def test_inv_vol_weights_favor_lower_vol_symbol(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    with sqlite3.connect(market_db) as conn:
        rows = conn.execute(
            """
            SELECT date, close
            FROM daily_price
            WHERE symbol = 'BBB'
            ORDER BY date
            """
        ).fetchall()
        for idx, (date_str, close) in enumerate(rows):
            multiplier = 1.10 if idx % 2 == 0 else 0.90
            new_close = float(close) * multiplier
            conn.execute(
                """
                UPDATE daily_price
                SET open = ?, high = ?, low = ?, close = ?
                WHERE symbol = 'BBB' AND date = ?
                """,
                (new_close, new_close, new_close, new_close, date_str),
            )
        conn.commit()

    builder = PortfolioBuilder(PitData(market_db, company_db))
    score_frame = pd.DataFrame(
        [{"AAA": 3.0, "BBB": 2.0}],
        index=["2024-09-02"],
    )
    portfolio = PortfolioSpec(
        selection="top_n",
        top_n=2,
        rebalance="weekly",
        weighting="inv_vol",
        vol_lookback_days=60,
        max_position_weight=0.8,
    )

    weights = builder.build_target_weights(score_frame, portfolio)
    row = weights.loc["2024-09-02"].dropna()
    assert abs(float(row.sum()) - 1.0) < 1e-9
    assert row["AAA"] > row["BBB"]


def test_cap_respected_when_cap_times_n_lt_one(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    builder = PortfolioBuilder(PitData(market_db, company_db))
    score_frame = pd.DataFrame(
        [
            {
                "AAA": 5.0,
                "BBB": 4.0,
                "CCC": 3.0,
                "DDD": 2.0,
                "EEE": 1.0,
            }
        ],
        index=["2024-09-02"],
    )
    portfolio = PortfolioSpec(
        selection="top_n",
        top_n=5,
        rebalance="weekly",
        weighting="equal",
        max_position_weight=0.1,
    )

    weights = builder.build_target_weights(score_frame, portfolio)
    row = weights.loc["2024-09-02"].dropna()
    assert all(float(value) <= 0.1 + 1e-12 for value in row.tolist())
    assert float(row.sum()) <= 0.5 + 1e-12
