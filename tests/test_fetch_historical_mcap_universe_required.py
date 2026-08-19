"""Matrix #10 — `fetch_historical_mcap.py` loses its implicit `extended` default.

The old `default="extended"` plus a bare fallthrough in `_resolve_symbols`
meant a typo'd or forgotten flag silently backfilled ~949 symbols. Targets are
now always explicit: a universe name, or `--symbols`.
"""
import sys
from argparse import Namespace
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestUniverseIsRequired:
    def test_bare_invocation_exits_2(self, monkeypatch):
        import scripts.fetch_historical_mcap as mod

        monkeypatch.setattr(sys, "argv", ["fetch_historical_mcap.py"])
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 2

    def test_no_implicit_default_in_the_parser(self, monkeypatch):
        """A default would resurrect the silent 949-symbol backfill."""
        import scripts.fetch_historical_mcap as mod

        captured = {}
        monkeypatch.setattr(sys, "argv", ["fetch_historical_mcap.py", "--universe", "pool"])
        monkeypatch.setattr(mod, "_resolve_symbols", lambda args: captured.setdefault(
            "universe", args.universe) and [])
        monkeypatch.setattr(mod, "fetch_all", lambda *a, **k: None)
        mod.main()
        assert captured["universe"] == "pool"

    def test_explicit_symbols_still_work_without_a_universe(self, monkeypatch):
        import scripts.fetch_historical_mcap as mod

        captured = {}
        monkeypatch.setattr(sys, "argv",
                            ["fetch_historical_mcap.py", "--symbols", "AAPL", "nvda"])
        monkeypatch.setattr(mod, "fetch_all",
                            lambda symbols, **k: captured.setdefault("symbols", symbols))
        mod.main()
        assert captured["symbols"] == ["AAPL", "NVDA"]


class TestResolveSymbols:
    def test_missing_universe_raises_instead_of_falling_through(self):
        import scripts.fetch_historical_mcap as mod

        args = Namespace(symbols=None, universe=None)
        with pytest.raises(ValueError, match="--universe"):
            mod._resolve_symbols(args)

    def test_extended_is_now_an_explicit_branch(self, monkeypatch):
        import scripts.fetch_historical_mcap as mod

        monkeypatch.setattr(mod, "get_extended_symbols", lambda: ["AAPL", "XOM"])
        assert mod._resolve_symbols(Namespace(symbols=None, universe="extended")) == ["AAPL", "XOM"]
