"""Matrix #12 — theme scan runs over the resolver's eligible universe.

Theme scanning is local indicator maths, so widening the universe costs
nothing but changes the output scale. The parity contract on a frozen signal
fixture: symbols may enter a theme, but no symbol the Core-sized scan found
may disappear, and no theme may drop out of the ranking.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scan_themes import get_momentum_tickers, match_themes


# --- frozen signal fixture: identical for both universes ---------------------
FROZEN_RS_B = pd.DataFrame({
    "symbol": ["NVDA", "AMD", "MU", "AVGO", "MRVL", "PLTR", "KO"],
    "rs_rank": [99, 95, 92, 97, 93, 91, 12],
})
FROZEN_MOMENTUM = {"rs_rating_b": FROZEN_RS_B, "rs_rating_c": None,
                   "dv_acceleration": None, "rvol_sustained": []}
FROZEN_INDICATORS = {"pmarp_crossovers": {"breakout_98": [], "recovery_2": []}}

SEED = {
    "ai_chip": {"tickers": ["NVDA", "AMD", "AVGO", "MRVL"]},
    "memory": {"tickers": ["MU"]},
    "ai_software": {"tickers": ["PLTR"]},
}

CORE_UNIVERSE = ["AMD", "KO", "MU", "NVDA"]
ELIGIBLE_UNIVERSE = ["AMD", "AVGO", "KO", "MRVL", "MU", "NVDA", "PLTR"]


def _ranking(theme_map):
    return [name for name, tickers in
            sorted(theme_map.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


class TestFrozenFixtureParity:
    def test_no_old_symbol_vanishes_from_its_theme(self):
        old = match_themes(
            get_momentum_tickers(FROZEN_MOMENTUM, FROZEN_INDICATORS, CORE_UNIVERSE),
            seed=SEED)
        new = match_themes(
            get_momentum_tickers(FROZEN_MOMENTUM, FROZEN_INDICATORS, ELIGIBLE_UNIVERSE),
            seed=SEED)

        for theme, tickers in old.items():
            assert theme in new, f"theme {theme} vanished"
            assert set(tickers) <= set(new[theme]), f"symbols dropped from {theme}"

    def test_widening_only_adds(self):
        old = match_themes(
            get_momentum_tickers(FROZEN_MOMENTUM, FROZEN_INDICATORS, CORE_UNIVERSE),
            seed=SEED)
        new = match_themes(
            get_momentum_tickers(FROZEN_MOMENTUM, FROZEN_INDICATORS, ELIGIBLE_UNIVERSE),
            seed=SEED)

        assert set(new["ai_chip"]) - set(old["ai_chip"]) == {"AVGO", "MRVL"}
        assert "ai_software" in new and "ai_software" not in old

    def test_top_n_ranking_keeps_every_old_theme(self):
        old = match_themes(
            get_momentum_tickers(FROZEN_MOMENTUM, FROZEN_INDICATORS, CORE_UNIVERSE),
            seed=SEED)
        new = match_themes(
            get_momentum_tickers(FROZEN_MOMENTUM, FROZEN_INDICATORS, ELIGIBLE_UNIVERSE),
            seed=SEED)

        top_n = len(_ranking(new))
        assert set(_ranking(old)) <= set(_ranking(new)[:top_n])
        # ai_chip led before and still leads — widening added to it, not past it
        assert _ranking(new)[0] == _ranking(old)[0] == "ai_chip"


class TestScanUniverseResolution:
    def test_scan_targets_are_the_resolver_eligible_universe(self):
        from scripts.scan_themes import _resolve_scan_symbols

        with patch("src.data.universe_resolver.current_base_universe",
                   return_value=list(ELIGIBLE_UNIVERSE)):
            assert _resolve_scan_symbols() == ELIGIBLE_UNIVERSE

    def test_falls_back_to_legacy_pool_pre_bootstrap(self):
        from scripts.scan_themes import _resolve_scan_symbols

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")), \
             patch("scripts.scan_themes.get_symbols", return_value=list(CORE_UNIVERSE)):
            assert _resolve_scan_symbols() == CORE_UNIVERSE

    def test_run_theme_scan_uses_the_resolved_universe(self, tmp_path):
        import scripts.scan_themes as mod

        seen = {}

        def _indicators(syms, parallel=False):
            seen["indicators"] = syms
            return {}

        def _momentum(syms, max_age_days=0):
            seen["momentum"] = syms
            return FROZEN_MOMENTUM

        with patch.object(mod, "_resolve_scan_symbols", return_value=list(ELIGIBLE_UNIVERSE)), \
             patch.object(mod, "run_all_indicators", side_effect=_indicators), \
             patch.object(mod, "get_indicator_summary", return_value=FROZEN_INDICATORS), \
             patch.object(mod, "run_momentum_scan", side_effect=_momentum), \
             patch.object(mod, "SCANS_DIR", tmp_path):
            mod.run_theme_scan()

        assert seen["indicators"] == ELIGIBLE_UNIVERSE
        assert seen["momentum"] == ELIGIBLE_UNIVERSE


def test_module_docstring_records_the_output_scale_change():
    import scripts.scan_themes as mod

    assert "eligible" in mod.__doc__
