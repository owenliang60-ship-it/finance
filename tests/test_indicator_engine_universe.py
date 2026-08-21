"""Matrix #13 — indicator engine defaults resolve to the eligible universe.

Only the `symbols=None` defaults move; every explicit-symbols caller keeps its
exact targets.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


ELIGIBLE = ["AAPL", "AMD", "MSFT", "NVDA"]
LEGACY_CORE = ["AAPL", "NVDA"]


class TestDefaultUniverse:
    def test_resolver_eligible_is_the_default(self):
        from src.indicators.engine import _resolve_default_symbols

        with patch("src.data.universe_resolver.current_base_universe",
                   return_value=list(ELIGIBLE)):
            assert _resolve_default_symbols() == ELIGIBLE

    def test_falls_back_to_legacy_pool_pre_bootstrap(self):
        from src.indicators.engine import _resolve_default_symbols

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")), \
             patch("src.indicators.engine.get_symbols", return_value=list(LEGACY_CORE)):
            assert _resolve_default_symbols() == LEGACY_CORE

    def test_run_all_indicators_default_uses_eligible(self):
        import src.indicators.engine as mod

        seen = []
        with patch.object(mod, "_resolve_default_symbols", return_value=list(ELIGIBLE)), \
             patch.object(mod, "run_indicators",
                          side_effect=lambda s, ind: seen.append(s) or {"symbol": s}):
            results = mod.run_all_indicators()

        assert seen == ELIGIBLE
        assert set(results) == set(ELIGIBLE)

    def test_run_momentum_scan_default_uses_eligible(self):
        import src.indicators.engine as mod

        with patch.object(mod, "_resolve_default_symbols", return_value=list(ELIGIBLE)), \
             patch.object(mod, "get_price_df", return_value=None), \
             patch("src.indicators.rs_rating.compute_rs_rating_b", return_value=pd.DataFrame()), \
             patch("src.indicators.rs_rating.compute_rs_rating_c", return_value=pd.DataFrame()), \
             patch("src.indicators.dv_acceleration.scan_dv_acceleration", return_value=pd.DataFrame()), \
             patch("src.indicators.rvol_sustained.scan_rvol_sustained", return_value=[]):
            results = mod.run_momentum_scan()

        assert results["symbols_scanned"] == len(ELIGIBLE)


class TestExplicitSymbolsUnchanged:
    def test_run_all_indicators_explicit_list_never_touches_the_resolver(self):
        import src.indicators.engine as mod

        seen = []
        with patch.object(mod, "_resolve_default_symbols") as mock_default, \
             patch.object(mod, "run_indicators",
                          side_effect=lambda s, ind: seen.append(s) or {"symbol": s}):
            mod.run_all_indicators(["TSLA", "MU"])

        assert seen == ["TSLA", "MU"]
        assert not mock_default.called

    def test_run_momentum_scan_explicit_list_never_touches_the_resolver(self):
        import src.indicators.engine as mod

        with patch.object(mod, "_resolve_default_symbols") as mock_default, \
             patch.object(mod, "get_price_df", return_value=None), \
             patch("src.indicators.rs_rating.compute_rs_rating_b", return_value=pd.DataFrame()), \
             patch("src.indicators.rs_rating.compute_rs_rating_c", return_value=pd.DataFrame()), \
             patch("src.indicators.dv_acceleration.scan_dv_acceleration", return_value=pd.DataFrame()), \
             patch("src.indicators.rvol_sustained.scan_rvol_sustained", return_value=[]):
            results = mod.run_momentum_scan(["TSLA", "MU"])

        assert results["symbols_scanned"] == 2
        assert not mock_default.called

    def test_empty_explicit_list_is_not_treated_as_a_default(self):
        """`[]` means "nothing", not "everything" — the guard is `is None`."""
        import src.indicators.engine as mod

        with patch.object(mod, "_resolve_default_symbols") as mock_default, \
             patch.object(mod, "run_indicators") as mock_run:
            assert mod.run_all_indicators([]) == {}

        assert not mock_default.called
        assert not mock_run.called
