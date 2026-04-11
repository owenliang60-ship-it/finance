import sqlite3

import pandas as pd

from backtest.pipeline.primitives.execution import ExecutionEngine
from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.spec import ExecutionSpec
from tests.pipeline.helpers import seed_pipeline_dbs


def test_execution_uses_next_open_and_charges_costs(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    pit = PitData(market_db, company_db)
    engine = ExecutionEngine(pit_data=pit, initial_capital=100_000.0)
    target_weights = pd.DataFrame(
        [{"AAA": 1.0}],
        index=["2024-09-02"],
    )

    result = engine.run(
        target_weights=target_weights,
        benchmark_symbol="SPY",
        execution=ExecutionSpec(timing="next_open", transaction_cost_bps=10.0, spread_bps=5.0),
        start_date="2024-08-30",
        end_date="2024-09-10",
    )

    assert not result.nav.empty
    assert not result.trades.empty
    assert result.trades.iloc[0]["date"] == "2024-09-03"
    assert result.trades.iloc[0]["side"] == "BUY"
    assert result.total_costs > 0
    assert result.annual_turnover > 0
    assert set(result.benchmark_nav.columns) == {"date", "nav"}


def test_execution_forward_fills_missing_close_for_nav(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    missing_date = "2024-08-14"
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            "DELETE FROM daily_price WHERE symbol = 'AAA' AND date = ?",
            (missing_date,),
        )
        conn.commit()

    pit = PitData(market_db, company_db)
    engine = ExecutionEngine(pit_data=pit, initial_capital=100_000.0)
    target_weights = pd.DataFrame(
        [{"AAA": 1.0}],
        index=["2024-08-12"],
    )

    result = engine.run(
        target_weights=target_weights,
        benchmark_symbol="SPY",
        execution=ExecutionSpec(timing="next_open"),
        start_date="2024-08-12",
        end_date="2024-08-16",
    )

    nav_by_date = result.nav.set_index("date")["nav"]
    assert missing_date in nav_by_date.index
    assert float(nav_by_date.loc[missing_date]) > 90_000.0
