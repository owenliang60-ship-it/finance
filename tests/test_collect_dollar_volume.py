"""Tests for scripts/collect_dollar_volume.py."""

from scripts.collect_dollar_volume import (
    fetch_all_stocks,
    fetch_recent_delisted_symbols,
)


class FakeClient:
    def __init__(self, screener_pages=None, delisted_pages=None):
        self.screener_pages = screener_pages or {}
        self.delisted_pages = delisted_pages or {}

    def get_screener_page(self, offset=0, limit=1000, volume_more_than=None):
        del limit
        return self.screener_pages.get((offset, volume_more_than), [])

    def get_delisted_companies(self, page=0, limit=200):
        del limit
        return self.delisted_pages.get(page, [])


def test_fetch_recent_delisted_symbols_only_keeps_recent_rows():
    client = FakeClient(
        delisted_pages={
            0: [
                {"symbol": "HOLX", "delistedDate": "2026-04-08"},
                {"symbol": "TWTR", "delistedDate": "2025-12-01"},
                {"symbol": "BAD", "delistedDate": "not-a-date"},
            ],
        }
    )

    symbols, api_calls = fetch_recent_delisted_symbols(
        client,
        as_of_date="2026-04-23",
        lookback_days=120,
    )

    assert symbols == {"HOLX"}
    assert api_calls == 1


def test_fetch_all_stocks_filters_recently_delisted_symbols():
    holx_row = {
        "symbol": "HOLX",
        "companyName": "Hologic, Inc.",
        "price": 76.01,
        "volume": 101_956_189,
    }
    nvda_row = {
        "symbol": "NVDA",
        "companyName": "NVIDIA Corporation",
        "price": 202.5,
        "volume": 106_421_485,
    }
    pltr_row = {
        "symbol": "PLTR",
        "companyName": "Palantir Technologies Inc.",
        "price": 152.62,
        "volume": 43_460_681,
    }
    client = FakeClient(
        screener_pages={
            (0, None): [holx_row, nvda_row],
            (1000, None): [],
            (0, 500000): [holx_row, pltr_row],
        },
        delisted_pages={
            0: [
                {"symbol": "HOLX", "delistedDate": "2026-04-08"},
            ],
        },
    )

    stocks, api_calls = fetch_all_stocks(client, as_of_date="2026-04-23")
    symbols = {row["symbol"] for row in stocks}

    assert symbols == {"NVDA", "PLTR"}
    assert api_calls == 3
