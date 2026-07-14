"""Historical market-cap anomaly state machine and repair contracts."""
from unittest.mock import Mock

import pytest

from src.data.market_store import MarketStore
from terminal.historical_market_cap_sanity import (
    accepted_market_cap_status,
    build_forced_refresh_windows,
    quarantine_market_cap_dates,
    refresh_market_cap_windows,
    scan_market_cap_candidates,
)


def _rows(symbol, values, field):
    return [
        {"symbol": symbol, "date": row_date, field: value}
        for row_date, value in values
    ]


def test_klac_split_day_does_not_close_anomaly_against_wrong_adjacent_regime():
    dates = [
        ("2026-06-09", 280e9, 2140.0),
        ("2026-06-10", 28e9, 2140.0),       # implied shares /10: opens
        ("2026-06-11", 31e9, 2370.0),
        ("2026-06-12", 32e9, 244.5),        # adjacent shares x10, still /10 vs anchor x10
        ("2026-06-15", 32.5e9, 248.0),
        ("2026-06-16", 33e9, 252.0),
        ("2026-06-17", 33.5e9, 256.0),
        ("2026-06-18", 34e9, 260.0),
        ("2026-06-19", 34.5e9, 264.0),
        ("2026-06-22", 35e9, 268.0),
        ("2026-06-23", 35.5e9, 271.0),
        ("2026-06-24", 315e9, 240.8),       # correct post-split regime
    ]
    mcap = _rows("KLAC", [(d, m) for d, m, _ in dates], "market_cap")
    price = _rows("KLAC", [(d, p) for d, _, p in dates], "close")
    splits = [{"symbol": "KLAC", "date": "2026-06-12",
               "numerator": 10, "denominator": 1}]
    result = scan_market_cap_candidates(mcap, price, splits)
    status = {row["date"]: row["status"] for row in result}
    assert status["2026-06-09"] == "clean"
    assert all(status[day] == "invalid_mcap" for day, _, _ in dates[1:-1])
    assert status["2026-06-12"] == "invalid_mcap"
    assert status["2026-06-24"] == "clean"
    assert result[-1]["normalization_recovery"] is True


def test_mchp_complete_bad_interval_and_recovery_boundary():
    dates = [
        ("2026-01-30", 41e9, 76.0),
        ("2026-02-02", 82e9, 76.0),
        ("2026-02-03", 82.5e9, 76.5),
        ("2026-02-04", 81e9, 75.0),
        ("2026-02-05", 82e9, 76.0),
        ("2026-02-06", 81.5e9, 75.5),
        ("2026-02-09", 40.5e9, 75.0),
    ]
    result = scan_market_cap_candidates(
        _rows("MCHP", [(d, m) for d, m, _ in dates], "market_cap"),
        _rows("MCHP", [(d, p) for d, _, p in dates], "close"),
        [],
    )
    status = {row["date"]: row["status"] for row in result}
    assert all(status[day] == "invalid_mcap" for day, _, _ in dates[1:-1])
    assert status["2026-02-09"] == "clean"
    assert result[-1]["normalization_recovery"] is True


def test_slab_price_aligned_jump_is_plausible():
    result = scan_market_cap_candidates(
        _rows("SLAB", [("2026-02-03", 4.5e9),
                       ("2026-02-04", 6.7e9)], "market_cap"),
        _rows("SLAB", [("2026-02-03", 136.62),
                       ("2026-02-04", 203.41)], "close"),
        [],
    )
    assert result[-1]["status"] == "price_move_plausible"
    assert result[-1]["candidate"] is True


def test_correct_split_is_anchored_and_accepted():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 100e9),
                       ("2026-01-05", 102e9)], "market_cap"),
        _rows("TEST", [("2026-01-02", 100.0),
                       ("2026-01-05", 10.2)], "close"),
        [{"symbol": "TEST", "date": "2026-01-05",
          "numerator": 10, "denominator": 1}],
    )
    assert result[-1]["status"] == "split_consistent"
    assert result[-1]["candidate"] is True
    assert result[-1]["expected_shares"] == pytest.approx(10e9)


