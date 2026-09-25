"""Backfill orchestration safety and idempotency contracts."""
import logging
import sqlite3
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import pytest

import scripts.backfill_soxx_historical_pe as backfill
from scripts.backfill_soxx_historical_pe import (
    BackfillState,
    failure_rate_exceeded,
    fundamentals_complete,
    market_cap_complete,
    parse_args,
    run_backfill,
    validate_snapshot_quality,
)


def _state():
    snapshot = {
        "basket_symbol": "SOXX", "holding_date": "2025-12-31",
        "source_kind": "disclosure", "raw_row_index": 0,
        "rebalance_close_date": "2025-12-19",
        "composition_effective_date": "2025-12-22",
        "composition_available_date": "2026-01-20",
        "raw_symbol": "TEST", "symbol": "TEST", "name": "TEST",
        "weight_pct": 4.0, "market_value": 40.0,
        "included": 1, "filter_reason": None, "covered_by": None,
        "row_accepted_at": "2026-01-20 16:00:00",
        "fetched_at": "2026-07-14T02:00:00Z",
    }
    quarters = [
        {"symbol": "TEST", "date": "2024-12-31", "period": "Q4",
         "accepted_date": "2025-01-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": "TEST", "date": "2025-03-31", "period": "Q1",
         "accepted_date": "2025-04-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": "TEST", "date": "2025-06-30", "period": "Q2",
         "accepted_date": "2025-07-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": "TEST", "date": "2025-09-30", "period": "Q3",
         "accepted_date": "2025-10-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
        {"symbol": "TEST", "date": "2025-12-31", "period": "Q4",
         "accepted_date": "2026-01-20 16:00:00", "reported_currency": "USD",
         "net_income": 25.0},
    ]
    symbols = ["TEST"] + [f"TEST{index:02d}" for index in range(1, 25)]
    snapshots = [{
        **snapshot, "raw_row_index": index, "raw_symbol": symbol,
        "symbol": symbol, "name": symbol,
    } for index, symbol in enumerate(symbols)]
    income = {symbol: [{**row, "symbol": symbol} for row in quarters]
              for symbol in symbols}
    mcaps = {symbol: [
        {"symbol": symbol, "date": day, "market_cap": 1000.0}
        for day in ("2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23")
    ] for symbol in symbols}
    prices = {symbol: [
        {"symbol": symbol, "date": day, "close": 10.0}
        for day in ("2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23")
    ] for symbol in symbols}
    return BackfillState(
        trading_dates=["2025-12-19", "2025-12-22", "2026-01-20",
                       "2026-01-21", "2026-01-22", "2026-01-23"],
        snapshots=snapshots,
        income_by_symbol=income,
        market_cap_by_symbol=mcaps,
        price_by_symbol=prices,
        splits_by_symbol={symbol: [] for symbol in symbols},
        fx_by_currency={},
    )


def test_parse_args_naked_dry_run_disables_network(tmp_path):
    args = parse_args([
        "--stage", "all", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23", "--dry-run", "--db", str(tmp_path / "x.db"),
    ])
    assert args.dry_run is True
    assert args.allow_network is False


def test_parse_args_rejects_bad_date_before_any_dependency():
    with pytest.raises(SystemExit):
        parse_args(["--stage", "all", "--from-date", "bad", "--dry-run"])


def test_fundamentals_completeness_rejects_recent_only_history():
    state = _state()
    recent_only = state.income_by_symbol["TEST"][-2:]
    assert not fundamentals_complete(
        recent_only, "2025-12-22", "2026-01-23", state.trading_dates)
    assert fundamentals_complete(
        state.income_by_symbol["TEST"], "2025-12-22", "2026-01-23",
        state.trading_dates)


def test_market_cap_completeness_uses_exact_seven_day_asof_rule():
    trading = ["2026-01-02", "2026-01-09", "2026-01-12"]
    rows = [{"date": "2026-01-02", "market_cap": 100.0}]
    assert market_cap_complete(rows, trading, "2026-01-02", "2026-01-09")
    assert not market_cap_complete(rows, trading, "2026-01-02", "2026-01-12")


def test_batch_fuse_is_strictly_greater_than_twenty_percent():
    assert not failure_rate_exceeded(2, 10)
    assert failure_rate_exceeded(3, 10)
    assert failure_rate_exceeded(1, 0)


@pytest.mark.parametrize("stage", ["fundamentals", "mcap"])
def test_nonempty_but_still_incomplete_responses_trip_failure_fuse(stage):
    state = _state()
    incomplete = sorted(state.income_by_symbol)[:6]
    if stage == "fundamentals":
        for symbol in incomplete:
            state.income_by_symbol[symbol] = []
        client = Mock()
        client.get_income_statement.side_effect = lambda symbol, **kwargs: [{
            "symbol": symbol, "date": "2025-12-31", "period": "Q4",
            "accepted_date": "2026-01-20 16:00:00",
            "reported_currency": "USD", "net_income": 25.0,
        }]
    else:
        for symbol in incomplete:
            state.market_cap_by_symbol[symbol] = []
        client = Mock()
        client.get_historical_market_cap.side_effect = lambda symbol, **kwargs: [{
            "symbol": symbol, "date": "2026-01-01", "market_cap": 1000.0,
        }]
    args = parse_args([
        "--stage", stage, "--from-date", "2026-01-20",
        "--to-date", "2026-01-23", "--dry-run", "--allow-network",
    ])
    with pytest.raises(RuntimeError, match="failure fuse"):
        run_backfill(args, state, client=client)


def test_stage_fuse_preserves_prior_per_symbol_writes_but_blocks_publication():
    state = _state()
    incomplete = sorted(state.income_by_symbol)[:7]
    for symbol in incomplete:
        state.income_by_symbol[symbol] = []
    client = Mock()
    client.get_income_statement.side_effect = lambda symbol, **kwargs: (
        [{**row, "symbol": symbol} for row in _state().income_by_symbol["TEST"]]
        if symbol == incomplete[0] else [{
            "symbol": symbol, "date": "2025-12-31", "period": "Q4",
            "accepted_date": "2026-01-20 16:00:00",
            "reported_currency": "USD", "net_income": 25.0,
        }])
    store = Mock()
    args = parse_args([
        "--stage", "fundamentals", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23",
    ])
    with pytest.raises(RuntimeError, match="failure fuse"):
        run_backfill(args, state, client=client, store=store)
    assert store.upsert_income.called
    store.replace_basket_ttm_valuation_range.assert_not_called()


def test_implausible_fx_response_trips_fuse_without_writing():
    state = _state()
    for row in state.income_by_symbol["TEST"]:
        row["reported_currency"] = "TWD"
    client = Mock()
    client.get_historical_fx.return_value = [
        {"date": "2026-01-20", "close": 30.0},
        {"date": "2026-01-23", "close": 30.0},
    ]
    store = Mock()
    args = parse_args([
        "--stage", "fx", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23",
    ])
    with pytest.raises(RuntimeError, match="fx failure fuse"):
        run_backfill(args, state, client=client, store=store)
    store.upsert_fx_daily.assert_not_called()


def test_snapshot_quality_blocks_truncated_members_or_weights():
    rows = _state().snapshots
    assert validate_snapshot_quality(rows) == {
        "members": 25, "raw_weight_sum": 100.0}
    with pytest.raises(ValueError, match="member count"):
        validate_snapshot_quality(rows[:5])
    with pytest.raises(ValueError, match="raw weight"):
        validate_snapshot_quality([{**row, "weight_pct": 3.4} for row in rows])


def test_naked_dry_run_uses_existing_state_without_network_or_writes():
    args = parse_args([
        "--stage", "all", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23", "--dry-run",
    ])
    client = Mock()
    store = Mock()
    report = run_backfill(args, _state(), client=None, store=None)
    assert report["dry_run"] is True
    assert report["network_enabled"] is False
    assert report["universe_count"] == 25
    assert report["member_universe_count"] == 25
    assert report["evaluation_universe_count"] == 25
    assert report["alias_support_symbols"] == []
    assert report["coverage_7d"]["publishable_dates"] == 4
    assert report["planned_network_calls"]
    client.assert_not_called()
    store.assert_not_called()


def test_empty_snapshot_universe_fails_fast():
    args = parse_args([
        "--stage", "compute", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23", "--dry-run",
    ])
    with pytest.raises(ValueError, match="universe"):
        run_backfill(args, BackfillState(trading_dates=["2026-01-20"]))


def test_authoritative_alias_replaces_wrong_raw_symbol_in_evaluation_universe():
    state = _state()
    state.snapshots[0].update({
        "symbol": "TERN", "raw_symbol": "TERN", "alias_symbol": "TER",
        "alias_mode": "authoritative", "alias_reason": "vendor symbol error",
    })
    assert "TERN" not in backfill._snapshot_universe(state.snapshots)
    assert "TER" in backfill._snapshot_universe(state.snapshots)


def test_write_dependency_opens_backup_before_market_store(monkeypatch, tmp_path):
    events = []
    db = tmp_path / "market.db"
    db.write_bytes(b"placeholder")
    monkeypatch.setattr(
        backfill, "_backup_sqlite",
        lambda path, label: events.append(("backup", Path(path), label)) or tmp_path / "b.db")
    monkeypatch.setattr(
        backfill, "MarketStore",
        lambda path: events.append(("store", Path(path))) or Mock())
    backup_path, store = backfill.open_write_dependencies(db)
    assert events[0][0] == "backup"
    assert events[1][0] == "store"
    assert backup_path == tmp_path / "b.db"
    assert store is not None


def test_compute_range_is_one_atomic_store_call():
    args = parse_args([
        "--stage", "compute", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23",
    ])
    store = Mock()
    report = run_backfill(args, _state(), store=store)
    store.replace_basket_ttm_valuation_range.assert_called_once()
    call = store.replace_basket_ttm_valuation_range.call_args.args
    assert call[:3] == ("SOXX", "2026-01-20", "2026-01-23")
    assert len(call[3]) == 4
    assert report["coverage_7d"]["publishable_dates"] == 4
    preview = report["valuation_preview"]
    assert preview["current"]["valuation_date"] == "2026-01-23"
    assert preview["primary"]["current"] == pytest.approx(10.0)
    assert preview["primary"]["percentile"] == 100.0
    assert preview["weight_coverage"]["minimum"] == 1.0


def test_write_compute_stops_before_store_when_95pct_target_misses():
    state = _state()
    state.income_by_symbol = {symbol: [] for symbol in state.income_by_symbol}
    args = parse_args([
        "--stage", "compute", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23",
    ])
    store = Mock()
    with pytest.raises(RuntimeError, match="95%"):
        run_backfill(args, state, store=store)
    store.replace_basket_ttm_valuation_range.assert_not_called()


def test_dry_run_main_returns_one_and_prints_report_when_target_misses(
    monkeypatch, tmp_path, capsys,
):
    db = tmp_path / "market.db"
    db.touch()
    args = Namespace(
        dry_run=True, db=db, to_date="2026-01-23", allow_network=False,
    )
    conn = Mock()
    monkeypatch.setattr(backfill, "parse_args", lambda argv: args)
    monkeypatch.setattr(backfill, "_connect_ro", lambda path: conn)
    monkeypatch.setattr(backfill, "load_state", lambda opened, end: _state())
    monkeypatch.setattr(backfill, "run_backfill", lambda *a, **k: {
        "coverage_7d": {"target_passed": False},
    })
    assert backfill.main([]) == 1
    assert '"target_passed": false' in capsys.readouterr().out
    conn.close.assert_called_once()


def test_disclosure_supersedes_live_for_same_rebalance_effective_date():
    state = _state()
    live = [{
        **row, "holding_date": "2026-01-23", "source_kind": "live",
        "data_quality_tier": "live_tail_weaker",
    } for row in state.snapshots]
    state.snapshots.extend(live)
    args = parse_args([
        "--stage", "compute", "--from-date", "2026-01-20",
        "--to-date", "2026-01-23",
    ])
    store = Mock()
    run_backfill(args, state, store=store)
    output = store.replace_basket_ttm_valuation_range.call_args.args[3]
    assert {row["holding_date"] for row in output} == {"2025-12-31"}
    assert {row["data_quality_tier"] for row in output} == {
        "historical_disclosure_fixed_proxy"}


def test_sanity_planner_keeps_mcap_date_missing_from_price_calendar():
    state = _state()
    state.market_cap_by_symbol["TEST"].append({
        "symbol": "TEST", "date": "2026-01-24", "market_cap": 10000.0,
    })
    args = parse_args([
        "--stage", "mcap_sanity", "--from-date", "2026-01-20",
        "--to-date", "2026-01-24", "--dry-run",
    ])

    report = run_backfill(args, state)

    planned = [row for row in report["planned_network_calls"]
               if row["stage"] == "mcap_sanity"]
    assert planned
    assert "2026-01-24" in planned[0]["trigger_dates"]


def test_sanity_stage_skips_empty_refresh_response_via_shared_adapter(caplog):
    """R2: _run_sanity must delegate window refresh to the shared,
    fail-closed refresh_market_cap_windows adapter instead of its own
    inline loop. An empty vendor response must not touch the store and
    must not corrupt in-memory state, matching the previous safe inline
    behavior (skip) plus a warning."""
    state = _state()
    original_mcap = [dict(row) for row in state.market_cap_by_symbol["TEST"]]
    state.market_cap_by_symbol["TEST"].append({
        "symbol": "TEST", "date": "2026-01-24", "market_cap": 10000.0,
    })
    args = parse_args([
        "--stage", "mcap_sanity", "--from-date", "2026-01-20",
        "--to-date", "2026-01-24",
    ])
    client = Mock()
    client.get_historical_market_cap.return_value = []
    store = Mock()

    with caplog.at_level(
            logging.WARNING, logger="terminal.historical_market_cap_sanity"):
        report = run_backfill(args, state, client=client, store=store)

    store.replace_historical_market_cap_range.assert_not_called()
    summary = report["stages"]["mcap_sanity"]["TEST"]
    assert summary["refreshed"] == 0
    assert summary["refreshed_windows"] == []
    # In-memory state for the un-refreshed window must be untouched aside
    # from the anomalous row appended above by the test itself.
    assert [row for row in state.market_cap_by_symbol["TEST"]
            if row["date"] != "2026-01-24"] == original_mcap
    assert any("TEST" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Fix round 1 / Important 1: the empty-response fuse must not be diluted by
# members that were never fetched.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", ["fundamentals", "mcap"])
def test_empty_response_fuse_denominator_is_attempted_not_universe(stage):
    """Every fetched member came back empty: that is a 100% vendor failure.

    Measured against the whole universe instead of the members actually
    attempted, a total vendor outage over a minority of the basket reads as a
    small failure rate and the run publishes anyway.
    """
    state = _state()
    universe = sorted(state.income_by_symbol)
    attempted = universe[:4]  # 4/25 = 16% of the universe, 100% of the fetches
    client = Mock()
    if stage == "fundamentals":
        for symbol in attempted:
            state.income_by_symbol[symbol] = []
        client.get_income_statement.return_value = []
        runner = backfill._run_fundamentals
    else:
        for symbol in attempted:
            state.market_cap_by_symbol[symbol] = []
        client.get_historical_market_cap.return_value = []
        runner = backfill._run_mcap
    args = Namespace(from_date="2026-01-20", to_date="2026-01-23")
    report = {"stages": {}, "planned_network_calls": []}
    with pytest.raises(RuntimeError, match="failure fuse"):
        runner(args, state, universe, client, None, report,
               fuse_on_incompleteness=False)


@pytest.mark.parametrize("stage", ["fundamentals", "mcap"])
def test_empty_response_fuse_tolerates_a_minority_of_failed_fetches(stage):
    """One bad response out of ten fetches is not an outage."""
    state = _state()
    universe = sorted(state.income_by_symbol)
    attempted = universe[:10]
    failing = attempted[0]
    client = Mock()
    if stage == "fundamentals":
        for symbol in attempted:
            state.income_by_symbol[symbol] = []
        client.get_income_statement.side_effect = lambda symbol, **kwargs: (
            [] if symbol == failing else [{
                "symbol": symbol, "date": "2025-12-31", "period": "Q4",
                "accepted_date": "2026-01-20 16:00:00",
                "reported_currency": "USD", "net_income": 25.0}])
        runner = backfill._run_fundamentals
    else:
        for symbol in attempted:
            state.market_cap_by_symbol[symbol] = []
        client.get_historical_market_cap.side_effect = (
            lambda symbol, **kwargs: [] if symbol == failing else [{
                "symbol": symbol, "date": "2026-01-20", "market_cap": 1000.0}])
        runner = backfill._run_mcap
    args = Namespace(from_date="2026-01-20", to_date="2026-01-23")
    report = {"stages": {}, "planned_network_calls": []}
    runner(args, state, universe, client, None, report,
           fuse_on_incompleteness=False)
    assert report["stages"][stage]["empty_responses"] == [failing]


@pytest.mark.parametrize("stage", ["fundamentals", "mcap"])
def test_soxx_incompleteness_fuse_keeps_the_universe_denominator(stage):
    """The SOXX contract is unchanged: incompleteness is measured basket-wide."""
    state = _state()
    universe = sorted(state.income_by_symbol)
    broken = universe[:4]  # 16% of the basket, below the 20% fuse
    client = Mock()
    if stage == "fundamentals":
        for symbol in broken:
            state.income_by_symbol[symbol] = []
        client.get_income_statement.return_value = []
        runner = backfill._run_fundamentals
    else:
        for symbol in broken:
            state.market_cap_by_symbol[symbol] = []
        client.get_historical_market_cap.return_value = []
        runner = backfill._run_mcap
    args = Namespace(from_date="2026-01-20", to_date="2026-01-23")
    report = {"stages": {}, "planned_network_calls": []}
    runner(args, state, universe, client, None, report)
    assert report["stages"][stage]["incomplete"] == broken


@pytest.mark.parametrize("write", [False, True])
def test_invalid_provider_cap_range_preserves_prior_and_continues_other_symbols(tmp_path, write):
    symbols = ["HONA", "A", "B", "C", "D"]
    prior = [{"symbol": "HONA", "date": "2026-01-15", "market_cap": 1000.0}]
    state = BackfillState(trading_dates=["2026-01-20", "2026-01-23"],
        market_cap_by_symbol={"HONA": list(prior)})
    client = Mock()
    client.get_historical_market_cap.side_effect = lambda symbol, **kwargs: [
        {"symbol": symbol, "date": "2026-01-20", "market_cap": 0 if symbol == "HONA" else 2000}]
    store = backfill.MarketStore(tmp_path / "market.db") if write else None
    if store:
        store.upsert_historical_market_cap("HONA", prior)
    report = {"stages": {}, "planned_network_calls": []}
    try:
        backfill._run_mcap(Namespace(from_date="2026-01-20", to_date="2026-01-23"),
            state, symbols, client, store, report, fuse_on_incompleteness=False)
        assert state.market_cap_by_symbol["HONA"] == prior
        assert set(report["stages"]["mcap"]["invalid_responses"]) == {"HONA"}
        assert report["stages"]["mcap"]["fetched"] == 4
        assert report["stages"]["mcap"]["empty_responses"] == []
        if store:
            rows = store.get_historical_market_cap_range("HONA", "2026-01-01", "2026-01-31")
            assert [(r["date"], r["market_cap"]) for r in rows] == [("2026-01-15", 1000)]
    finally:
        if store:
            store.close()


def test_invalid_provider_caps_count_toward_attempted_request_fuse():
    state = BackfillState(trading_dates=["2026-01-20"])
    client = Mock()
    client.get_historical_market_cap.side_effect = lambda symbol, **kwargs: [
        {"symbol": symbol, "date": "2026-01-20", "market_cap": 0}]
    report = {"stages": {}, "planned_network_calls": []}
    with pytest.raises(RuntimeError, match="failure fuse"):
        backfill._run_mcap(Namespace(from_date="2026-01-20", to_date="2026-01-23"),
            state, ["A", "B"], client, None, report, fuse_on_incompleteness=False)
    assert set(report["stages"]["mcap"]["invalid_responses"]) == {"A", "B"}
    assert not state.market_cap_by_symbol


def test_mcap_store_failure_is_not_misclassified_as_bad_vendor_data():
    state = BackfillState(trading_dates=["2026-01-20"])
    client, store = Mock(), Mock()
    client.get_historical_market_cap.return_value = [
        {"symbol": "A", "date": "2026-01-20", "market_cap": 1000}]
    store.replace_historical_market_cap_range.side_effect = sqlite3.OperationalError("disk full")
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        backfill._run_mcap(Namespace(from_date="2026-01-20", to_date="2026-01-23"),
            state, ["A"], client, store, {"stages": {}}, fuse_on_incompleteness=False)
    assert not state.market_cap_by_symbol


@pytest.mark.parametrize('fetched,calendar_end,deferred', [
    ('2026-09-19T04:00:00+00:00', '2026-09-18', True),
    ('2026-09-21T15:00:00+00:00', '2026-09-18', True),
    ('2026-09-21T20:00:00+00:00', '2026-09-18', False),
    ('2026-09-22T02:00:00+00:00', '2026-09-18', False),
    ('2026-09-19T04:00:00+00:00', '2026-09-10', False),
    ('2026-09-26T04:00:00+00:00', '2026-09-21', False),
])
def test_live_rebalance_calendar_boundary(monkeypatch, fetched, calendar_end, deferred):
    from datetime import datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(fetched)

    monkeypatch.setattr(backfill, 'datetime', Clock)
    state = _state()
    state.trading_dates += ['2026-09-10']
    if calendar_end >= '2026-09-18':
        state.trading_dates.append('2026-09-18')
    if calendar_end >= '2026-09-21':
        state.trading_dates.append('2026-09-21')
    client = Mock()
    client.get_fund_disclosure_dates.return_value = [
        {'date': '2025-12-31', 'year': 2025, 'quarter': 4}]
    client.get_etf_holdings.return_value = [
        {'asset': row['symbol'], 'name': row['name'], 'weightPercentage': 4.0,
         'marketValue': 40.0} for row in state.snapshots]
    report = {'stages': {}}
    args = Namespace(from_date='2026-01-01', to_date=fetched[:10], refresh_live=False)
    if not deferred and calendar_end < '2026-09-21':
        with pytest.raises(ValueError, match='no trading date after'):
            backfill._fetch_sources(args, state, client, None, report)
        return
    backfill._fetch_sources(args, state, client, None, report)
    if deferred:
        client.get_etf_holdings.assert_not_called()
        info = report['stages']['source']['live_deferred']
        assert info == {'rebalance_close_date': '2026-09-18',
                        'expected_first_session': '2026-09-21',
                        'expected_close_utc': '2026-09-21T20:00:00Z',
                        'fetched_at': fetched.replace('+00:00', 'Z'),
                        'calendar_end': calendar_end}
        assert all(row['source_kind'] != 'live' for row in state.snapshots)
        assert report['stages']['source']['skipped'] == 2
    else:
        client.get_etf_holdings.assert_called_once_with('SOXX')
        live = [r for r in state.snapshots if r['source_kind'] == 'live']
        assert live and {r['composition_effective_date'] for r in live} == {'2026-09-21'}
        assert 'live_deferred' not in report['stages']['source']
