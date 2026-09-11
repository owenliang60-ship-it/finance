"""Historical market-cap anomaly state machine and repair contracts."""
import logging
from unittest.mock import Mock

import pytest

from src.data.market_store import MarketStore
from terminal.historical_basket_valuation import select_asof_market_cap
from terminal.historical_market_cap_sanity import (
    accepted_market_cap_status,
    build_forced_refresh_windows,
    market_cap_row_hash,
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


def test_back_adjusted_price_and_mcap_series_do_not_apply_split_twice():
    result = scan_market_cap_candidates(
        _rows("MCHP", [("2021-10-12", 39.12e9),
                       ("2021-10-13", 38.98e9)], "market_cap"),
        _rows("MCHP", [("2021-10-12", 70.50),
                       ("2021-10-13", 70.25)], "close"),
        [{"symbol": "MCHP", "date": "2021-10-13",
          "numerator": 2, "denominator": 1}],
    )
    split_day = result[-1]
    assert split_day["status"] == "split_consistent"
    assert split_day["split_ratio"] == 2.0
    assert split_day["split_adjustment_applied"] is False
    assert split_day["split_adjustment_mode"] == "already_back_adjusted"


def test_split_day_synchronous_price_and_mcap_divide_stays_invalid_until_recovery():
    result = scan_market_cap_candidates(
        _rows("TEST", [
            ("2026-01-02", 100e9),
            ("2026-01-05", 10.2e9),
            ("2026-01-06", 10.5e9),
            ("2026-01-07", 105e9),
        ], "market_cap"),
        _rows("TEST", [
            ("2026-01-02", 100.0),
            ("2026-01-05", 10.2),
            ("2026-01-06", 10.5),
            ("2026-01-07", 10.5),
        ], "close"),
        [{"symbol": "TEST", "date": "2026-01-05",
          "numerator": 10, "denominator": 1}],
    )
    status = {row["date"]: row["status"] for row in result}
    assert status["2026-01-05"] == "invalid_mcap"
    assert status["2026-01-06"] == "invalid_mcap"
    assert status["2026-01-07"] == "clean"
    assert result[-1]["normalization_recovery"] is True


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


def test_non_trading_split_is_applied_to_first_following_observation():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 1000.0),
                       ("2026-01-05", 1000.0)], "market_cap"),
        _rows("TEST", [("2026-01-02", 10.0),
                       ("2026-01-05", 5.0)], "close"),
        [{"symbol": "TEST", "date": "2026-01-04",
          "numerator": 2, "denominator": 1}],
    )
    monday = result[-1]
    assert monday["status"] == "split_consistent"
    assert monday["split_ratio"] == 2.0
    assert monday["split_source_dates"] == ["2026-01-04"]


def test_missing_price_on_trigger_is_unresolved_and_fail_closed():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 100e9),
                       ("2026-01-05", 200e9)], "market_cap"),
        _rows("TEST", [("2026-01-02", 100.0)], "close"),
        [],
    )
    assert result[-1]["status"] == "unresolved"
    assert not accepted_market_cap_status(result[-1]["status"])


@pytest.mark.parametrize("bad", [0, -1, None, float("nan"), float("inf")])
def test_invalid_market_cap_is_quarantined_without_poisoning_clean_anchor(bad):
    caps = _rows("HONA", [("2026-06-25", 1000), ("2026-06-26", bad),
                          ("2026-06-29", 1020)], "market_cap")
    result = scan_market_cap_candidates(
        caps, _rows("HONA", [("2026-06-25", 10), ("2026-06-26", 10),
                              ("2026-06-29", 10.2)], "close"), [])
    assert [r["status"] for r in result] == ["clean", "invalid_mcap", "clean"]
    assert result[1]["candidate_reason"] == "invalid_market_cap_value"
    assert quarantine_market_cap_dates(result) == {"2026-06-26"}
    assert result[-1]["expected_shares"] == pytest.approx(100)
    assert result[-1]["normalization_recovery"] is True
    assert caps[1]["market_cap"] is bad  # The raw source is never patched.
    assert select_asof_market_cap(caps, "2026-06-26",
        {r["date"]: r["status"] for r in result},
        quarantine_market_cap_dates(result)) is None


def test_zero_market_cap_on_split_date_defers_event_until_valid_observation():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 1000), ("2026-01-05", 0),
                       ("2026-01-06", 1020)], "market_cap"),
        _rows("TEST", [("2026-01-02", 10), ("2026-01-05", 5),
                       ("2026-01-06", 5.1)], "close"),
        [{"date": "2026-01-05", "numerator": 2, "denominator": 1}])
    assert [r["status"] for r in result] == ["clean", "invalid_mcap", "split_consistent"]
    assert result[-1]["split_source_dates"] == ["2026-01-05"]
    assert result[-1]["expected_shares"] == pytest.approx(200)


def test_invalid_market_cap_does_not_allow_an_unverified_new_share_regime():
    result = scan_market_cap_candidates(
        _rows("TEST", [("2026-01-02", 1000), ("2026-01-05", 0),
                       ("2026-01-06", 2000)], "market_cap"),
        _rows("TEST", [("2026-01-02", 10), ("2026-01-05", 10),
                       ("2026-01-06", 10)], "close"), [])
    assert [r["status"] for r in result] == ["clean", "invalid_mcap", "invalid_mcap"]


