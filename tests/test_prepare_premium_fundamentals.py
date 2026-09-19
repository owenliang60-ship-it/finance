"""Repair only missing/stale derived inputs before the weekly Premium gate."""
import importlib

import pytest

from src.data.market_store import MarketStore


@pytest.fixture
def module():
    return importlib.import_module("scripts.prepare_premium_fundamentals")


@pytest.fixture
def store(tmp_path):
    s = MarketStore(tmp_path / "market.db")
    yield s
    s.close()


def test_targets_include_extended_gaps_but_preserve_failed_and_structural_states(module, store, monkeypatch):
    symbols = ["NEW", "LAG", "GOOD", "SHORT", "FAILED", "EMPTY"]
    monkeypatch.setattr(module, "current_base_universe", lambda store: symbols)
    for symbol in ["LAG", "GOOD", "SHORT", "FAILED", "OUTSIDE"]:
        store.upsert_income(symbol, [{"date": "2026-07-31", "period": "Q1", "fiscalYear": "2027"}])
    for symbol in ["GOOD", "SHORT"]:
        store.upsert_metrics(symbol, [{"date": "2026-07-31"}])
    store.upsert_metrics("LAG", [{"date": "2026-04-30"}])
    store.upsert_coverage_status([
        {"symbol": symbol, "dataset": "income_quarterly", "status": status}
        for symbol, status in [("LAG", "ok"), ("GOOD", "ok"), ("SHORT", "ok"),
                               ("FAILED", "fetch_failed"), ("EMPTY", "provider_empty")]
    ])
    assert module.find_repair_targets(store) == {"LAG": "metrics_not_current", "NEW": "never_collected"}


def test_repair_collects_complete_statements_before_computing_metrics(module, store, monkeypatch):
    monkeypatch.setattr(module, "find_repair_targets", lambda store: {"LAG": "metrics_not_current"})
    calls = []

    def collect(symbol, **kwargs):
        calls.append(("collect", symbol, kwargs["limit_quarters"]))
        assert "T" in kwargs["observed_at"]
        return {key: "ok" for key in ("profile", "income", "balance", "cashflow", "ratios")}

    def compute(symbols, **kwargs):
        calls.append(("compute", symbols))
        return {"LAG": 8}

    monkeypatch.setattr(module, "collect_fundamentals_for_symbol", collect)
    monkeypatch.setattr(module, "compute_all_metrics", compute)
    result = module.repair_inputs(store, client=object(), max_targets=50)
    assert result["failed"] == []
    assert calls == [("collect", "LAG", 8), ("compute", ["LAG"])]


def test_target_cap_blocks_all_calls_before_writes(module, store, monkeypatch):
    monkeypatch.setattr(module, "find_repair_targets", lambda store: {"A": "never_collected", "B": "never_collected"})
    monkeypatch.setattr(module, "collect_fundamentals_for_symbol", lambda *a, **k: pytest.fail("provider called"))
    with pytest.raises(RuntimeError, match="target cap"):
        module.repair_inputs(store, client=object(), max_targets=1)


def test_failed_collection_does_not_compute_from_partial_statements(module, store, monkeypatch):
    monkeypatch.setattr(module, "find_repair_targets", lambda store: {"BAD": "metrics_not_current"})
    monkeypatch.setattr(module, "collect_fundamentals_for_symbol", lambda *a, **k: {
        "profile": "ok", "income": "ok", "balance": "fetch_failed", "cashflow": "ok", "ratios": "ok",
    })
    monkeypatch.setattr(module, "compute_all_metrics", lambda *a, **k: pytest.fail("computed partial data"))
    result = module.repair_inputs(store, client=object(), max_targets=50)
    assert result["failed"] == ["BAD"]


def test_cli_lock_busy_exits_before_opening_writable_store(module, monkeypatch):
    class BusyLock:
        def acquire(self):
            return False
    monkeypatch.setattr(module, "FileLock", BusyLock)
    monkeypatch.setattr(module, "MarketStore", lambda **k: pytest.fail("database opened"))
    assert module.main(["--apply"]) == 75


def test_cli_report_only_is_read_only(module, store, monkeypatch):
    modes = []
    def open_store(**kwargs):
        modes.append(kwargs)
        return store
    monkeypatch.setattr(module, "MarketStore", open_store)
    monkeypatch.setattr(module, "find_repair_targets", lambda store: {"NEW": "never_collected"})
    monkeypatch.setattr(module, "repair_inputs", lambda *a, **k: pytest.fail("repair called"))
    assert module.main([]) == 0
    assert modes == [{"read_only": True}]


def test_real_kernels_restore_new_quarter_and_gate_without_changing_raw_history(module, store, monkeypatch):
    from terminal.selection_compass import build_premium_pool
    monkeypatch.setattr(module, "current_base_universe", lambda store: ["EXT"])
    rows = [
        {"date": d, "period": q, "fiscalYear": y, "revenue": revenue,
         "epsDiluted": eps, "netIncome": revenue / 3}
        for d, q, y, revenue, eps in [
            ("2026-06-30", "Q2", "2026", 150, 1.3),
            ("2026-03-31", "Q1", "2026", 130, 1.0),
            ("2025-12-31", "Q4", "2025", 120, 1.0),
            ("2025-09-30", "Q3", "2025", 100, 1.0),
            ("2025-06-30", "Q2", "2025", 95, 1.0),
        ]
    ]
    store.upsert_income("EXT", rows)
    store.upsert_metrics("EXT", [{"date": "2026-03-31"}])
    store.upsert_coverage_status([{"symbol": "EXT", "dataset": "income_quarterly", "status": "ok"}])
    original = store.get_income("EXT", limit=20)
    kwargs = dict(store=store, symbols=["EXT"], as_of="2026-09-18", beta_observations={"EXT": 1.5})
    assert build_premium_pool(**kwargs)["reason"] == "fundamental_coverage_below_threshold"
    class Client:
        def get_dataset_with_status(self, kind, symbol, **kwargs):
            if kind == "profile":
                return [{"symbol": symbol, "companyName": "Extended"}], "ok"
            return rows, "ok"
    result = module.repair_inputs(store, client=Client(), max_targets=50)
    assert result["failed"] == []
    assert store.get_income("EXT", limit=20) == original
    restored = build_premium_pool(**kwargs)
    assert restored["available"] is True
    assert restored["coverage"]["fundamental_ready"]["ratio"] == 1.0
    assert restored["members"][0]["symbol"] == "EXT"
    assert restored["members"][0]["revenue_cagr_4q"] == pytest.approx(1.5 ** (1 / 3) - 1)
    assert module.find_repair_targets(store) == {}


def test_no_lock_cannot_bypass_writer_lock(module, monkeypatch):
    monkeypatch.delenv("FINANCE_CRON_RESOURCE_KEY", raising=False)
    monkeypatch.setattr(module, "MarketStore", lambda **k: pytest.fail("database opened"))
    with pytest.raises(SystemExit):
        module.main(["--apply", "--no-lock"])
