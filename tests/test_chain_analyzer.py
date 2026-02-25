"""Tests for Chain Analyzer module."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from terminal.company_store import CompanyStore
from terminal.options.chain_analyzer import (
    fetch_and_store_chain,
    _parse_chain_response,
    analyze_liquidity,
    get_term_structure,
    filter_liquid_strikes,
    find_atm_options,
    get_earnings_proximity,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore."""
    db_path = tmp_path / "test.db"
    s = CompanyStore(db_path=db_path)
    s.upsert_company("AAPL")
    return s


def _sample_chain_response():
    """Sample MarketData.app chain response."""
    return {
        "s": "ok",
        "optionSymbol": [
            "AAPL260321C00200000",
            "AAPL260321P00200000",
            "AAPL260321C00210000",
            "AAPL260417C00200000",
        ],
        "expiration": [
            "2026-03-21", "2026-03-21", "2026-03-21", "2026-04-17",
        ],
        "strike": [200, 200, 210, 200],
        "side": ["call", "put", "call", "call"],
        "bid": [8.50, 6.00, 4.00, 12.00],
        "ask": [8.80, 6.30, 4.30, 12.50],
        "mid": [8.65, 6.15, 4.15, 12.25],
        "last": [8.70, 6.10, 4.10, 12.20],
        "volume": [1500, 800, 2000, 300],
        "openInterest": [25000, 18000, 30000, 5000],
        "iv": [0.28, 0.30, 0.32, 0.26],
        "delta": [0.55, -0.45, 0.35, 0.60],
        "gamma": [0.03, 0.03, 0.025, 0.02],
        "theta": [-0.15, -0.14, -0.12, -0.10],
        "vega": [0.25, 0.24, 0.22, 0.35],
        "dte": [25, 25, 25, 52],
        "inTheMoney": [True, False, False, True],
        "underlyingPrice": [202.50, 202.50, 202.50, 202.50],
    }


def _seed_snapshot(store, chain_data=None):
    """Seed store with a snapshot from chain data."""
    if chain_data is None:
        chain_data = _sample_chain_response()
    contracts = _parse_chain_response(chain_data, "AAPL")
    for c in contracts:
        c["underlying_price"] = 202.50
    store.save_options_snapshot("AAPL", "2026-02-24", contracts)


class TestParseChainResponse:
    """Test chain response parsing."""

    def test_parse_basic(self):
        data = _sample_chain_response()
        contracts = _parse_chain_response(data, "AAPL")
        assert len(contracts) == 4
        assert contracts[0]["expiration"] == "2026-03-21"
        assert contracts[0]["strike"] == 200
        assert contracts[0]["side"] == "call"
        assert contracts[0]["bid"] == 8.50
        assert contracts[0]["iv"] == 0.28
        assert contracts[0]["delta"] == 0.55

    def test_parse_empty(self):
        contracts = _parse_chain_response({"s": "ok"}, "AAPL")
        assert contracts == []

    def test_parse_missing_fields(self):
        """Should handle missing optional fields."""
        data = {
            "s": "ok",
            "optionSymbol": ["AAPL260321C00200000"],
            "strike": [200],
            "side": ["call"],
            # Missing bid, ask, etc.
        }
        contracts = _parse_chain_response(data, "AAPL")
        assert len(contracts) == 1
        assert contracts[0]["strike"] == 200
        assert contracts[0]["bid"] is None
        assert contracts[0]["iv"] is None


class TestFetchAndStoreChain:
    """Test fetch_and_store_chain."""

    def test_successful_fetch(self, store):
        mock_client = MagicMock()
        mock_client.get_options_chain.return_value = _sample_chain_response()

        result = fetch_and_store_chain("AAPL", store, client=mock_client)

        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["contract_count"] == 4
        assert len(result["expirations"]) == 2
        assert result["underlying_price"] == 202.50

    def test_no_data(self, store):
        mock_client = MagicMock()
        mock_client.get_options_chain.return_value = None

        result = fetch_and_store_chain("AAPL", store, client=mock_client)
        assert result is None

    def test_cleanup_called(self, store):
        """Should clean old snapshots after storing new ones."""
        mock_client = MagicMock()
        mock_client.get_options_chain.return_value = _sample_chain_response()

        with patch.object(store, "cleanup_old_snapshots") as mock_cleanup:
            fetch_and_store_chain("AAPL", store, client=mock_client)
            mock_cleanup.assert_called_once()


