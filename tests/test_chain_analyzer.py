"""Tests for Chain Analyzer module."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.data.market_store import MarketStore
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
    """Create a fresh MarketStore."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
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

    def test_no_cleanup_per_symbol(self, store):
        """Cleanup should NOT be called per-symbol (moved to cron script)."""
        mock_client = MagicMock()
        mock_client.get_options_chain.return_value = _sample_chain_response()

        with patch.object(store, "cleanup_old_snapshots") as mock_cleanup:
            fetch_and_store_chain("AAPL", store, client=mock_client)
            mock_cleanup.assert_not_called()


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

    def test_atm_not_diluted_by_otm(self, store):
        """ATM liquidity should NOT be diluted by deep OTM contracts."""
        # 2 liquid ATM contracts + many illiquid deep OTM
        contracts = [
            {
                "expiration": "2026-03-21", "strike": 200, "side": "call",
                "bid": 8.50, "ask": 8.70, "mid": 8.60,
                "volume": 1500, "open_interest": 25000,
                "underlying_price": 202.50,
            },
            {
                "expiration": "2026-03-21", "strike": 200, "side": "put",
                "bid": 6.00, "ask": 6.15, "mid": 6.075,
                "volume": 800, "open_interest": 18000,
                "underlying_price": 202.50,
            },
        ]
        # Add 20 deep OTM contracts with terrible liquidity
        for i in range(20):
            contracts.append({
                "expiration": "2026-03-21", "strike": 300 + i * 10, "side": "call",
                "bid": 0.01, "ask": 0.10, "mid": 0.055,
                "volume": 1, "open_interest": 5,
                "underlying_price": 202.50,
            })
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        result = analyze_liquidity("AAPL", store, "2026-02-24")
        # Should NOT be NO_GO — ATM strikes are liquid
        assert result["verdict"] != "NO_GO"


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
    """Earnings gate: per-symbol lookup, three states (issue 044)."""

    @staticmethod
    def _fmp(rows=None, error=None):
        mock_fmp = MagicMock()
        if error is not None:
            mock_fmp.get_earnings.side_effect = error
        else:
            mock_fmp.get_earnings.return_value = rows
        return mock_fmp

    @staticmethod
    def _day(offset):
        from datetime import timedelta
        return (datetime.now().date() + timedelta(days=offset)).strftime("%Y-%m-%d")

    def _zone(self, offset):
        rows = [{"date": self._day(offset), "epsActual": None},
                {"date": self._day(-90), "epsActual": 1.0}]
        return get_earnings_proximity("AAPL", fmp_client=self._fmp(rows))

    def test_zone_boundaries_use_calendar_days(self):
        expected = {0: "BLACKOUT", 5: "BLACKOUT", 6: "T5_WARNING", 10: "T5_WARNING",
                    11: "CAUTION", 30: "CAUTION", 31: "CLEAR"}
        for offset, zone in expected.items():
            result = self._zone(offset)
            assert result["zone"] == zone, offset
            assert result["days_to_earnings"] == offset
            assert result["earnings_date"] == self._day(offset)

    def test_earnings_today_is_blackout_not_past(self):
        result = self._zone(0)
        assert result == {"days_to_earnings": 0, "earnings_date": self._day(0), "zone": "BLACKOUT"}

    def test_lookup_failure_is_unknown_never_clear(self):
        """FMP 404 / outage must not read as safe."""
        result = get_earnings_proximity("AAPL", fmp_client=self._fmp(error=RuntimeError("404")))
        assert result == {"days_to_earnings": None, "earnings_date": None, "zone": "UNKNOWN"}

    def test_non_list_payload_is_unknown(self):
        result = get_earnings_proximity("AAPL", fmp_client=self._fmp(rows=None))
        assert result["zone"] == "UNKNOWN"

    def test_reporter_without_upcoming_date_is_unknown(self):
        rows = [{"date": self._day(-30), "epsActual": 2.1}, {"date": self._day(-120), "epsActual": 1.9}]
        result = get_earnings_proximity("AAPL", fmp_client=self._fmp(rows))
        assert result["zone"] == "UNKNOWN"

    def test_etf_without_any_reported_eps_is_clear(self):
        """SOXX returns []; QQQ / SPY return old placeholder rows with no EPS."""
        assert get_earnings_proximity("SOXX", fmp_client=self._fmp([]))["zone"] == "CLEAR"
        placeholder = [{"date": "2008-02-16", "epsActual": None, "epsEstimated": None}]
        assert get_earnings_proximity("QQQ", fmp_client=self._fmp(placeholder))["zone"] == "CLEAR"

    def test_picks_closest_upcoming_date(self):
        rows = [{"date": self._day(40), "epsActual": None}, {"date": self._day(3), "epsActual": None},
                {"date": self._day(-1), "epsActual": 1.0}]
        result = get_earnings_proximity("aapl", fmp_client=self._fmp(rows))
        assert result["zone"] == "BLACKOUT"
        assert result["days_to_earnings"] == 3

    def test_queries_the_symbol_uppercased(self):
        mock_fmp = self._fmp([])
        get_earnings_proximity("aapl", fmp_client=mock_fmp)
        mock_fmp.get_earnings.assert_called_once_with("AAPL")
        mock_fmp.get_earnings_calendar.assert_not_called()

    def test_unparseable_dates_are_skipped(self):
        rows = [{"date": "n/a"}, {"date": ""}, {}, {"date": self._day(8), "epsActual": None}]
        assert get_earnings_proximity("AAPL", fmp_client=self._fmp(rows))["zone"] == "T5_WARNING"



class TestEarningsFormatterUnknown:
    """The formatter must never render an unavailable earnings gate as cleared."""

    def test_unknown_zone_is_rendered_as_unavailable(self):
        from terminal.options.formatter import format_options_context
        text = format_options_context({
            "symbol": "AAPL",
            "earnings": {"days_to_earnings": None, "earnings_date": None, "zone": "UNKNOWN"},
        })
        assert "DATA UNAVAILABLE" in text
        assert "UNKNOWN" in text
        assert "No upcoming earnings found" not in text
        assert "CLEAR" not in text

    def test_clear_zone_without_date_keeps_old_wording(self):
        from terminal.options.formatter import format_options_context
        text = format_options_context({
            "symbol": "SOXX",
            "earnings": {"days_to_earnings": None, "earnings_date": None, "zone": "CLEAR"},
        })
        assert "No upcoming earnings found — Zone: **CLEAR**" in text
