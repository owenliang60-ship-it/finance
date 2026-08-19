"""Matrix #6 — daily price line splits into an FMP overlay tier + a yfinance batch.

Boss ruling P1: FMP daily price covers ONLY the overlay tier
(holdings ∪ watchlist ∪ benchmarks, ~50 names); everything else in the base
universe is priced by the yfinance batch. `daily_price` schema is unchanged,
and the day's combined coverage must never fall below the status quo.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import AUX_SYMBOLS, BENCHMARK_SYMBOLS


BASE_UNIVERSE = ["AAPL", "AMD", "CVX", "MSFT", "NVDA", "TSLA", "XOM"]
HOLDINGS = ["NVDA", "TSLA"]
WATCHLIST = ["AMD"]
LEGACY_CORE = ["AAPL", "AMD", "CVX", "MSFT", "NVDA", "TSLA", "XOM", "GOOG", "META"]


def _patch_overlay_tier(base=BASE_UNIVERSE):
    """Patch the resolver + overlay loaders `get_fmp_price_targets` reads."""
    return (
        patch("src.data.universe_resolver.current_base_universe", return_value=list(base)),
        patch("src.data.overlays.load_holdings", return_value=list(HOLDINGS)),
        patch("src.data.overlays.load_watchlist", return_value=list(WATCHLIST)),
        patch("src.data.overlays.load_benchmarks", return_value=list(BENCHMARK_SYMBOLS)),
    )


class TestFmpPriceTargets:
    def test_default_tier_is_overlays_only(self):
        from src.data.price_fetcher import get_fmp_price_targets

        p_base, p_h, p_w, p_b = _patch_overlay_tier()
        with p_base, p_h, p_w, p_b:
            targets = get_fmp_price_targets()

        assert set(targets) == set(HOLDINGS) | set(WATCHLIST) | set(BENCHMARK_SYMBOLS)
        # Strictly smaller than the legacy Core pool — that is the credit saving.
        assert len(targets) < len(LEGACY_CORE)

    def test_falls_back_to_legacy_core_pool_pre_bootstrap(self):
        """Between the Stop B merge and the Stop C bootstrap run membership is
        empty; narrowing FMP then would strand the Core pool with no price
        source, so the legacy targets must survive that window."""
        from src.data.price_fetcher import get_fmp_price_targets

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")), \
             patch("src.data.price_fetcher.get_symbols", return_value=list(LEGACY_CORE)):
            targets = get_fmp_price_targets()

        assert targets == LEGACY_CORE

    def test_update_all_prices_default_only_calls_fmp_for_the_tier(self):
        from src.data.price_fetcher import update_all_prices

        fetched = []

        def _fake_fetch(symbol, force_full=False):
            fetched.append(symbol)
            return pd.DataFrame({"date": ["2026-08-19"], "close": [1.0]})

        p_base, p_h, p_w, p_b = _patch_overlay_tier()
        with p_base, p_h, p_w, p_b, \
             patch("src.data.price_fetcher.fetch_and_update_price", side_effect=_fake_fetch):
            result = update_all_prices()

        overlay_tier = set(HOLDINGS) | set(WATCHLIST) | set(BENCHMARK_SYMBOLS)
        assert set(fetched) == overlay_tier | set(AUX_SYMBOLS)
        # FMP call count never exceeds the overlay tier (+ the fixed aux symbols).
        assert len(fetched) <= len(overlay_tier) + len(AUX_SYMBOLS)
        assert set(result["success"]) == overlay_tier | set(AUX_SYMBOLS)


class TestYfinanceLeg:
    def test_leg_targets_are_the_base_universe_minus_the_fmp_tier(self):
        from scripts.update_data import _yfinance_price_leg_targets

        fmp_tier = sorted(set(HOLDINGS) | set(WATCHLIST) | set(BENCHMARK_SYMBOLS))
        with patch("src.data.universe_resolver.current_base_universe",
                   return_value=list(BASE_UNIVERSE)):
            yf_targets = _yfinance_price_leg_targets(fmp_tier)

        assert set(yf_targets) == set(BASE_UNIVERSE) - set(fmp_tier)
        # Daily coverage >= status quo: the two legs together cover the base universe.
        assert set(BASE_UNIVERSE) <= set(fmp_tier) | set(yf_targets)

    def test_leg_is_empty_pre_bootstrap(self):
        """Pre-bootstrap the FMP tier is still the legacy Core pool, so the
        yfinance leg has nothing to add and must not raise."""
        from scripts.update_data import _yfinance_price_leg_targets

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")):
            assert _yfinance_price_leg_targets(LEGACY_CORE) == []
