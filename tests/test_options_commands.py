"""Tests for Options Commands orchestrator + Formatter."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from terminal.company_store import CompanyStore
from src.data.market_store import MarketStore
from terminal.options.commands import prepare_options_context, _get_deep_analysis, _get_oprms
from terminal.options.formatter import (
    format_options_context,
    format_chain_table,
    format_strategy_comparison,
    format_trade_memo,
)


@pytest.fixture(autouse=True)
def _sheet_unavailable_by_default(monkeypatch):
    """No test may hit the network: the Google Sheet is unavailable unless a
    test patches terminal.options.commands.load_sheet_book itself."""
    from portfolio.holdings.sheet_book import SheetBookError

    def _no_sheet(*a, **k):
        raise SheetBookError("PORTFOLIO_SHEET_ID not configured")

    monkeypatch.setattr("terminal.options.commands.load_sheet_book", _no_sheet)


def _fake_book(net_total, holdings=()):
    from types import SimpleNamespace
    return SimpleNamespace(holdings=list(holdings),
                           whole_port_net_total_usd=net_total)


def _fake_holding(symbol, shares, cost):
    from types import SimpleNamespace
    return SimpleNamespace(symbol=symbol, market="US", shares=shares,
                           cost_per_share=cost, sheet_price=cost,
                           market_value=shares * cost, category="Test",
                           is_leaps=False)


@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore for company-dimension data."""
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    s.upsert_company("AAPL", company_name="Apple Inc.", sector="Technology")
    return s


