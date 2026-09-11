"""Premium Pool membership and atomic weekly artifact contracts."""
from datetime import datetime, timezone
import json

import pytest
import pandas as pd

from src.data.premium_pool import (
    PREMIUM_POOL_NAME,
    build_artifact,
    load_premium_pool,
    publish_premium_pool,
)
from terminal.selection_compass import build_premium_pool, scan_selection_compass


def income_rows():
    return [
        {"date": "2026-06-30", "fiscal_year": "2026", "period": "Q2",
         "eps_diluted": 1.20, "revenue": 150, "net_income": 45},
        {"date": "2026-03-31", "fiscal_year": "2026", "period": "Q1",
         "eps_diluted": 1.00, "revenue": 130, "net_income": 35},
        {"date": "2025-12-31", "fiscal_year": "2025", "period": "Q4",
         "eps_diluted": 1.00, "revenue": 120, "net_income": 32},
        {"date": "2025-09-30", "fiscal_year": "2025", "period": "Q3",
         "eps_diluted": 1.00, "revenue": 100, "net_income": 30},
        {"date": "2025-06-30", "fiscal_year": "2025", "period": "Q2",
         "eps_diluted": 1.00, "revenue": 95, "net_income": 25},
    ]


class Store:
    def __init__(self, symbols):
        self.incomes = {symbol: income_rows() for symbol in symbols}
        self.metrics = {symbol: [{
            "date": "2026-06-30", "revenue_cagr_4q": 0.10,
            "net_income_cagr_4q": 0.10,
        }] for symbol in symbols}
        self.coverage = {symbol: "ok" for symbol in symbols}

    def get_income(self, symbol, limit=20):
        return self.incomes.get(symbol, [])[:limit]

    def get_metrics(self, symbol, limit=8):
        return self.metrics.get(symbol, [])[:limit]

    def get_coverage(self, dataset):
        assert dataset == "income_quarterly"
        return self.coverage


def test_membership_uses_only_fundamental_and_beta_gates():
    result = build_premium_pool(
        store=Store(["EXACT", "LOW"]), symbols=["EXACT", "LOW"],
        as_of="2026-09-04", beta_observations={"EXACT": 1.35, "LOW": 1.349},
    )
    assert result["available"] is True
    assert [row["symbol"] for row in result["members"]] == ["EXACT"]
    assert result["members"][0]["beta_6m"] == 1.35
    assert set(result["coverage"]) == {"fundamental_ready", "beta_ready"}


def test_membership_coverage_below_95_percent_fails_closed():
    symbols = [f"S{i:02d}" for i in range(20)]
    result = build_premium_pool(
        store=Store(symbols), symbols=symbols, as_of="2026-09-04",
        beta_observations={symbol: 1.50 for symbol in symbols[:18]},
    )
    assert result["available"] is False
    assert result["reason"] == "beta_coverage_below_threshold"
    assert result["members"] == []


def valid_artifact(members=None, generated_at="2026-09-06T02:00:00Z"):
    result = {
        "available": True,
        "reason": None,
        "coverage": {
            "fundamental_ready": {"covered": 19, "total": 20, "ratio": 0.95},
            "beta_ready": {"covered": 20, "total": 20, "ratio": 1.0},
        },
        "members": members if members is not None else [{
            "symbol": "AAA", "beta_6m": 1.35,
            "quarter_date": "2026-06-30",
            "eps_yoy_growth": 0.20, "eps_yoy_turnaround": False,
            "eps_qoq_growth": 0.20, "eps_qoq_turnaround": False,
            "revenue_cagr_4q": 0.10, "net_income_cagr_4q": 0.10,
            "growth_route": "cagr", "growth_avg_4q": 0.10,
        }],
    }
    return build_artifact(
        result=result, as_of="2026-09-04",
        universe_symbols=["AAA"] + [f"S{i:02d}" for i in range(19)],
        generated_at=generated_at,
    )


@pytest.mark.parametrize("members", [[], None])
def test_atomic_artifact_roundtrip_supports_zero_members(tmp_path, members):
    artifact = valid_artifact(members=members)
    path = tmp_path / "premium_pool.json"
    publish_premium_pool(artifact, path)
    loaded = load_premium_pool(
        path, now=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    assert loaded["available"] is True
    assert loaded["name"] == PREMIUM_POOL_NAME
    assert loaded["members"] == (members if members is not None else artifact["members"])
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: p.update(name="Wrong"), "premium_pool_invalid"),
        (lambda p: p["criteria"].update(beta_6m=1.0), "premium_pool_criteria_mismatch"),
        (lambda p: p["members"].append(dict(p["members"][0])), "premium_pool_invalid"),
        (lambda p: p["members"][0].update(beta_6m=1.349), "premium_pool_invalid"),
        (lambda p: p["members"][0].update(eps_yoy_growth=0.199), "premium_pool_invalid"),
        (lambda p: p["members"][0].update(growth_avg_4q=0.099), "premium_pool_invalid"),
        (lambda p: p["members"][0].update(growth_route="unknown"), "premium_pool_invalid"),
        (lambda p: p["coverage"]["beta_ready"].update(
            covered=18, ratio=0.90), "premium_pool_invalid"),
    ],
)
def test_loader_rejects_wrong_schema_criteria_duplicates_and_invalid_members(
    tmp_path, mutate, reason
):
    artifact = valid_artifact()
    mutate(artifact)
    path = tmp_path / "premium_pool.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    loaded = load_premium_pool(
        path, now=datetime(2026, 9, 6, 12, tzinfo=timezone.utc),
    )
    assert loaded == {"available": False, "reason": reason, "members": []}