class TestAnalyzeLiquidity:
    """Test liquidity assessment."""

    def test_excellent_liquidity(self, store):
        """Tight spreads + high OI = EXCELLENT."""
        _seed_snapshot(store)
        result = analyze_liquidity("AAPL", store, "2026-02-24")

        assert result["verdict"] in ("EXCELLENT", "GOOD")
        assert result["avg_spread_pct"] is not None
        assert result["avg_oi"] is not None

    def test_no_data(self, store):
        result = analyze_liquidity("AAPL", store)
        assert result["verdict"] == "NO_GO"

    def test_poor_liquidity(self, store):
        """Wide spreads + low OI = POOR or NO_GO."""
        contracts = [
            {
                "expiration": "2026-03-21",
                "strike": 200,
                "side": "call",
                "bid": 5.00,
                "ask": 6.00,  # 20% spread
                "mid": 5.50,
                "volume": 10,
                "open_interest": 50,
                "underlying_price": 200,
            },
        ]
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        result = analyze_liquidity("AAPL", store, "2026-02-24")
        assert result["verdict"] in ("POOR", "NO_GO")


class TestTermStructure:
    """Test term structure extraction."""

    def test_term_structure(self, store):
        _seed_snapshot(store)
        ts = get_term_structure("AAPL", store, "2026-02-24")

        assert len(ts) == 2  # Two expirations
        assert ts[0]["dte"] < ts[1]["dte"]  # Sorted by DTE
        assert ts[0]["expiration"] == "2026-03-21"
        assert ts[1]["expiration"] == "2026-04-17"
        assert ts[0]["atm_iv"] > 0

    def test_empty_term_structure(self, store):
        ts = get_term_structure("AAPL", store)
        assert ts == []


class TestFilterLiquidStrikes:
    """Test liquid strike filtering."""

    def test_filter_basic(self, store):
        _seed_snapshot(store)
        liquid = filter_liquid_strikes(
            "AAPL", store, "2026-03-21", min_oi=200
        )
        # All our sample contracts have high OI, should pass
        assert len(liquid) >= 2

    def test_filter_high_oi_threshold(self, store):
        _seed_snapshot(store)
        liquid = filter_liquid_strikes(
            "AAPL", store, "2026-03-21", min_oi=50000
        )
        # None should pass with OI > 50000
        assert len(liquid) == 0


class TestFindATMOptions:
    """Test ATM option finding."""

    def test_find_atm(self, store):
        _seed_snapshot(store)
        result = find_atm_options("AAPL", store, "2026-03-21")

        assert result is not None
        assert result["atm_strike"] == 200  # Closest to 202.50
        assert result["underlying_price"] == 202.50
        assert result["call"] is not None
        assert result["put"] is not None
        assert result["call"]["side"] == "call"
        assert result["put"]["side"] == "put"

    def test_no_data(self, store):
        result = find_atm_options("AAPL", store, "2099-12-31")
        assert result is None


class TestEarningsProximity:
    """Test earnings proximity assessment."""

    def test_clear_zone(self):
        """No earnings in 90 days = CLEAR."""
        mock_fmp = MagicMock()
        mock_fmp.get_earnings_calendar.return_value = []

        result = get_earnings_proximity("AAPL", fmp_client=mock_fmp)
        assert result["zone"] == "CLEAR"

    def test_blackout_zone(self):
        """Earnings in 3 days = BLACKOUT."""
        mock_fmp = MagicMock()
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        mock_fmp.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "date": future},
        ]

        result = get_earnings_proximity("AAPL", fmp_client=mock_fmp)
        assert result["zone"] == "BLACKOUT"
        assert result["days_to_earnings"] in (2, 3)  # Off-by-one from time-of-day

    def test_caution_zone(self):
        """Earnings in 20 days = CAUTION."""
        mock_fmp = MagicMock()
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
        mock_fmp.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "date": future},
        ]

        result = get_earnings_proximity("AAPL", fmp_client=mock_fmp)
        assert result["zone"] == "CAUTION"

    def test_t5_warning_zone(self):
        """Earnings in 7 days = T5_WARNING."""
        mock_fmp = MagicMock()
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        mock_fmp.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "date": future},
        ]

        result = get_earnings_proximity("AAPL", fmp_client=mock_fmp)
        assert result["zone"] == "T5_WARNING"

    def test_symbol_case_insensitive(self):
        """Should match regardless of case."""
        mock_fmp = MagicMock()
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=50)).strftime("%Y-%m-%d")
        mock_fmp.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "date": future},
        ]

        result = get_earnings_proximity("aapl", fmp_client=mock_fmp)
        assert result["zone"] == "CLEAR"
        assert result["days_to_earnings"] in (49, 50)  # Off-by-one from time-of-day
