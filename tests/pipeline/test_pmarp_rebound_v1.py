import sqlite3
from pathlib import Path

import pandas as pd

from backtest.pipeline.primitives.signal_engine import SignalEngine
from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.runner import PipelineRunner
from backtest.pipeline.spec import ComboSpec, FactorInput, StrategySpec


def _seed_rebound_market_db(path: Path) -> None:
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

    dates = pd.bdate_range("2024-01-02", periods=240)

    aaa_closes = [100.0] * 170 + [95.0, 90.0, 85.0, 86.0, 88.0, 90.0]
    aaa_closes += [91.0 + i for i in range(len(dates) - len(aaa_closes))]
    aaa_volumes = [1_000_000 + ((i % 5) - 2) * 50_000 for i in range(len(dates))]
    aaa_volumes[175] = 2_000_000

    bbb_closes = [100.0 + 0.05 * i for i in range(len(dates))]
    bbb_volumes = [1_000_000 + ((i % 3) - 1) * 25_000 for i in range(len(dates))]

    spy_closes = [100.0 + 0.15 * i for i in range(len(dates))]
    spy_volumes = [10_000_000] * len(dates)

    for symbol, closes, volumes in (
        ("AAA", aaa_closes, aaa_volumes),
        ("BBB", bbb_closes, bbb_volumes),
        ("SPY", spy_closes, spy_volumes),
    ):
        for date_value, close, volume in zip(dates, closes, volumes):
            open_price = close * 0.995
            cur.execute(
                "INSERT INTO daily_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol,
                    date_value.strftime("%Y-%m-%d"),
                    float(open_price),
                    float(max(open_price, close)),
                    float(min(open_price, close)),
                    float(close),
                    float(volume),
                    0.0,
                    0.0,
                ),
            )

    for symbol, market_cap in (
        ("AAA", 250_000_000_000.0),
        ("BBB", 180_000_000_000.0),
    ):
        for date_value in dates:
            cur.execute(
                "INSERT INTO historical_market_cap VALUES (?, ?, ?)",
                (symbol, date_value.strftime("%Y-%m-%d"), market_cap),
            )

    conn.commit()
    conn.close()


def _seed_rebound_company_db(path: Path) -> None:
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
            ("AAA", "AAA Corp", "Technology", "Software", "NASDAQ", 250_000_000_000.0, 1, "test", "2024-01-02", "2024-01-02"),
            ("BBB", "BBB Corp", "Technology", "Semiconductors", "NASDAQ", 180_000_000_000.0, 1, "test", "2024-01-02", "2024-01-02"),
        ],
    )
    conn.commit()
    conn.close()


def _shift_rvol_peak_before_signal(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE daily_price SET volume = ? WHERE symbol = 'AAA' AND date = '2024-09-03'",
        (1_100_000.0,),
    )
    conn.execute(
        "UPDATE daily_price SET volume = ? WHERE symbol = 'AAA' AND date = '2024-08-29'",
        (2_000_000.0,),
    )
    conn.commit()
    conn.close()


def _make_spec_dict() -> dict:
    return {
        "spec_id": "pmarp_rebound_v1_test",
        "benchmark": "SPY",
        "universe": {
            "market_cap_min_usd": 100_000_000_000,
            "min_names": 2,
        },
        "factors": [
            {
                "name": "PMARP_Rebound_V1",
                "params": {
                    "holding_window_days": 20,
                    "regime_symbol": "SPY",
                },
                "transform": "raw",
            }
        ],
        "combo": {"method": "single"},
        "portfolio": {
            "selection": "top_n",
            "top_n": 1,
            "rebalance": "daily",
            "weighting": "equal",
            "max_position_weight": 1.0,
        },
        "execution": {
            "timing": "next_open",
            "transaction_cost_bps": 5.0,
        },
        "evaluation": {
            "newey_west_lag_days": 5,
        },
        "period": {
            "start": "2024-06-03",
            "train_end": "2024-10-31",
            "test_end": "2024-11-29",
        },
    }


def _recent_universe_frame(start: str = "2024-08-01", end: str = "2024-09-06") -> pd.DataFrame:
    rows = []
    for date_value in pd.bdate_range(start, end):
        date_str = date_value.strftime("%Y-%m-%d")
        rows.append(
            {"date": date_str, "symbol": "AAA", "market_cap": 250_000_000_000.0, "sector": "Technology"}
        )
        rows.append(
            {"date": date_str, "symbol": "BBB", "market_cap": 180_000_000_000.0, "sector": "Technology"}
        )
    return pd.DataFrame(rows)


def test_daily_rebalance_spec_is_supported():
    spec = StrategySpec.from_dict(_make_spec_dict())
    assert spec.portfolio.rebalance == "daily"
    assert spec.resolved_newey_west_lag_days() == 5


