"""Matrix #11 — `backfill_iv.py --symbols` becomes required.

A manual credit-burning tool (9,500 MarketData credits/day) must never guess
its own targets: the old fallback quietly aimed a multi-day backfill at the
whole Core pool plus benchmarks.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestSymbolsRequired:
    def test_missing_symbols_exits_2(self, monkeypatch):
        import scripts.backfill_iv as mod

        monkeypatch.setattr(sys, "argv",
                            ["backfill_iv.py", "--start", "2026-02-17", "--end", "2026-02-18"])
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 2

    def test_no_pool_fallback_left_in_backfill(self):
        """The pool import is gone, not merely unused — a live import would let
        the fallback come back with a one-line edit."""
        import scripts.backfill_iv as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "pool_manager" not in source

    def test_explicit_symbols_are_upper_cased_and_used_verbatim(self):
        from scripts.backfill_iv import backfill

        mock_store = MagicMock()
        mock_store.get_iv_history.return_value = []

        args = MagicMock()
        args.symbols = ["aapl", "NVDA"]
        args.start = "2026-02-17"
        args.end = "2026-02-17"
        args.daily_limit = 9500
        args.dry_run = True

        with patch("scripts.backfill_iv.get_store", return_value=mock_store):
            backfill(args)

        # dry-run only reads existing dates; both explicit symbols, nothing else
        queried = {c.args[0] for c in mock_store.get_iv_history.call_args_list}
        assert queried == {"AAPL", "NVDA"}
