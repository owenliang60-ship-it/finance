from datetime import date

import pytest

import scripts.refresh_stock_splits as mod
from scripts.refresh_stock_splits import calendar_events, refresh_splits
from src.data.market_store import MarketStore


class Client:
    def __init__(self, calendar, splits, fail=()):
        self.calendar = calendar
        self.splits = splits
        self.fail = set(fail)
        self.windows = []
        self.fetched = []

    def get_splits_calendar(self, start, end):
        self.windows.append((start, end))
        return [r for r in self.calendar if start <= r["date"] <= end]

    def get_stock_splits(self, symbol):
        self.fetched.append(symbol)
        if symbol in self.fail:
            raise RuntimeError("boom")
        return self.splits.get(symbol, [])


def _split(symbol, day, num=2.0, den=1.0):
    return {"symbol": symbol, "date": day, "numerator": num, "denominator": den,
            "splitType": "stock-split"}


def _run(store, client, base, checked=None, **kw):
    return refresh_splits(store, client, days=7, today=date(2026, 10, 25),
                          base_symbols=base, checked=set() if checked is None else checked, **kw)


def test_calendar_is_read_in_seven_day_windows():
    client = Client([_split("A", "2026-10-02"), _split("B.C", "2026-10-20")], {})
    events = calendar_events(client, date(2026, 10, 1), date(2026, 10, 20))
    assert events == {"A": {"2026-10-02"}, "B-C": {"2026-10-20"}}
    assert client.windows == [("2026-10-01", "2026-10-07"), ("2026-10-08", "2026-10-14"),
                              ("2026-10-15", "2026-10-20")]


def test_truncated_window_fails_loudly(monkeypatch):
    monkeypatch.setattr(mod, "WINDOW_ROW_LIMIT", 2)
    client = Client([_split(s, "2026-10-02") for s in "ABC"], {})
    with pytest.raises(RuntimeError, match="truncated"):
        calendar_events(client, date(2026, 10, 1), date(2026, 10, 7))


def test_only_tracked_symbols_are_refreshed(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.replace_stock_splits("OLD", [_split("OLD", "2020-01-01")])
    client = Client(
        [_split("KLAC", "2026-10-20", 10.0), _split("OLD", "2026-10-21"), _split("FOREIGN.HK", "2026-10-21")],
        {"KLAC": [_split("KLAC", "2026-10-20", 10.0)],
         "OLD": [_split("OLD", "2020-01-01"), _split("OLD", "2026-10-21")]})
    result = _run(store, client, ["KLAC"])
    assert result["targets"] == ["KLAC", "OLD"]
    assert result["failed"] == {}
    assert [r["date"] for r in store.get_stock_splits("OLD")] == ["2020-01-01", "2026-10-21"]


def test_share_class_notation_matches(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    client = Client([_split("BRK.B", "2026-10-20")], {"BRK-B": [_split("BRK-B", "2026-10-20")]})
    result = _run(store, client, ["BRK-B"], max_new=0)
    assert result["targets"] == ["BRK-B"]
    assert result["refreshed"] == {"BRK-B": 1}


def test_empty_reply_never_wipes_history(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.replace_stock_splits("KLAC", [_split("KLAC", "2024-01-01")])
    client = Client([_split("KLAC", "2026-10-20", 10.0)], {"KLAC": []})
    result = _run(store, client, ["KLAC"])
    assert "KLAC" in result["failed"]
    assert [r["date"] for r in store.get_stock_splits("KLAC")] == ["2024-01-01"]


def test_stale_history_without_calendar_event_fails(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    client = Client([_split("KLAC", "2026-10-20", 10.0)], {"KLAC": [_split("KLAC", "2024-01-01")]})
    result = _run(store, client, ["KLAC"], max_new=0)
    assert "calendar split" in result["failed"]["KLAC"]
    assert store.get_stock_splits("KLAC") == []


def test_history_that_drops_stored_rows_is_refused(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.replace_stock_splits("A", [_split("A", "2020-01-01"), _split("A", "2022-01-01")])
    client = Client([_split("A", "2026-10-20")], {"A": [_split("A", "2026-10-20")]})
    result = _run(store, client, ["A"])
    assert "drops stored" in result["failed"]["A"]
    assert len(store.get_stock_splits("A")) == 2


def test_new_base_symbols_get_full_history_once(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    client = Client([], {"NEW": [_split("NEW", "2024-05-01")], "NONE": []})
    checked = set()
    result = _run(store, client, ["NEW", "NONE", "LATER"], checked=checked, max_new=2)
    assert client.fetched == ["LATER", "NEW"]          # sorted, capped at max_new
    assert result["coverage_fetched"] == 2 and result["coverage_remaining"] == 1
    assert checked == {"LATER", "NEW"}
    assert [r["date"] for r in store.get_stock_splits("NEW")] == ["2024-05-01"]
    client.fetched.clear()
    _run(store, client, ["NEW", "NONE", "LATER"], checked=checked, max_new=5)
    assert client.fetched == ["NONE"]                   # only the one never checked
    assert checked == {"LATER", "NEW", "NONE"}


def test_symbol_failure_is_reported_and_others_still_refresh(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    client = Client([_split("A", "2026-10-20"), _split("B", "2026-10-20")],
                    {"B": [_split("B", "2026-10-20")]}, fail={"A"})
    result = _run(store, client, ["A", "B"])
    assert set(result["failed"]) == {"A"}
    assert result["refreshed"] == {"B": 1}


def test_checked_state_round_trips(tmp_path):
    path = tmp_path / "checked.json"
    assert mod.load_checked(path) == set()
    mod.save_checked(path, {"B", "A"})
    assert mod.load_checked(path) == {"A", "B"}
