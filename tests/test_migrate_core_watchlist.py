"""T20 B5 — migrate manual/analysis Core exceptions into local watchlist."""


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
