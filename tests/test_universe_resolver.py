"""Tests for T7 unified universe resolver (R1-R3, R13)."""
import pytest

from src.data.market_store import MarketStore
from src.data.universe_resolver import resolve_universe, current_base_universe


@pytest.fixture
def tmp_store(tmp_path):
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


def _sm_row(symbol, cik, company_name, eligible=1, reason="ok"):
    """security_master row factory (matches T3 upsert_security_master column set)."""
    return dict(symbol=symbol, cik=cik, company_name=company_name,
                exchange="NASDAQ", is_etf=0, is_fund=0, is_adr=0,
                share_class_of=None, eligible=eligible, reason=reason,
                updated_at="2026-08-19")


# ---------------------------------------------------------------------------
# resolve_universe: v1 five cases + fail-loud propagation
# ---------------------------------------------------------------------------

def test_empty_sm_propagates_runtime_error():
    def boom():
        raise RuntimeError("security_master empty — run bootstrap first")
    with pytest.raises(RuntimeError, match="bootstrap"):
        resolve_universe(symbol_loader=lambda: ["AAPL"], eligibility_loader=boom,
                         overlay_loaders={})


def test_base_extended_filters_ineligible():
    result = resolve_universe(
        symbol_loader=lambda: ["AAPL", "BADCO"],
        eligibility_loader=lambda: {"AAPL": True, "BADCO": False},
        overlay_loaders={},
    )
    assert result.symbols == ("AAPL",)
    assert result.provenance == {"AAPL": "base"}


def test_overlay_adds_with_provenance_and_no_base_pollution():
    result = resolve_universe(
        overlays=("holdings",),
        symbol_loader=lambda: ["AAPL", "MSFT"],
        eligibility_loader=lambda: {"AAPL": True, "MSFT": True},
        overlay_loaders={"holdings": lambda: ["MSFT", "TSLA"]},
    )
    assert result.symbols == ("AAPL", "MSFT", "TSLA")
    assert result.provenance["AAPL"] == "base"
    assert result.provenance["MSFT"] == "base"          # dup with overlay: base wins, no pollution
    assert result.provenance["TSLA"] == "overlay:holdings"


def test_base_none_is_pure_overlay():
    def boom(*a, **k):
        raise AssertionError("base symbol/eligibility loaders must not be called when base='none'")

    result = resolve_universe(
        base="none",
        overlays=("benchmarks",),
        symbol_loader=boom,
        eligibility_loader=boom,
        overlay_loaders={"benchmarks": lambda: ["SPY", "QQQ"]},
    )
    assert result.base == "none"
    assert result.symbols == ("QQQ", "SPY")
    assert result.provenance == {"QQQ": "overlay:benchmarks", "SPY": "overlay:benchmarks"}


def test_unknown_overlay_raises():
    with pytest.raises(ValueError):
        resolve_universe(base="none", overlays=("nope",), overlay_loaders={})


def test_symbols_sorted_dedup():
    result = resolve_universe(
        overlays=("watchlist",),
        symbol_loader=lambda: ["msft", "aapl", "aapl"],
        eligibility_loader=lambda: {"MSFT": True, "AAPL": True},
        overlay_loaders={"watchlist": lambda: ["aapl", "tsla"]},
    )
    assert result.symbols == ("AAPL", "MSFT", "TSLA")


def test_unknown_base_raises():
    with pytest.raises(ValueError):
        resolve_universe(base="core")


# ---------------------------------------------------------------------------
# current_base_universe: DB SSOT, never extended_universe.json
# ---------------------------------------------------------------------------

def test_current_base_universe_reads_db_not_json(tmp_store, monkeypatch):
    tmp_store.upsert_security_master([
        _sm_row("AAPL", "0000320193", "Apple Inc."),
        _sm_row("NEWCO", "0000000002", "Newco Inc."),
    ])
    tmp_store.record_membership_snapshot(["AAPL", "NEWCO"], as_of="2026-08-19")

    def boom(*a, **k):
        raise AssertionError("current_base_universe must read DB SSOT, not extended_universe.json")
    monkeypatch.setattr(
        "src.data.extended_universe_manager.get_extended_symbols", boom
    )

    result = current_base_universe(store=tmp_store)
    assert set(result) == {"AAPL", "NEWCO"}


def test_current_base_universe_intersects_membership_and_eligibility(tmp_store):
    tmp_store.upsert_security_master([
        _sm_row("AAPL", "0000320193", "Apple Inc.", eligible=1, reason="ok"),
        _sm_row("SOXX", "0000000003", "iShares Semi ETF", eligible=0, reason="etf"),
    ])
    tmp_store.record_membership_snapshot(["AAPL", "SOXX"], as_of="2026-08-19")
    assert current_base_universe(store=tmp_store) == ["AAPL"]


def test_current_base_universe_empty_membership_raises(tmp_store):
    tmp_store.upsert_security_master([_sm_row("AAPL", "0000320193", "Apple Inc.")])
    with pytest.raises(RuntimeError, match="bootstrap"):
        current_base_universe(store=tmp_store)