@pytest.fixture
def mkt_store(tmp_path):
    """Create a fresh MarketStore for IV/options data."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    return s


@pytest.fixture
def store_with_data(store, mkt_store):
    """Stores with OPRMS + analysis + IV data."""
    store.save_oprms_rating(
        "AAPL", dna="S", timing="A", timing_coeff=0.9,
        position_pct=18.0, verdict="Strong conviction"
    )
    store.save_analysis("AAPL", {
        "analysis_date": "2026-02-20",
        "executive_summary": "Apple shows strong momentum with AI integration.",
        "debate_verdict": "STRONG BUY",
        "key_forces": ["AI", "Services growth"],
        "price_at_analysis": 200.0,
    })
    mkt_store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.28, hv_30d=0.22)
    mkt_store.save_iv_daily("AAPL", "2026-02-21", iv_30d=0.30, hv_30d=0.24)

    # Add option contracts
    contracts = [
        {
            "expiration": "2026-03-21", "strike": 200, "side": "call",
            "bid": 8.50, "ask": 8.80, "mid": 8.65,
            "volume": 1500, "open_interest": 25000, "iv": 0.28,
            "delta": 0.55, "gamma": 0.03, "theta": -0.15, "vega": 0.25,
            "dte": 25, "in_the_money": True, "underlying_price": 202.50,
        },
        {
            "expiration": "2026-03-21", "strike": 200, "side": "put",
            "bid": 6.00, "ask": 6.30, "mid": 6.15,
            "volume": 800, "open_interest": 18000, "iv": 0.30,
            "delta": -0.45, "gamma": 0.03, "theta": -0.14, "vega": 0.24,
            "dte": 25, "in_the_money": False, "underlying_price": 202.50,
        },
    ]
    mkt_store.save_options_snapshot("AAPL", "2026-02-24", contracts)
    return store, mkt_store


class TestPrepareOptionsContext:
    """Test the main orchestrator."""

    def test_full_context(self, store_with_data):
        """Should return complete context with all fields."""
        company_store, mkt_store = store_with_data
        mock_client = MagicMock()
        mock_client.get_options_chain.return_value = {
            "s": "ok",
            "optionSymbol": ["AAPL260321C00200000"],
            "expiration": ["2026-03-21"],
            "strike": [200],
            "side": ["call"],
            "bid": [8.50], "ask": [8.80], "mid": [8.65],
            "volume": [1500], "openInterest": [25000],
            "iv": [0.28], "delta": [0.55], "gamma": [0.03],
            "theta": [-0.15], "vega": [0.25],
            "dte": [25], "inTheMoney": [True],
            "underlyingPrice": [202.50],
        }
        mock_fmp = MagicMock()
        mock_fmp.get_earnings.return_value = []

        ctx = prepare_options_context(
            "AAPL",
            store=company_store,
            market_store=mkt_store,
            client=mock_client,
            fmp_client=mock_fmp,
        )

        assert ctx["symbol"] == "AAPL"
        assert ctx["underlying_price"] == 202.50
        assert ctx["oprms"]["dna"] == "S"
        assert ctx["deep_analysis"]["executive_summary"] is not None
        assert ctx["iv_summary"] is not None
        assert ctx["liquidity"]["verdict"] != "NO_GO"
        assert ctx["earnings"]["zone"] == "CLEAR"
        assert "formatted_context" in ctx

    def test_skip_chain_fetch(self, store_with_data):
        """Should use existing snapshot when skip_chain_fetch=True."""
        company_store, mkt_store = store_with_data
        mock_fmp = MagicMock()
        mock_fmp.get_earnings.return_value = []
        mock_fmp.get_realtime_price.return_value = 205.0

        ctx = prepare_options_context(
            "AAPL",
            store=company_store,
            market_store=mkt_store,
            fmp_client=mock_fmp,
            skip_chain_fetch=True,
        )

        assert ctx["underlying_price"] == 205.0
        assert ctx["chain_summary"] is None
        assert ctx["liquidity"] is not None

    def test_no_data_symbol(self, store, mkt_store):
        """Should handle symbol with no data gracefully."""
        mock_fmp = MagicMock()
        mock_fmp.get_earnings.return_value = []
        mock_fmp.get_realtime_price.return_value = None

        ctx = prepare_options_context(
            "UNKNOWN",
            store=store,
            market_store=mkt_store,
            fmp_client=mock_fmp,
            skip_chain_fetch=True,
        )

        assert ctx["symbol"] == "UNKNOWN"
        assert ctx["deep_analysis"] == {}
        assert ctx["oprms"] == {}
        assert ctx["iv_summary"] is None


class TestGetDeepAnalysis:
    """Test deep analysis extraction."""

    def test_with_analysis(self, store_with_data):
        company_store, _ = store_with_data
        result = _get_deep_analysis("AAPL", company_store)
        assert result["executive_summary"] is not None
        assert result["debate_verdict"] == "STRONG BUY"
        assert result["price_at_analysis"] == 200.0

    def test_no_analysis(self, store):
        result = _get_deep_analysis("AAPL", store)
        assert result == {}


class TestGetOPRMS:
    """Test OPRMS extraction."""

    def test_with_oprms(self, store_with_data):
        company_store, _ = store_with_data
        result = _get_oprms("AAPL", company_store)
        assert result["dna"] == "S"
        assert result["timing"] == "A"
        assert result["position_pct"] == 18.0

    def test_no_oprms(self, store):
        result = _get_oprms("AAPL", store)
        assert result == {}


class TestFormatOptionsContext:
    """Test the overview formatter."""

    def test_format_basic(self):
        ctx = {
            "symbol": "AAPL",
            "underlying_price": 202.50,
            "oprms": {"dna": "S", "timing": "A", "position_pct": 18.0},
            "iv_summary": {
                "current_iv": 0.28, "iv_rank": 65.0,
                "iv_percentile": 70.0, "hv_30d": 0.22, "rv_iv_ratio": 0.79,
            },
            "liquidity": {"verdict": "EXCELLENT", "avg_spread_pct": 0.03, "avg_oi": 20000},
            "earnings": {"days_to_earnings": None, "zone": "CLEAR"},
            "term_structure": [
                {"expiration": "2026-03-21", "dte": 25, "atm_iv": 0.29, "atm_strike": 200},
            ],
            "kill_conditions": [],
            "deep_analysis": {"executive_summary": "Strong AI play.", "debate_verdict": "BUY"},
        }

        output = format_options_context(ctx)
        assert "AAPL" in output
        assert "$202.50" in output
        assert "DNA=S" in output
        assert "IV Rank" in output
        assert "EXCELLENT" in output
        assert "Term Structure" in output

    def test_format_no_go_warning(self):
        ctx = {
            "symbol": "ILLIQUID",
            "underlying_price": 50.0,
            "oprms": {},
            "iv_summary": None,
            "liquidity": {"verdict": "NO_GO", "avg_spread_pct": None, "avg_oi": None},
            "earnings": {},
            "term_structure": [],
            "kill_conditions": [],
            "deep_analysis": {},
        }

        output = format_options_context(ctx)
        assert "NO_GO" in output
        assert "WARNING" in output


class TestFormatChainTable:
    """Test chain table formatting."""

    def test_format_contracts(self):
        contracts = [
            {
                "strike": 200, "side": "call", "bid": 8.50, "ask": 8.80,
                "mid": 8.65, "iv": 0.28, "delta": 0.55,
                "open_interest": 25000, "volume": 1500,
            },
        ]
        output = format_chain_table(contracts)
        assert "$200" in output
        assert "8.50" in output
        assert "28.0%" in output

    def test_filter_by_side(self):
        contracts = [
            {"strike": 200, "side": "call", "bid": 8.50, "ask": 8.80, "mid": 8.65},
            {"strike": 200, "side": "put", "bid": 6.00, "ask": 6.30, "mid": 6.15},
        ]
        output = format_chain_table(contracts, side="call")
        assert "C" in output
        assert "P" not in output.split("\n")[2]  # Data row should only have C

    def test_empty_contracts(self):
        assert "(No contracts)" in format_chain_table([])


class TestFormatStrategyComparison:
    """Test strategy comparison formatting."""

    def test_format_comparison(self):
        strategies = [
            {
                "name": "Bull Call Spread",
                "structure": "Buy $200C / Sell $210C",
                "net_cost": 3.50,
                "max_profit": 6.50,
                "max_loss": 3.50,
                "breakeven": "$203.50",
                "risk_reward": "1:1.86",
                "delta": 0.35,
                "theta": -0.12,
            },
            {
                "name": "Bull Put Spread",
                "structure": "Sell $200P / Buy $190P",
                "net_cost": -2.00,
                "max_profit": 2.00,
                "max_loss": 8.00,
                "breakeven": "$198.00",
                "risk_reward": "4:1",
                "delta": 0.30,
                "theta": 0.08,
            },
        ]
        output = format_strategy_comparison(strategies)
        assert "Bull Call Spread" in output
        assert "Bull Put Spread" in output
        assert "Strategy Comparison" in output

    def test_empty_strategies(self):
        assert "(No strategies" in format_strategy_comparison([])


class TestFormatTradeMemo:
    """Test trade memo formatting."""

    def test_format_memo(self):
        memo = {
            "symbol": "AAPL",
            "strategy": "Bull Call Spread",
            "structure": "Buy Mar 21 $200C / Sell Mar 21 $210C",
            "net_cost": "$3.50/spread",
            "max_profit": "$6.50",
            "max_loss": "$3.50",
            "breakeven": "$203.50",
            "risk_reward": "1:1.86",
            "contracts": 5,
            "expiry": "2026-03-21",
            "dte": 25,
            "greeks": {"delta": "+0.35", "theta": "-$12/day", "vega": "+$8"},
            "management_plan": [
                "Profit 50-75%: close",
                "Loss 50%: stop",
                "14 DTE: close regardless",
            ],
        }
        output = format_trade_memo(memo)
        assert "TRADE MEMO" in output
        assert "AAPL" in output
        assert "Bull Call Spread" in output
        assert "Management Plan" in output
        assert "14 DTE" in output


# ── Gates, Issue 045, holdings/NAV, skew, Q vs P ──

from datetime import datetime, timedelta

from terminal.options.commands import _get_portfolio_context
from terminal.options.formatter import format_dual_probability_summary


def _set_oprms_created_at(company_store, symbol, created_at):
    conn = company_store._get_conn()
    conn.execute(
        "UPDATE oprms_ratings SET created_at = ? WHERE symbol = ? AND is_current = 1",
        (created_at, symbol),
    )
    conn.commit()


def _fmp(price=None):
    mock_fmp = MagicMock()
    mock_fmp.get_earnings.return_value = []
    mock_fmp.get_realtime_price.return_value = price
    return mock_fmp


class TestOPRMSStaleness:
    def test_fresh_rating_has_created_at(self, store_with_data):
        company_store, _ = store_with_data
        result = _get_oprms("AAPL", company_store)
        assert result["created_at"]
        assert result["is_stale"] is False

    def test_rating_older_than_60_days_is_stale(self, store_with_data):
        company_store, _ = store_with_data
        old = (datetime.now() - timedelta(days=61)).isoformat()
        _set_oprms_created_at(company_store, "AAPL", old)
        assert _get_oprms("AAPL", company_store)["is_stale"] is True

    def test_rating_at_60_days_is_not_stale(self, store_with_data):
        company_store, _ = store_with_data
        edge = (datetime.now() - timedelta(days=60)).isoformat()
        _set_oprms_created_at(company_store, "AAPL", edge)
        assert _get_oprms("AAPL", company_store)["is_stale"] is False

    def test_unparseable_created_at_is_stale(self, store_with_data):
        company_store, _ = store_with_data
        _set_oprms_created_at(company_store, "AAPL", "garbage")
        assert _get_oprms("AAPL", company_store)["is_stale"] is True


class TestPortfolioContext:
    """NAV = Whole Port 投资 Net Total from the read-only Google Sheet.
    Fictional numbers only."""

    def test_sheet_failure_fails_closed(self, store):
        pf = _get_portfolio_context("AAPL", store, 200.0)
        assert pf["holding"] is None
        assert pf["option_legs"] == []
        assert pf["option_legs_note"] == "期权腿未纳入"
        assert pf["estimated_nav"] is None
        assert pf["max_loss_2pct"] is None
        assert pf["max_loss_3pct"] is None
        assert pf["portfolio_status"] == "UNKNOWN"

    def test_whole_port_net_total_is_nav(self, store, monkeypatch):
        book = _fake_book(500000.0, [_fake_holding("AAPL", 100, 150.0),
                                     _fake_holding("MSFT", 10, 400.0)])
        monkeypatch.setattr("terminal.options.commands.load_sheet_book",
                            lambda *a, **k: book)
        pf = _get_portfolio_context("aapl", store, 200.0)
        assert pf["holding"] == {"shares": 100, "avg_cost": 150.0, "open_date": None}
        assert pf["estimated_nav"] == 500000.0
        assert pf["nav_source"] == "whole_port_net_total"
        assert pf["max_loss_2pct"] == 10000.0
        assert pf["max_loss_3pct"] == 15000.0
        assert pf["option_legs"] == []
        assert pf["portfolio_status"] == "OK"

    def test_not_held_symbol(self, store, monkeypatch):
        monkeypatch.setattr("terminal.options.commands.load_sheet_book",
                            lambda *a, **k: _fake_book(500000.0))
        pf = _get_portfolio_context("AAPL", store, 200.0)
        assert pf["holding"] is None
        assert pf["estimated_nav"] == 500000.0

    @pytest.mark.parametrize("net_total", [None, 0.0, -1.0])
    def test_missing_or_bad_net_total_fails_closed(self, store, monkeypatch, net_total):
        book = _fake_book(net_total, [_fake_holding("AAPL", 100, 150.0)])
        monkeypatch.setattr("terminal.options.commands.load_sheet_book",
                            lambda *a, **k: book)
        pf = _get_portfolio_context("AAPL", store, 200.0)
        assert pf["estimated_nav"] is None
        assert pf["max_loss_2pct"] is None
        assert pf["holding"] is None
        assert pf["portfolio_status"] == "UNKNOWN"

    def test_never_reads_company_db_ledger(self, monkeypatch):
        store = MagicMock()
        monkeypatch.setattr("terminal.options.commands.load_sheet_book",
                            lambda *a, **k: _fake_book(500000.0))
        _get_portfolio_context("AAPL", store, 200.0)
        assert store.method_calls == []


class TestContextGates:
    def test_full_context_adds_gates_and_new_blocks(self, store_with_data):
        company_store, mkt_store = store_with_data
        mock_client = MagicMock()
        mock_client.get_options_chain.return_value = {
            "s": "ok",
            "optionSymbol": ["AAPL260321C00200000"],
            "expiration": ["2026-03-21"], "strike": [200], "side": ["call"],
            "bid": [8.50], "ask": [8.80], "mid": [8.65],
            "volume": [1500], "openInterest": [25000],
            "iv": [0.28], "delta": [0.55], "gamma": [0.03],
            "theta": [-0.15], "vega": [0.25],
            "dte": [25], "inTheMoney": [True],
            "underlyingPrice": [202.50],
        }
        ctx = prepare_options_context(
            "AAPL", store=company_store, market_store=mkt_store,
            client=mock_client, fmp_client=_fmp(),
        )
        assert ctx["snapshot_freshness"]["status"] == "FRESH"
        assert ctx["snapshot_freshness"]["snapshot_date"] == datetime.now().strftime("%Y-%m-%d")
        assert ctx["gates"] == {
            "snapshot": "FRESH",
            "liquidity": ctx["liquidity"]["verdict"],
            "earnings": "CLEAR",
        }
        assert "available" in ctx["skew_25d"]
        assert ctx["portfolio"]["estimated_nav"] is None
        assert ctx["expected_move"]["days"] == 30
        assert "Position**:" not in ctx["formatted_context"]
        assert "Actual Holdings" in ctx["formatted_context"]

    def test_skip_chain_fetch_reads_existing_snapshot_date(self, store_with_data):
        company_store, mkt_store = store_with_data
        ctx = prepare_options_context(
            "AAPL", store=company_store, market_store=mkt_store,
            fmp_client=_fmp(205.0), skip_chain_fetch=True,
        )
        assert ctx["snapshot_freshness"]["snapshot_date"] == "2026-02-24"
        assert ctx["snapshot_freshness"]["status"] == "STALE"
        assert ctx["gates"]["snapshot"] == "STALE"

    def test_no_data_symbol_is_missing_everywhere(self, store, mkt_store):
        ctx = prepare_options_context(
            "UNKNOWN", store=store, market_store=mkt_store,
            fmp_client=_fmp(None), skip_chain_fetch=True,
        )
        assert ctx["snapshot_freshness"]["status"] == "MISSING"
        assert ctx["gates"]["snapshot"] == "MISSING"
        assert ctx["skew_25d"]["available"] is False
        assert ctx["expected_move"] is None
        assert ctx["portfolio"]["estimated_nav"] is None

    def test_expected_move_falls_back_to_skew_atm_iv(self, store, mkt_store):
        contracts = [
            {"expiration": "2026-12-31", "strike": 100, "side": "put", "bid": 1, "ask": 1.1,
             "mid": 1.05, "iv": 0.40, "delta": -0.25, "dte": 35, "underlying_price": 100.0},
            {"expiration": "2026-12-31", "strike": 100, "side": "call", "bid": 1, "ask": 1.1,
             "mid": 1.05, "iv": 0.30, "delta": 0.50, "dte": 35, "underlying_price": 100.0},
        ]
        mkt_store.save_options_snapshot("AAPL", "2026-11-26", contracts)
        ctx = prepare_options_context(
            "AAPL", store=store, market_store=mkt_store,
            fmp_client=_fmp(100.0), skip_chain_fetch=True,
        )
        em = ctx["expected_move"]
        assert em["iv_source"] == "skew_25d.atm_iv"
        assert em["iv"] == 0.30
        assert em["move_1sigma"] == round(100.0 * 0.30 * (30 / 365.0) ** 0.5, 2)


def _base_ctx(**over):
    ctx = {
        "symbol": "AAPL",
        "underlying_price": 202.50,
        "oprms": {"dna": "S", "timing": "A", "position_pct": 18.0,
                  "created_at": "2026-11-01T10:00:00", "is_stale": False},
        "earnings": {"days_to_earnings": 40, "earnings_date": "2027-01-05", "zone": "CLEAR"},
    }
    ctx.update(over)
    return ctx


class TestFormatterIssue045AndNewBlocks:
    def test_oprms_position_pct_is_labeled_as_suggested_cap(self):
        out = format_options_context(_base_ctx())
        assert "Position**:" not in out
        assert "Position:" not in out
        assert "OPRMS Suggested Cap (非实际持仓): 18.0%" in out
        assert "as of 2026-11-01" in out
        assert "STALE >60d" not in out

    def test_stale_oprms_is_flagged(self):
        ctx = _base_ctx(oprms={"dna": "A", "timing": "B", "position_pct": 7.5,
                               "created_at": "2026-03-20T10:00:00", "is_stale": True})
        out = format_options_context(ctx)
        assert "OPRMS Suggested Cap (非实际持仓): 7.5%" in out
        assert "⚠️ STALE >60d" in out

    def test_snapshot_freshness_states(self):
        fresh = format_options_context(_base_ctx(snapshot_freshness={
            "status": "FRESH", "snapshot_date": "2026-11-26", "age_days": 0}))
        stale = format_options_context(_base_ctx(snapshot_freshness={
            "status": "STALE", "snapshot_date": "2026-11-20", "age_days": 6}))
        missing = format_options_context(_base_ctx())
        assert "Snapshot Freshness" in fresh and "2026-11-26" in fresh and "FRESH" in fresh
        assert "⚠️ STALE" in stale and "2026-11-20" in stale
        assert "❌ MISSING" in missing

    def test_gates_line(self):
        out = format_options_context(_base_ctx(gates={
            "snapshot": "STALE", "liquidity": "GOOD", "earnings": "T5_WARNING"}))
        assert "Gates" in out
        assert "STALE" in out and "GOOD" in out and "T5_WARNING" in out

    def test_holdings_block_empty_book(self):
        out = format_options_context(_base_ctx(portfolio={
            "holding": None, "option_legs": [], "cash": 0.0,
            "estimated_nav": None, "nav_is_estimated": False,
            "max_loss_2pct": None, "max_loss_3pct": None,
        }))
        assert "Actual Holdings (company.db, 非 Book of Record, 以只读 Google Sheet 为准)" in out
        assert "无持仓记录" in out

    def test_holdings_block_with_positions(self):
        out = format_options_context(_base_ctx(portfolio={
            "holding": {"shares": 100, "avg_cost": 150.0, "open_date": "2026-01-02"},
            "option_legs": [{"expiration": "2026-12-18", "strike": 220.0, "side": "CALL",
                             "quantity": -1, "avg_premium": 3.5, "strategy_tag": "cc"}],
            "cash": 50000.0, "estimated_nav": 74000.0, "nav_is_estimated": True,
            "max_loss_2pct": 1480.0, "max_loss_3pct": 2220.0,
        }))
        assert "100" in out and "$150.00" in out
        assert "2026-12-18" in out and "$220" in out
        assert "$74,000" in out
        assert "$1,480" in out and "$2,220" in out
        assert "无持仓记录" not in out

    def test_holdings_not_loaded_is_not_rendered_as_empty(self):
        out = format_options_context(_base_ctx())
        assert "Actual Holdings" in out
        assert "无持仓记录" not in out
        assert "UNKNOWN" in out

    def test_skew_and_expected_move(self):
        out = format_options_context(_base_ctx(
            skew_25d={"available": True, "snapshot_date": "2026-11-26",
                      "expiration": "2026-12-31", "dte": 35, "put_iv_25d": 0.38,
                      "call_iv_25d": 0.30, "atm_iv": 0.32, "skew_25d_pt": 8.0,
                      "skew_alpha": -0.25},
            expected_move={"days": 30, "iv": 0.32, "iv_source": "iv_summary.current_iv",
                           "move_1sigma": 18.37, "move_pct": 9.07,
                           "lower": 184.13, "upper": 220.87},
        ))
        assert "25Δ Skew" in out
        assert "38.0%" in out and "30.0%" in out and "8.0pt" in out and "-0.25" in out
        assert "30D 1σ Expected Move" in out and "18.37" in out

    def test_unavailable_skew_hidden(self):
        out = format_options_context(_base_ctx(skew_25d={"available": False, "reason": "no_snapshot"}))
        assert "25Δ Skew" not in out


class TestFormatDualProbabilitySummary:
    def _dual(self, **over):
        summary = {"expected_pnl": 0, "win_probability": 0.45, "median_pnl": -50,
                   "pctl_25": -200, "pctl_75": 150}
        dual = {
            "days_forward": 30, "r": 0.045, "market_iv": 0.30, "view_drift": 0.60,
            "view_vol": 0.30, "half_view_drift": 0.3225, "half_view_vol": 0.30,
            "skew_alpha": -0.2, "slippage_dollars": 15.0,
            "q_summary": dict(summary), "p_summary": dict(summary, win_probability=0.6),
            "half_view_summary": dict(summary, win_probability=0.52),
            "ev_q": -14.2, "ev_p": 120.5, "ev_half_view": 48.0, "edge_ev": 134.7,
            "expected_move_1sigma": 8.6, "expected_move_pct": 8.6,
            "target_price": 110.0, "prob_target_q": 0.18, "prob_target_p": 0.31,
        }
        dual.update(over)
        return dual

    def test_renders_three_distributions_and_edge(self):
        out = format_dual_probability_summary(self._dual())
        assert "Q" in out and "P" in out and "折半" in out
        assert "-14" in out and "+120" in out and "+48" in out
        assert "+135" in out or "+134.7" in out
        assert "$15" in out
        assert "18%" in out and "31%" in out

    def test_warns_when_half_view_flips_negative(self):
        out = format_dual_probability_summary(self._dual(ev_half_view=-5.0))
        assert "⚠️" in out

    def test_no_target_line_without_target(self):
        out = format_dual_probability_summary(
            self._dual(target_price=None, prob_target_q=None, prob_target_p=None))
        assert "Target" not in out