def test_pmarp_rebound_factor_emits_score_for_recent_signal(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_rebound_market_db(market_db)
    _seed_rebound_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = pd.DataFrame(
        [
            {"date": "2024-09-06", "symbol": "AAA"},
            {"date": "2024-09-06", "symbol": "BBB"},
        ]
    )
    factor = FactorInput(
        name="PMARP_Rebound_V1",
        params={"holding_window_days": 20, "regime_symbol": "SPY"},
        transform="raw",
    )

    result = engine.compute([factor], ComboSpec(method="single"), universe_df)
    values = result.factor_frames["PMARP_Rebound_V1"].loc["2024-09-06"].dropna()

    assert "AAA" in values.index
    assert "BBB" not in values.index
    assert values["AAA"] >= 2.0


def test_pmarp_rebound_supports_universe_regime_and_soft_confirm(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_rebound_market_db(market_db)
    _seed_rebound_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = _recent_universe_frame()
    factor = FactorInput(
        name="PMARP_Rebound_V1",
        params={
            "holding_window_days": 20,
            "regime_mode": "universe_equal_weight_ema",
            "regime_fast_ema": 2,
            "regime_slow_ema": 4,
            "regime_slope_lookback": 1,
            "confirm_mode": "soft",
            "confirm_floor": 0.0,
        },
        transform="raw",
    )

    result = engine.compute([factor], ComboSpec(method="single"), universe_df)
    values = result.factor_frames["PMARP_Rebound_V1"].loc["2024-09-06"].dropna()

    assert "AAA" in values.index
    assert values["AAA"] > 0


def test_pmarp_rebound_supports_recent_peak_soft_confirm(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_rebound_market_db(market_db)
    _seed_rebound_company_db(company_db)
    _shift_rvol_peak_before_signal(market_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = pd.DataFrame(
        [
            {"date": "2024-09-06", "symbol": "AAA"},
            {"date": "2024-09-06", "symbol": "BBB"},
        ]
    )
    hard_factor = FactorInput(
        name="PMARP_Rebound_V1",
        params={"holding_window_days": 20, "regime_symbol": "SPY"},
        transform="raw",
    )
    peak_factor = FactorInput(
        name="PMARP_Rebound_V1",
        params={
            "holding_window_days": 20,
            "regime_symbol": "SPY",
            "confirm_mode": "recent_peak_soft",
            "confirm_floor": 0.5,
            "recent_peak_window": 7,
            "recent_peak_threshold": 2.0,
        },
        transform="raw",
    )

    hard_result = engine.compute([hard_factor], ComboSpec(method="single"), universe_df)
    peak_result = engine.compute([peak_factor], ComboSpec(method="single"), universe_df)

    hard_values = hard_result.factor_frames["PMARP_Rebound_V1"].loc["2024-09-06"].dropna()
    peak_values = peak_result.factor_frames["PMARP_Rebound_V1"].loc["2024-09-06"].dropna()

    assert "AAA" not in hard_values.index
    assert "AAA" in peak_values.index
    assert peak_values["AAA"] >= 2.0


def test_pmarp_rebound_supports_breadth_regime(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_rebound_market_db(market_db)
    _seed_rebound_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = _recent_universe_frame()
    factor = FactorInput(
        name="PMARP_Rebound_V1",
        params={
            "holding_window_days": 20,
            "regime_mode": "universe_breadth",
            "regime_breadth_threshold": 0.5,
        },
        transform="raw",
    )

    result = engine.compute([factor], ComboSpec(method="single"), universe_df)
    values = result.factor_frames["PMARP_Rebound_V1"].loc["2024-09-06"].dropna()

    assert "AAA" in values.index


def test_pmarp_rebound_can_filter_high_trailing_volatility(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_rebound_market_db(market_db)
    _seed_rebound_company_db(company_db)

    pit = PitData(market_db, company_db)
    engine = SignalEngine(pit)
    universe_df = pd.DataFrame(
        [
            {"date": "2024-09-06", "symbol": "AAA"},
            {"date": "2024-09-06", "symbol": "BBB"},
        ]
    )
    factor = FactorInput(
        name="PMARP_Rebound_V1",
        params={
            "holding_window_days": 20,
            "regime_symbol": "SPY",
            "vol_lookback": 60,
            "max_trailing_volatility": 0.10,
        },
        transform="raw",
    )

    result = engine.compute([factor], ComboSpec(method="single"), universe_df)
    values = result.factor_frames["PMARP_Rebound_V1"].loc["2024-09-06"].dropna()

    assert "AAA" not in values.index


def test_e2e_daily_rebound_pipeline_runs(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    _seed_rebound_market_db(market_db)
    _seed_rebound_company_db(company_db)

    spec_path = tmp_path / "rebound.yaml"
    spec = _make_spec_dict()
    spec_path.write_text(
        "\n".join(
            [
                'spec_id: "pmarp_rebound_v1_test"',
                'benchmark: "SPY"',
                "universe:",
                "  market_cap_min_usd: 100000000000",
                "  min_names: 2",
                "factors:",
                '  - name: "PMARP_Rebound_V1"',
                "    params:",
                "      holding_window_days: 20",
                '      regime_symbol: "SPY"',
                '    transform: "raw"',
                "combo:",
                '  method: "single"',
                "portfolio:",
                '  selection: "top_n"',
                "  top_n: 1",
                '  rebalance: "daily"',
                '  weighting: "equal"',
                "  max_position_weight: 1.0",
                "execution:",
                '  timing: "next_open"',
                "  transaction_cost_bps: 5.0",
                "evaluation:",
                "  newey_west_lag_days: 5",
                "period:",
                '  start: "2024-06-03"',
                '  train_end: "2024-10-31"',
                '  test_end: "2024-11-29"',
            ]
        ),
        encoding="utf-8",
    )

    runner = PipelineRunner(
        spec_path,
        artifact_root=tmp_path / "reports",
        market_db_path=market_db,
        company_db_path=company_db,
    )
    result = runner.run()

    assert not result.nav_is.empty
    assert result.metrics["strategy"]["is"]["n_trades"] >= 1
    assert "factor" in result.metrics
    assert result.output_paths["report_html"].exists()
