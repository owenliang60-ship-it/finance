"""Matrix #9 — `--correlation` defaults to the overlay tier, `--wide` opts into eligible.

Correlation is O(n²); running it over the whole base universe by default was
never what anyone read. Default = holdings ∪ watchlist ∪ benchmarks (<100).
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import BENCHMARK_SYMBOLS


HOLDINGS = ["NVDA", "TSLA"]
WATCHLIST = ["AMD"]
BASE_UNIVERSE = [f"SYM{i:04d}" for i in range(400)]


def _patch_overlays():
    return (
        patch("src.data.overlays.load_holdings", return_value=list(HOLDINGS)),
        patch("src.data.overlays.load_watchlist", return_value=list(WATCHLIST)),
        patch("src.data.overlays.load_benchmarks", return_value=list(BENCHMARK_SYMBOLS)),
    )


class TestOverlayTierLoader:
    def test_unions_the_three_overlays(self):
        from src.data.overlays import load_overlay_tier

        p_h, p_w, p_b = _patch_overlays()
        with p_h, p_w, p_b:
            tier = load_overlay_tier()

        assert set(tier) == set(HOLDINGS) | set(WATCHLIST) | set(BENCHMARK_SYMBOLS)
        assert tier == sorted(tier)

    def test_needs_no_base_universe(self):
        """The overlay tier reads company.db + settings only, so it resolves
        before `extended_membership` is ever bootstrapped."""
        from src.data.overlays import load_overlay_tier

        p_h, p_w, p_b = _patch_overlays()
        with p_h, p_w, p_b, \
             patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")):
            assert set(load_overlay_tier()) == set(HOLDINGS) | set(WATCHLIST) | set(BENCHMARK_SYMBOLS)


class TestCorrelationSymbols:
    def test_default_is_the_overlay_tier_and_stays_small(self):
        from scripts.update_data import _resolve_correlation_symbols

        p_h, p_w, p_b = _patch_overlays()
        with p_h, p_w, p_b:
            symbols = _resolve_correlation_symbols()

        assert set(symbols) == set(HOLDINGS) | set(WATCHLIST) | set(BENCHMARK_SYMBOLS)
        assert len(symbols) < 100

    def test_wide_opts_into_resolver_eligible(self):
        from scripts.update_data import _resolve_correlation_symbols

        with patch("src.data.universe_resolver.current_base_universe",
                   return_value=list(BASE_UNIVERSE)):
            symbols = _resolve_correlation_symbols(wide=True)

        assert symbols == BASE_UNIVERSE

    def test_wide_fails_loud_pre_bootstrap(self):
        """`--wide` is an explicit opt-in: silently shrinking it back to the
        overlay tier would hide a broken universe."""
        from scripts.update_data import _resolve_correlation_symbols

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")):
            with pytest.raises(RuntimeError, match="extended_membership empty"):
                _resolve_correlation_symbols(wide=True)


class TestWideFlag:
    def test_flag_is_registered_and_defaults_off(self, monkeypatch, capsys):
        import scripts.update_data as mod

        monkeypatch.setattr(sys, "argv", ["update_data.py"])
        mod.main()  # no action flags -> prints help and returns
        assert "--wide" in capsys.readouterr().out
