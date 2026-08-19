"""Matrix #12 — theme scan runs over the resolver's eligible universe.

Theme scanning is local indicator maths, so widening the universe costs
nothing but changes the output scale. The parity contract on a frozen signal
fixture: symbols may enter a theme, but no symbol the Core-sized scan found
may disappear, and no theme may drop out of the ranking.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
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


def _price_frame(slope):
    days = 90
    x = np.arange(days)
    close = 100 * np.exp(slope * x) * (1 + 0.002 * np.sin(x / 3))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=days),
        "close": close,
        "volume": np.full(days, 1_000_000),
    })


class TestRealCrossSectionParity:
    def test_widening_recomputes_rs_and_records_the_expected_loss(self):
        """B2 review I3: exercise the real cross-sectional ranker.

        Expanding the denominator may legitimately push an old Core winner
        below P80.  The contract is that every old symbol remains observable
        in the new RS frame and the threshold loss is explicit, not silently
        caused by a missing price series.
        """
        from src.indicators.engine import run_momentum_scan

        slopes = {
            "AAPL": 0.0030,
            "AMD": 0.0025,
            "MU": 0.0020,
            "NVDA": 0.0035,
            "KO": 0.0010,
            "AVGO": 0.0060,
            "MRVL": 0.0055,
            "PLTR": 0.0050,
        }
        frames = {symbol: _price_frame(slope)
                  for symbol, slope in slopes.items()}

        with patch("src.indicators.engine.get_price_df",
                   side_effect=lambda symbol, max_age_days=0: frames[symbol]), \
             patch("src.indicators.dv_acceleration.scan_dv_acceleration",
                   return_value=pd.DataFrame()), \
             patch("src.indicators.rvol_sustained.scan_rvol_sustained",
                   return_value=[]):
            old_rs = run_momentum_scan(CORE_UNIVERSE)["rs_rating_b"]
            new_rs = run_momentum_scan(ELIGIBLE_UNIVERSE)["rs_rating_b"]

        old_rank = dict(zip(old_rs["symbol"], old_rs["rs_rank"]))
        new_rank = dict(zip(new_rs["symbol"], new_rs["rs_rank"]))
        assert set(CORE_UNIVERSE) <= set(new_rank)

        old_pass = {s for s, rank in old_rank.items() if rank >= 80}
        new_pass = {s for s, rank in new_rank.items() if rank >= 80}
        losses = old_pass - new_pass
        assert losses == {"NVDA"}
        assert (old_rank["NVDA"], new_rank["NVDA"]) == (99, 57)

        old_themes = match_themes(sorted(old_pass), seed=SEED)
        new_themes = match_themes(sorted(new_pass), seed=SEED)
        assert old_themes == {"ai_chip": ["NVDA"]}
        assert new_themes == {"ai_chip": ["AVGO", "MRVL"]}


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
