"""T20 B5 — migrate manual/analysis Core exceptions into local watchlist."""
import subprocess
import sys
from pathlib import Path


def test_script_bootstraps_project_imports_outside_repo_cwd(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_core_watchlist.py"
    code = (
        "import runpy; "
        f"m=runpy.run_path({str(script)!r}, run_name='migration_module'); "
        "r=m['migrate_core_watchlist']([], base_symbols=[], add_fn=lambda *a: None); "
        "assert r['migrated']==[]"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path,
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_migration_is_idempotent_and_excludes_etf():
    from scripts.migrate_core_watchlist import migrate_core_watchlist

    entries = [
        {"symbol": "AAPL", "source": "screener"},
        {"symbol": "SMALL", "source": "manual"},
        {"symbol": "ANALYSIS", "source": "analysis"},
        {"symbol": "SOXX", "source": "manual"},
    ]
    stored = set()

    def add(symbol, source):
        stored.add(symbol)

    first = migrate_core_watchlist(
        entries,
        base_symbols=["AAPL"],
        add_fn=add,
        etf_symbols=["SOXX"],
    )
    second = migrate_core_watchlist(
        entries,
        base_symbols=["AAPL"],
        add_fn=add,
        etf_symbols=["SOXX"],
    )

    assert stored == {"SMALL", "ANALYSIS"}
    assert first["migrated"] == ["ANALYSIS", "SMALL"]
    assert first["skipped_etf"] == ["SOXX"]
    assert second["migrated"] == ["ANALYSIS", "SMALL"]
    assert second["basket_covered"] == {"SOXX": "SOX"}
