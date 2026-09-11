"""Tests for scripts/update_options_iv.py — explicit overlay IV targets (T16, R2/R5/R13).

Pre-T16, default IV targets came from the full pool (`pool_manager.get_symbols()`)
+ benchmarks. Post-T16 the default must be the explicit
holdings ∪ watchlist ∪ benchmarks overlay union via `resolve_universe(base="none", ...)`
— NEVER the full pool/extended universe (955/1003 symbols), since IV credits
are metered (~3 credits/symbol) and most of the pool has no options interest.
"""
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.update_options_iv as update_options_iv


class TestResolveIVTargets:
    def test_overlay_union(self):
        with patch("src.data.overlays.load_holdings", return_value=["AAPL"]), \
             patch("src.data.overlays.load_watchlist", return_value=["NEWCO"]), \
             patch("src.data.overlays.load_benchmarks", return_value=["SPY", "QQQ"]):
            targets = update_options_iv._resolve_iv_targets()
        assert set(targets) == {"AAPL", "NEWCO", "SPY", "QQQ"}
        assert len(targets) < 100

    def test_never_defaults_to_full_pool_size(self):
        # Simulate the pre-T16 pool-sized default (955/1003) never leaking
        # back in when overlays are legitimately small.
        with patch("src.data.overlays.load_holdings", return_value=[]), \
             patch("src.data.overlays.load_watchlist", return_value=[]), \
             patch("src.data.overlays.load_benchmarks", return_value=["SPY", "QQQ"]):
            targets = update_options_iv._resolve_iv_targets()
        assert sorted(targets) == ["QQQ", "SPY"]
        assert len(targets) < 100

    def test_dedupes_and_uppercases(self):
        with patch("src.data.overlays.load_holdings", return_value=["aapl"]), \
             patch("src.data.overlays.load_watchlist", return_value=["AAPL"]), \
             patch("src.data.overlays.load_benchmarks", return_value=["SPY", "QQQ"]):
            targets = update_options_iv._resolve_iv_targets()
        assert targets.count("AAPL") == 1