def test_loader_rejects_stale_snapshot(tmp_path):
    path = tmp_path / "premium_pool.json"
    path.write_text(json.dumps(valid_artifact(generated_at="2026-08-28T02:00:00Z")),
                    encoding="utf-8")
    assert load_premium_pool(
        path, now=datetime(2026, 9, 6, tzinfo=timezone.utc),
    ) == {"available": False, "reason": "premium_pool_stale", "members": []}


def test_atomic_publish_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "premium_pool.json"
    old = valid_artifact(generated_at="2026-09-05T02:00:00Z")
    path.write_text(json.dumps(old), encoding="utf-8")
    import src.data.premium_pool as module

    def fail_replace(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        publish_premium_pool(valid_artifact(), path)
    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert list(tmp_path.glob(".premium_pool.*.tmp")) == []


def premium_payload(symbols):
    return {
        "available": True, "reason": None, "name": PREMIUM_POOL_NAME,
        "as_of": "2026-09-04", "generated_at": "2026-09-06T02:00:00Z",
        "coverage": {
            "fundamental_ready": {"covered": 20, "total": 20, "ratio": 1.0},
            "beta_ready": {"covered": 20, "total": 20, "ratio": 1.0},
        },
        "members": [{
            "symbol": symbol, "quarter_date": "2026-06-30", "beta_6m": 1.50,
            "eps_yoy_growth": 0.20, "eps_yoy_turnaround": False,
            "eps_qoq_growth": 0.20, "eps_qoq_turnaround": False,
            "revenue_cagr_4q": 0.10, "net_income_cagr_4q": 0.10,
            "growth_route": "cagr", "growth_avg_4q": 0.10,
        } for symbol in symbols],
    }


def price_frame(last_close=110.0):
    return pd.DataFrame({
        "date": pd.bdate_range(end="2026-09-04", periods=40),
        "close": [100.0] * 39 + [last_close],
    })


def test_daily_compass_is_premium_intersect_ema_without_rvol():
    result = scan_selection_compass(
        premium_pool=premium_payload(["ABOVE", "BELOW"]), as_of="2026-09-04",
        price_frames={"ABOVE": price_frame(110), "BELOW": price_frame(90)},
        market_cap_observations={
            "ABOVE": {"date": "2026-09-04", "market_cap": 20e9},
            "BELOW": {"date": "2026-09-04", "market_cap": 30e9},
        },
    )
    assert result["available"] is True
    assert [row["symbol"] for row in result["hits"]] == ["ABOVE"]
    assert result["hits"][0]["close"] == 110
    assert result["hits"][0]["ema30"] == pytest.approx(100 + 20 / 31)
    assert "rvol_ready" not in result["coverage"]
    assert result["premium_pool_as_of"] == "2026-09-04"


@pytest.mark.parametrize("covered,available", [(19, True), (18, False)])
def test_daily_compass_ema_coverage_uses_premium_denominator(covered, available):
    symbols = [f"S{i:02d}" for i in range(20)]
    frames = {symbol: price_frame() for symbol in symbols[:covered]}
    result = scan_selection_compass(
        premium_pool=premium_payload(symbols), as_of="2026-09-04",
        price_frames=frames, market_cap_observations={
            symbol: {"date": "2026-09-04", "market_cap": 10e9} for symbol in symbols
        },
    )
    assert result["available"] is available
    assert result["coverage"]["ema30_ready"] == {
        "covered": covered, "total": 20, "ratio": covered / 20,
    }
    if not available:
        assert result["reason"] == "ema30_coverage_below_threshold"


def test_daily_compass_preserves_member_fields_and_sorts_market_cap():
    result = scan_selection_compass(
        premium_pool=premium_payload(["SMALL", "BIG"]), as_of="2026-09-04",
        price_frames={"SMALL": price_frame(), "BIG": price_frame()},
        market_cap_observations={
            "SMALL": {"date": "2026-09-04", "marketCap": 10e9},
            "BIG": {"date": "2026-09-04", "marketCap": 100e9},
        },
    )
    assert [row["symbol"] for row in result["hits"]] == ["BIG", "SMALL"]
    assert result["hits"][0]["beta_6m"] == 1.50
    assert result["hits"][0]["growth_avg_4q"] == 0.10


def test_daily_compass_propagates_unavailable_pool_without_partial_rows():
    result = scan_selection_compass(
        premium_pool={"available": False, "reason": "premium_pool_stale", "members": []},
        as_of="2026-09-04", price_frames={}, market_cap_observations={},
    )
    assert result["available"] is False
    assert result["reason"] == "premium_pool_stale"
    assert result["hits"] == []
