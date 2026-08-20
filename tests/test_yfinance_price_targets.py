"""Matrix #7 — the yfinance price line resolves `current base universe − FMP tier`.

Replaces the old `get_extended_only_symbols()` (extended cache − core pool)
default. Acceptance: `targets ∪ FMP tier ⊇ current base universe`.
"""
import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


BASE_UNIVERSE = ["AAPL", "AMD", "CVX", "MSFT", "NVDA", "TSLA", "XOM"]
FMP_TIER = ["NVDA", "QQQ", "SPY", "TSLA"]
LEGACY_EXTENDED = ["AAPL", "AMD", "CVX", "MSFT", "NVDA", "TSLA", "XOM"]
LEGACY_CORE = ["MSFT", "NVDA", "TSLA"]


class TestYfinancePriceTargets:
    def test_targets_are_base_plus_legacy_core_minus_fmp_tier(self):
        from src.data.extended_price_fetcher import get_yfinance_price_targets

        with patch("src.data.universe_resolver.current_base_universe",
                   return_value=list(BASE_UNIVERSE)), \
             patch("src.data.price_fetcher.get_fmp_price_targets",
                   return_value=list(FMP_TIER)), \
             patch("src.data.pool_manager.get_symbols",
                   return_value=list(LEGACY_CORE)):
            targets = get_yfinance_price_targets()

        assert set(targets) == (set(BASE_UNIVERSE) | set(LEGACY_CORE)) - set(FMP_TIER)
        assert targets == sorted(targets)

    def test_targets_union_fmp_tier_cover_the_base_universe(self):
        from src.data.extended_price_fetcher import get_yfinance_price_targets

        with patch("src.data.universe_resolver.current_base_universe",
                   return_value=list(BASE_UNIVERSE)), \
             patch("src.data.price_fetcher.get_fmp_price_targets",
                   return_value=list(FMP_TIER)), \
             patch("src.data.pool_manager.get_symbols",
                   return_value=list(LEGACY_CORE)):
            targets = get_yfinance_price_targets()

        assert set(BASE_UNIVERSE) | set(LEGACY_CORE) <= set(targets) | set(FMP_TIER)

    def test_falls_back_to_legacy_extended_only_list_pre_bootstrap(self):
        """The daily cloud pipeline runs this before the Stop C bootstrap, so an
        empty membership table must degrade to the pre-migration target list,
        not to an empty fetch."""
        from src.data.extended_price_fetcher import get_yfinance_price_targets

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=RuntimeError("extended_membership empty — run bootstrap first")), \
             patch("src.data.extended_universe_manager.get_extended_symbols",
                   return_value=list(LEGACY_EXTENDED)), \
             patch("src.data.pool_manager.get_symbols", return_value=list(LEGACY_CORE)):
            targets = get_yfinance_price_targets()

        assert targets == sorted(set(LEGACY_EXTENDED) - set(LEGACY_CORE))

    def test_update_extended_prices_default_uses_the_resolver_targets(self):
        from src.data.extended_price_fetcher import update_extended_prices

        with patch("src.data.extended_price_fetcher.get_yfinance_price_targets",
                   return_value=[]) as mock_targets:
            result = update_extended_prices()

        assert mock_targets.called
        assert result["total"] == 0

    def test_database_corruption_does_not_fall_back_to_legacy_files(self):
        from src.data.extended_price_fetcher import get_yfinance_price_targets

        with patch("src.data.universe_resolver.current_base_universe",
                   side_effect=sqlite3.DatabaseError("database disk image malformed")):
            with pytest.raises(sqlite3.DatabaseError):
                get_yfinance_price_targets()


class TestUpdateExtendedPricesScript:
    def test_extended_branch_targets_come_from_the_resolver(self, monkeypatch):
        import scripts.update_extended_prices as mod

        expected = sorted(set(BASE_UNIVERSE) - set(FMP_TIER))
        captured = {}

        def _fake_update(full_backfill=False, symbols=None, start_date=None):
            captured["symbols"] = symbols
            return {"total": len(symbols or []), "success": 0,
                    "failed": [], "rows_inserted": 0}

        monkeypatch.setattr(sys, "argv", ["update_extended_prices.py", "--universe", "extended"])
        monkeypatch.setattr("src.data.extended_price_fetcher.update_extended_prices",
                            _fake_update)
        monkeypatch.setattr("src.data.extended_price_fetcher.get_yfinance_price_targets",
                            lambda *a, **k: list(expected))
        mod.main()

        assert captured["symbols"] == expected
