"""Tests for scripts/backfill_delisted_large_caps.py."""

from scripts.backfill_delisted_large_caps import backfill_symbols


class FakeClient:
    def __init__(self, mcap_rows, price_rows):
        self._mcap_rows = mcap_rows
        self._price_rows = price_rows

    def get_historical_market_cap(self, symbol, from_date, to_date):
        del from_date, to_date
        return self._mcap_rows.get(symbol, [])

    def get_historical_price_range(self, symbol, from_date, to_date):
        del from_date, to_date
        return self._price_rows.get(symbol, [])


class FakeStore:
    def __init__(self):
        self.mcap_calls = []
        self.price_calls = []
        self.delete_calls = []

    def upsert_historical_market_cap(self, symbol, rows):
        self.mcap_calls.append((symbol, rows))
        return len(rows)

    def upsert_daily_prices(self, symbol, rows):
        self.price_calls.append((symbol, rows))
        return len(rows)

    def _get_conn(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def execute(self, sql, params):
        self.delete_calls.append((sql, params))
        return None


def test_backfill_symbols_writes_overlay_for_eligible_names():
    client = FakeClient(
        mcap_rows={
            "TWTR": [{"date": "2022-10-27", "market_cap": 41_179_146_900}],
        },
        price_rows={
            "TWTR": [{"date": "2022-10-27", "close": 53.7, "changePercent": 0.0}],
        },
    )
    store = FakeStore()
    captured = {}

    def overlay_writer(symbols, metadata):
        captured["symbols"] = symbols
        captured["metadata"] = metadata
        return {"symbols": symbols, **metadata}

    summary = backfill_symbols(
        symbols=["TWTR"],
        from_date="2021-07-01",
        to_date="2022-10-31",
        mcap_threshold=10e9,
        client=client,
        store=store,
        overlay_loader=lambda: ["VMW"],
        overlay_writer=overlay_writer,
        candidate_registry_loader=lambda: {"candidates": []},
    )

    assert summary["success"] == ["TWTR"]
    assert summary["below_threshold"] == []
    assert summary["missing_mcap"] == []
    assert summary["missing_price"] == []
    assert captured["symbols"] == ["TWTR", "VMW"]
    assert len(store.mcap_calls) == 1
    assert len(store.price_calls) == 1
    assert summary["details"][0]["effective_to_date"] == "2022-10-31"


def test_backfill_symbols_caps_to_candidate_delisted_date():
    client = FakeClient(
        mcap_rows={
            "TWTR": [{"date": "2022-10-27", "market_cap": 41_179_146_900}],
        },
        price_rows={
            "TWTR": [{"date": "2022-10-27", "close": 53.7, "changePercent": 0.0}],
        },
    )
    store = FakeStore()

    summary = backfill_symbols(
        symbols=["TWTR"],
        from_date="2021-02-03",
        to_date="2026-04-22",
        mcap_threshold=10e9,
        client=client,
        store=store,
        overlay_loader=lambda: [],
        overlay_writer=lambda symbols, metadata: {"symbols": symbols, **metadata},
        candidate_registry_loader=lambda: {
            "candidates": [
                {"symbol": "TWTR", "delisted_date": "2022-10-28"},
            ]
        },
    )

    assert summary["details"][0]["effective_to_date"] == "2022-10-28"


def test_backfill_symbols_dry_run_skips_writes_and_overlay():
    client = FakeClient(
        mcap_rows={
            "TWTR": [{"date": "2022-10-27", "market_cap": 41_179_146_900}],
        },
        price_rows={
            "TWTR": [{"date": "2022-10-27", "close": 53.7, "changePercent": 0.0}],
        },
    )
    store = FakeStore()
    called = {"overlay": False}

    def overlay_writer(symbols, metadata):
        del symbols, metadata
        called["overlay"] = True
        return {}

    summary = backfill_symbols(
        symbols=["TWTR"],
        from_date="2021-07-01",
        to_date="2022-10-31",
        mcap_threshold=10e9,
        dry_run=True,
        client=client,
        store=store,
        overlay_loader=lambda: [],
        overlay_writer=overlay_writer,
    )

    assert summary["success"] == ["TWTR"]
    assert store.mcap_calls == []
    assert store.price_calls == []
    assert called["overlay"] is False


def test_backfill_symbols_tracks_below_threshold_and_missing_price():
    client = FakeClient(
        mcap_rows={
            "FRC": [{"date": "2023-04-28", "market_cap": 649_350_000}],
            "SIVB": [{"date": "2023-03-09", "market_cap": 12_500_000_000}],
        },
        price_rows={
            "FRC": [{"date": "2023-04-28", "close": 3.51, "changePercent": 0.0}],
            "SIVB": [],
        },
    )
    store = FakeStore()

    summary = backfill_symbols(
        symbols=["FRC", "SIVB", "MISSING"],
        from_date="2021-07-01",
        to_date="2023-05-01",
        mcap_threshold=10e9,
        client=client,
        store=store,
        overlay_loader=lambda: [],
        overlay_writer=lambda symbols, metadata: {"symbols": symbols, **metadata},
    )

    assert summary["success"] == []
    assert summary["below_threshold"] == ["FRC"]
    assert summary["missing_price"] == ["SIVB"]
    assert summary["missing_mcap"] == ["MISSING"]
    # FRC and SIVB both had mcap rows, so both should be written to DB.
    assert [call[0] for call in store.mcap_calls] == ["FRC", "SIVB"]


def test_backfill_symbols_replace_existing_purges_before_rewrite():
    client = FakeClient(
        mcap_rows={
            "TWTR": [{"date": "2022-10-27", "market_cap": 41_179_146_900}],
        },
        price_rows={
            "TWTR": [{"date": "2022-10-27", "close": 53.7, "changePercent": 0.0}],
        },
    )
    store = FakeStore()

    backfill_symbols(
        symbols=["TWTR"],
        from_date="2021-02-03",
        to_date="2022-10-31",
        mcap_threshold=10e9,
        replace_existing=True,
        client=client,
        store=store,
        overlay_loader=lambda: [],
        overlay_writer=lambda symbols, metadata: {"symbols": symbols, **metadata},
    )

    assert store.delete_calls == [
        ("DELETE FROM historical_market_cap WHERE symbol = ?", ("TWTR",)),
        ("DELETE FROM daily_price WHERE symbol = ?", ("TWTR",)),
    ]
