"""Tests for Options Commands orchestrator + Formatter."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from terminal.company_store import CompanyStore
from terminal.options.commands import prepare_options_context, _get_deep_analysis, _get_oprms
from terminal.options.formatter import (
    format_options_context,
    format_chain_table,
    format_strategy_comparison,
    format_trade_memo,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore."""
    db_path = tmp_path / "test.db"
    s = CompanyStore(db_path=db_path)
    s.upsert_company("AAPL", company_name="Apple Inc.", sector="Technology")
    return s


@pytest.fixture
def store_with_data(store):
    """Store with OPRMS + analysis + IV data."""
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
    store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.28, hv_30d=0.22)
    store.save_iv_daily("AAPL", "2026-02-21", iv_30d=0.30, hv_30d=0.24)

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
    store.save_options_snapshot("AAPL", "2026-02-24", contracts)
    return store


class TestPrepareOptionsContext:
    """Test the main orchestrator."""

    def test_full_context(self, store_with_data):
        """Should return complete context with all fields."""
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
        mock_fmp.get_earnings_calendar.return_value = []

        ctx = prepare_options_context(
            "AAPL",
            store=store_with_data,
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
        mock_fmp = MagicMock()
        mock_fmp.get_earnings_calendar.return_value = []
        mock_fmp.get_realtime_price.return_value = 205.0

        ctx = prepare_options_context(
            "AAPL",
            store=store_with_data,
            fmp_client=mock_fmp,
            skip_chain_fetch=True,
        )

        assert ctx["underlying_price"] == 205.0
        assert ctx["chain_summary"] is None
        assert ctx["liquidity"] is not None

    def test_no_data_symbol(self, store):
        """Should handle symbol with no data gracefully."""
        mock_fmp = MagicMock()
        mock_fmp.get_earnings_calendar.return_value = []
        mock_fmp.get_realtime_price.return_value = None

        ctx = prepare_options_context(
            "UNKNOWN",
            store=store,
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
        result = _get_deep_analysis("AAPL", store_with_data)
        assert result["executive_summary"] is not None
        assert result["debate_verdict"] == "STRONG BUY"
        assert result["price_at_analysis"] == 200.0

    def test_no_analysis(self, store):
        result = _get_deep_analysis("AAPL", store)
        assert result == {}


class TestGetOPRMS:
    """Test OPRMS extraction."""

    def test_with_oprms(self, store_with_data):
        result = _get_oprms("AAPL", store_with_data)
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