def test_split_adjacent_rows_are_inspected_even_without_large_mcap_jump():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 100e9),
                       ("2026-01-05", 102e9),
                       ("2026-01-06", 101e9)], "market_cap"),
        _rows("TEST", [("2026-01-02", 100.0),
                       ("2026-01-05", 10.2),
                       ("2026-01-06", 10.1)], "close"),
        [{"symbol": "TEST", "date": "2026-01-05",
          "numerator": 10, "denominator": 1}],
    )
    assert result[-1]["candidate"] is True
    assert result[-1]["candidate_reason"] == "split_adjacent"


def test_missing_price_on_trigger_is_unresolved_and_fail_closed():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 100e9),
                       ("2026-01-05", 200e9)], "market_cap"),
        _rows("TEST", [("2026-01-02", 100.0)], "close"),
        [],
    )
    assert result[-1]["status"] == "unresolved"
    assert not accepted_market_cap_status(result[-1]["status"])


def test_forced_refresh_windows_expand_and_merge_by_trading_calendar():
    trading = [f"2026-06-{day:02d}" for day in range(1, 31)]
    classifications = [
        {"date": "2026-06-10", "status": "invalid_mcap"},
        {"date": "2026-06-11", "status": "invalid_mcap"},
        {"date": "2026-06-23", "status": "unresolved"},
    ]
    windows = build_forced_refresh_windows(classifications, trading, pad=5)
    assert windows == [
        {"from_date": "2026-06-05", "to_date": "2026-06-16",
         "trigger_dates": ["2026-06-10", "2026-06-11"]},
        {"from_date": "2026-06-18", "to_date": "2026-06-28",
         "trigger_dates": ["2026-06-23"]},
    ]


def test_status_allowlist_and_quarantine_dates():
    for status in ("clean", "price_move_plausible", "split_consistent"):
        assert accepted_market_cap_status(status)
    rows = [
        {"date": "2026-01-01", "status": "clean"},
        {"date": "2026-01-02", "status": "invalid_mcap"},
        {"date": "2026-01-03", "status": "unresolved"},
    ]
    assert quarantine_market_cap_dates(rows) == {"2026-01-02", "2026-01-03"}


def test_atomic_range_replace_removes_old_bad_dates_missing_from_refetch(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.upsert_historical_market_cap("KLAC", [
        {"date": "2026-06-10", "market_cap": 28e9},
        {"date": "2026-06-11", "market_cap": 31e9},
        {"date": "2026-06-12", "market_cap": 32e9},
    ])
    store.replace_historical_market_cap_range(
        "KLAC", "2026-06-10", "2026-06-12", [
            {"symbol": "KLAC", "date": "2026-06-10", "market_cap": 280e9},
            {"symbol": "KLAC", "date": "2026-06-12", "market_cap": 310e9},
        ])
    rows = store.get_historical_market_cap_range(
        "KLAC", "2026-06-10", "2026-06-12")
    assert [row["date"] for row in rows] == ["2026-06-10", "2026-06-12"]
    store.close()


@pytest.mark.parametrize(
    "bad_rows",
    [
        [],
        [{"symbol": "WRONG", "date": "2026-06-10", "market_cap": 280e9}],
        [{"symbol": "KLAC", "date": "2026-06-09", "market_cap": 280e9}],
        [{"symbol": "KLAC", "date": "2026-06-10", "market_cap": -1}],
    ],
)
def test_bad_range_refresh_preserves_old_rows(tmp_path, bad_rows):
    store = MarketStore(tmp_path / "market.db")
    store.upsert_historical_market_cap(
        "KLAC", [{"date": "2026-06-10", "market_cap": 28e9}])
    with pytest.raises(ValueError):
        store.replace_historical_market_cap_range(
            "KLAC", "2026-06-10", "2026-06-12", bad_rows)
    rows = store.get_historical_market_cap_range(
        "KLAC", "2026-06-10", "2026-06-12")
    assert rows[0]["market_cap"] == 28e9
    store.close()


def test_refresh_adapter_uses_existing_client_and_atomic_store_path():
    client = Mock()
    store = Mock()
    client.get_historical_market_cap.return_value = [
        {"symbol": "KLAC", "date": "2026-06-10", "market_cap": 280e9}]
    result = refresh_market_cap_windows(
        "KLAC", [{"from_date": "2026-06-10", "to_date": "2026-06-12"}],
        client, store)
    client.get_historical_market_cap.assert_called_once_with(
        "KLAC", from_date="2026-06-10", to_date="2026-06-12")
    store.replace_historical_market_cap_range.assert_called_once()
    assert result[0]["rows"] == 1
