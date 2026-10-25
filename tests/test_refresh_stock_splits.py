from datetime import date

import pytest

from scripts.refresh_stock_splits import calendar_symbols, refresh_splits
from src.data.market_store import MarketStore


class Client:
    def __init__(self, calendar, splits, fail=()):
        self.calendar = calendar
        self.splits = splits
        self.fail = set(fail)
        self.windows = []

    def get_splits_calendar(self, start, end):
        self.windows.append((start, end))
        return [r for r in self.calendar if start <= r["date"] <= end]

    def get_stock_splits(self, symbol):
        if symbol in self.fail:
            raise RuntimeError("boom")
        return self.splits[symbol]


def _split(symbol, day, num=2.0, den=1.0):
    return {"symbol": symbol, "date": day, "numerator": num, "denominator": den,
            "splitType": "stock-split"}


def test_calendar_is_read_in_seven_day_windows():
    client = Client([_split("A", "2026-10-02"), _split("B", "2026-10-20")], {})
    assert calendar_symbols(client, date(2026, 10, 1), date(2026, 10, 20)) == {"A", "B"}
    assert client.windows == [("2026-10-01", "2026-10-07"), ("2026-10-08", "2026-10-14"),
                              ("2026-10-15", "2026-10-20")]


def test_truncated_window_fails_loudly(monkeypatch):
    import scripts.refresh_stock_splits as mod
    monkeypatch.setattr(mod, "WINDOW_ROW_LIMIT", 2)
    client = Client([_split(s, "2026-10-02") for s in "ABC"], {})
    with pytest.raises(RuntimeError, match="truncated"):
        calendar_symbols(client, date(2026, 10, 1), date(2026, 10, 7))


def test_only_tracked_symbols_are_refreshed(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.replace_stock_splits("OLD", [_split("OLD", "2020-01-01")])
    client = Client(
        [_split("KLAC", "2026-10-20", 10.0), _split("OLD", "2026-10-21"), _split("FOREIGN.HK", "2026-10-21")],
        {"KLAC": [_split("KLAC", "2026-10-20", 10.0)],
         "OLD": [_split("OLD", "2020-01-01"), _split("OLD", "2026-10-21")]})
    result = refresh_splits(store, client, days=7, today=date(2026, 10, 25), base_symbols=["KLAC"])
    assert result["targets"] == ["KLAC", "OLD"]
    assert result["failed"] == {}
    assert [r["date"] for r in store.get_stock_splits("KLAC")] == ["2026-10-20"]
    assert [r["date"] for r in store.get_stock_splits("OLD")] == ["2020-01-01", "2026-10-21"]


def test_symbol_failure_is_reported_and_others_still_refresh(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    client = Client([_split("A", "2026-10-20"), _split("B", "2026-10-20")],
                    {"B": [_split("B", "2026-10-20")]}, fail={"A"})
    result = refresh_splits(store, client, days=7, today=date(2026, 10, 25), base_symbols=["A", "B"])
    assert set(result["failed"]) == {"A"}
    assert result["refreshed"] == {"B": 1}
