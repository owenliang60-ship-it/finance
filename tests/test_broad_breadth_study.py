from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from backtest.breadth_study.core import (
    attach_latest_market_cap,
    build_breadth_outputs,
    build_eligible_price_frame,
    event_cell_status,
)
from backtest.breadth_study import StudyConfig


def _price_rows(symbol: str, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.to_datetime(dates),
            "open": np.linspace(90, 110, len(dates)),
            "high": np.linspace(91, 111, len(dates)),
            "low": np.linspace(89, 109, len(dates)),
            "close": np.linspace(90, 110, len(dates)),
            "volume": 1_000_000,
            "source": "test",
        }
    )


def _cap_rows(symbol: str, rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.to_datetime([row[0] for row in rows]),
            "market_cap": [row[1] for row in rows],
            "source": "test",
        }
    )


def test_latest_market_cap_uses_latest_pit_value_not_once_large_forever():
    prices = _price_rows("FALL", ["2024-01-02", "2024-02-01", "2024-03-01"])
    caps = _cap_rows(
        "FALL",
        [
            ("2024-01-01", 20_000_000_000.0),
            ("2024-02-15", 2_000_000_000.0),
        ],
    )

    attached = attach_latest_market_cap(prices, caps)
    march = attached[attached["date"] == pd.Timestamp("2024-03-01")].iloc[0]

    assert march["latest_cap"] == 2_000_000_000.0

    eligible = build_eligible_price_frame(
        prices=prices,
        caps=caps,
        min_market_cap=10_000_000_000.0,
        max_staleness_days=90,
    )

    assert pd.Timestamp("2024-03-01") not in set(eligible["date"])


def test_delisted_ticker_excluded_after_staleness_limit():
    dates = pd.bdate_range("2024-01-02", "2024-06-15").strftime("%Y-%m-%d").tolist()
    prices = _price_rows("DEAD", dates)
    caps = _cap_rows("DEAD", [("2024-01-02", 30_000_000_000.0)])

    eligible = build_eligible_price_frame(
        prices=prices,
        caps=caps,
        min_market_cap=10_000_000_000.0,
        max_staleness_days=90,
    )

    assert eligible["date"].max() <= pd.Timestamp("2024-04-01")
    assert pd.Timestamp("2024-06-03") not in set(eligible["date"])


def test_ipo_before_first_market_cap_is_not_eligible():
    prices = _price_rows("IPO", ["2024-01-02", "2024-01-03", "2024-02-01"])
    caps = _cap_rows("IPO", [("2024-01-15", 15_000_000_000.0)])

    attached = attach_latest_market_cap(prices, caps)
    before_ipo = attached[attached["date"] == pd.Timestamp("2024-01-02")]

    assert before_ipo["latest_cap"].isna().all()

    eligible = build_eligible_price_frame(
        prices=prices,
        caps=caps,
        min_market_cap=10_000_000_000.0,
        max_staleness_days=90,
    )

    assert pd.Timestamp("2024-01-02") not in set(eligible["date"])


def test_event_cell_status_matches_preregistered_low_n_rules():
    assert event_cell_status(16) == "tested"
    assert event_cell_status(15) == "tested"
    assert event_cell_status(14) == "supportive"
    assert event_cell_status(10) == "supportive"
    assert event_cell_status(9) == "not_tested"


def test_daily_breadth_excludes_raw_price_dates_without_breadth_aggregates():
    trading_dates = pd.bdate_range("2024-01-02", periods=55).strftime("%Y-%m-%d").tolist()
    holiday_like_date = "2024-07-04"
    prices = pd.concat(
        [
            _price_rows("AAA", trading_dates),
            _price_rows("BBB", trading_dates),
            _price_rows("VIXLIKE", [holiday_like_date]),
        ],
        ignore_index=True,
    )
    caps = pd.concat(
        [
            _cap_rows("AAA", [("2024-01-02", 20_000_000_000.0)]),
            _cap_rows("BBB", [("2024-01-02", 20_000_000_000.0)]),
        ],
        ignore_index=True,
    )

    daily, _, _ = build_breadth_outputs(
        active_prices=prices,
        active_caps=caps,
        sidecar_prices=_price_rows("SIDECAR", trading_dates),
        sidecar_caps=_cap_rows("SIDECAR", [("2024-01-02", 20_000_000_000.0)]),
        overlay_symbols=[],
        config=StudyConfig(min_market_cap=1_000_000_000.0, max_staleness_days=90),
    )

    assert holiday_like_date not in set(daily["date"])