def test_leading_zero_cap_is_not_a_bootstrap_anchor_or_dropped_from_evidence():
    result = scan_market_cap_candidates(
        _rows("HONA", [("2026-06-26", 0), ("2026-06-29", 1000)], "market_cap"),
        _rows("HONA", [("2026-06-26", 10), ("2026-06-29", 10)], "close"), [])
    assert [r["status"] for r in result] == ["invalid_mcap", "clean"]
    assert result[-1]["expected_shares"] is None


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
    assert result[0]["skipped"] is False


def test_refresh_adapter_skips_empty_response_without_range_replace(caplog):
    """R2: an empty vendor response must not reach the destructive
    range-replace CRUD at all — it must be skipped fail-closed, with a
    warning, leaving whatever the store already has untouched."""
    client = Mock()
    store = Mock()
    client.get_historical_market_cap.return_value = []
    with caplog.at_level(
            logging.WARNING, logger="terminal.historical_market_cap_sanity"):
        result = refresh_market_cap_windows(
            "KLAC", [{"from_date": "2026-06-10", "to_date": "2026-06-12"}],
            client, store)
    client.get_historical_market_cap.assert_called_once_with(
        "KLAC", from_date="2026-06-10", to_date="2026-06-12")
    store.replace_historical_market_cap_range.assert_not_called()
    assert result[0]["rows"] == 0
    assert result[0]["skipped"] is True
    assert any(
        "KLAC" in record.message and "2026-06-10" in record.message
        for record in caplog.records
    ), "expected a warning naming the symbol and skipped window"


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan")])
def test_invalid_refresh_payload_preserves_range_and_explicit_skip_provenance(tmp_path, write, bad):
    prior = [{"symbol": "HONA", "date": "2026-06-26", "market_cap": 0}]
    client = Mock()
    client.get_historical_market_cap.return_value = [
        {"symbol": "HONA", "date": "2026-06-26", "market_cap": bad}]
    store = MarketStore(tmp_path / "market.db") if write else None
    if store:
        store.upsert_historical_market_cap("HONA", prior)
    try:
        result = refresh_market_cap_windows("HONA", [{
            "from_date": "2026-06-26", "to_date": "2026-06-29"}],
            client, store, existing_rows=prior)[0]
        assert result["skipped"] is True
        assert result["rows"] == 1
        assert result["rejection_reason"]
        assert result["pre_row_hash"] == result["post_row_hash"]
        assert result["row_data"] == []
        if store:
            assert store.get_historical_market_cap_range(
                "HONA", "2026-06-26", "2026-06-29")[0]["market_cap"] == 0
    finally:
        if store:
            store.close()


# ---------------------------------------------------------------------------
# Task 4 (R3 / issue048): forced-refresh provenance the source tables destroy
# ---------------------------------------------------------------------------

def test_refresh_adapter_records_pre_and_post_row_hashes():
    """A forced refresh overwrites the rows that prove it was needed.

    Post-refresh source tables cannot reconstruct the pre-refresh anomaly, so
    the adapter hands the manifest a hash of both sides of the replacement.
    """
    client = Mock()
    store = Mock()
    client.get_historical_market_cap.return_value = [
        {"symbol": "KLAC", "date": "2026-06-10", "market_cap": 280e9},
        {"symbol": "KLAC", "date": "2026-06-11", "market_cap": 281e9},
    ]
    existing = [
        {"symbol": "KLAC", "date": "2026-06-09", "market_cap": 279e9},
        {"symbol": "KLAC", "date": "2026-06-10", "market_cap": 28e9},
        {"symbol": "KLAC", "date": "2026-06-11", "market_cap": 28.1e9},
    ]
    result = refresh_market_cap_windows(
        "KLAC", [{"from_date": "2026-06-10", "to_date": "2026-06-12"}],
        client, store, existing_rows=existing)
    window = result[0]
    # Only the replaced range is hashed; the untouched 06-09 row is not.
    assert window["pre_row_count"] == 2
    assert window["post_row_count"] == 2
    assert window["pre_row_hash"] != window["post_row_hash"]
    assert len(window["pre_row_hash"]) == 64


def test_row_hash_is_order_independent_and_value_sensitive():
    left = market_cap_row_hash([
        {"date": "2026-06-11", "market_cap": 281e9},
        {"date": "2026-06-10", "market_cap": 280e9}])
    right = market_cap_row_hash([
        {"date": "2026-06-10", "market_cap": 280e9},
        {"date": "2026-06-11", "market_cap": 281e9}])
    assert left == right
    assert left != market_cap_row_hash([
        {"date": "2026-06-10", "market_cap": 280e9},
        {"date": "2026-06-11", "market_cap": 281.5e9}])
    assert market_cap_row_hash([]) == market_cap_row_hash([])


def test_skipped_refresh_reports_the_preserved_range_on_both_sides():
    client = Mock()
    client.get_historical_market_cap.return_value = []
    existing = [{"symbol": "KLAC", "date": "2026-06-10", "market_cap": 28e9}]
    result = refresh_market_cap_windows(
        "KLAC", [{"from_date": "2026-06-10", "to_date": "2026-06-12"}],
        client, None, existing_rows=existing)
    window = result[0]
    assert window["skipped"] is True
    assert window["pre_row_hash"] == window["post_row_hash"]
    assert window["post_row_count"] == 1
