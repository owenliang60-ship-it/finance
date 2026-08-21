"""Tests for forward_estimates coverage verifier (with --min-date filter)."""
import sqlite3
import pytest


@pytest.fixture
def temp_db(tmp_path):
    """Minimal market.db with forward_estimates schema (含 date 列)."""
    db = tmp_path / "market.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE forward_estimates (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            period TEXT NOT NULL,
            eps_avg REAL,
            PRIMARY KEY (symbol, date, period)
        )
    """)
    con.commit()
    return db, con


def _patch_loaders(monkeypatch, db, pool, ext):
    monkeypatch.setattr("scripts.verify_forward_coverage.MARKET_DB", db)
    monkeypatch.setattr(
        "scripts.verify_forward_coverage.get_expected_buckets",
        lambda: {"overlay": sorted(set(pool)), "base": sorted(set(ext))},
    )


def test_full_coverage_within_date_window(temp_db, monkeypatch):
    db, con = temp_db
    for sym in ["AAPL", "NVDA", "EXT1", "EXT2"]:
        con.execute("INSERT INTO forward_estimates VALUES (?, '2026-05-09', '0y', 1.0)", (sym,))
    con.commit()
    _patch_loaders(monkeypatch, db, ["AAPL", "NVDA"], ["EXT1", "EXT2"])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="all", min_core_pct=99, min_extended_pct=95, min_date="2026-05-01")
    assert rc == 0
    assert report["overlay"]["covered"] == 2
    assert report["base"]["covered"] == 2


def test_old_data_excluded_by_min_date(temp_db, monkeypatch):
    """旧 row 不应被算作覆盖（防止 stale 误判）。"""
    db, con = temp_db
    # 旧数据
    con.execute("INSERT INTO forward_estimates VALUES ('AAPL', '2026-03-01', '0y', 1.0)")
    con.execute("INSERT INTO forward_estimates VALUES ('NVDA', '2026-03-01', '0y', 1.0)")
    # 本次只有 AAPL
    con.execute("INSERT INTO forward_estimates VALUES ('AAPL', '2026-05-09', '0y', 1.0)")
    con.commit()
    _patch_loaders(monkeypatch, db, ["AAPL", "NVDA"], [])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="core", min_core_pct=99, min_extended_pct=95, min_date="2026-05-01")
    assert rc == 1  # NVDA 在窗口内缺失
    assert "NVDA" in report["overlay"]["missing"]


def test_no_min_date_counts_all(temp_db, monkeypatch):
    """min_date=None 时不过滤时间，回退到全表 distinct（兼容场景）。"""
    db, con = temp_db
    con.execute("INSERT INTO forward_estimates VALUES ('AAPL', '2026-03-01', '0y', 1.0)")
    con.execute("INSERT INTO forward_estimates VALUES ('NVDA', '2026-03-01', '0y', 1.0)")
    con.commit()
    _patch_loaders(monkeypatch, db, ["AAPL", "NVDA"], [])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="core", min_core_pct=99, min_extended_pct=95, min_date=None)
    assert rc == 0


def test_scope_core_skips_extended(temp_db, monkeypatch):
    db, con = temp_db
    con.execute("INSERT INTO forward_estimates VALUES ('AAPL', '2026-05-09', '0y', 1.0)")
    con.execute("INSERT INTO forward_estimates VALUES ('NVDA', '2026-05-09', '0y', 1.0)")
    con.commit()
    _patch_loaders(monkeypatch, db, ["AAPL", "NVDA"], ["EXT1"])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="core", min_core_pct=99, min_extended_pct=95, min_date="2026-05-01")
    assert rc == 0
    assert "base" not in report


def test_empty_expected_fails_fast(temp_db, monkeypatch):
    """Empty pool / extended loader returning [] must FAIL, not silently pass.

    Triggered by missing data/pool/universe.json, broken symlink, or
    upstream loader bug. Reporting '0/0 OK' would defeat the verifier.
    """
    db, con = temp_db
    # Both loaders return []. Whether DB has rows or not is irrelevant —
    # the bucket fails because the expected universe is empty.
    _patch_loaders(monkeypatch, db, [], [])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="all", min_core_pct=99, min_extended_pct=95,
                     min_date="2026-05-01")
    assert rc == 1
    assert report["overlay"]["ok"] is False
    assert report["overlay"]["expected"] == 0
    assert report["overlay"]["pct"] == 0.0
    assert report["base"]["ok"] is False
    assert report["base"]["expected"] == 0


def test_extended_bucket_keeps_noncore_overlay_symbol(temp_db, monkeypatch):
    """B2 review C2: the verifier denominator must match scope=all after the
    price-target forwarder removes overlay names from extended-only."""
    db, con = temp_db
    con.execute("INSERT INTO forward_estimates VALUES ('CVX', '2026-05-09', '0y', 1.0)")
    con.commit()
    _patch_loaders(monkeypatch, db, [], ["CVX"])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="extended", min_core_pct=99,
                     min_extended_pct=95, min_date="2026-05-01")

    assert rc == 0
    assert report["base"]["expected"] == 1
    assert report["base"]["covered"] == 1


def test_base_and_overlay_buckets_are_disjoint_and_preserve_union(
        temp_db, monkeypatch):
    db, con = temp_db
    for sym in ["AAPL", "MSFT", "CVX"]:
        con.execute(
            "INSERT INTO forward_estimates VALUES (?, '2026-05-09', '0y', 1.0)",
            (sym,),
        )
    con.commit()
    monkeypatch.setattr("scripts.verify_forward_coverage.MARKET_DB", db)
    monkeypatch.setattr(
        "scripts.verify_forward_coverage.get_expected_buckets",
        lambda: {"overlay": ["AAPL", "CVX"], "base": ["MSFT"]},
    )

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="all", min_core_pct=99,
                     min_extended_pct=95, min_date="2026-05-01")

    assert rc == 0
    assert report["overlay"]["expected"] + report["base"]["expected"] == 3


def test_expected_buckets_legacy_fallback_only_for_prebootstrap(monkeypatch):
    import scripts.verify_forward_coverage as verifier

    monkeypatch.setattr(
        "src.data.universe_resolver.current_base_universe",
        lambda: (_ for _ in ()).throw(
            RuntimeError("extended_membership empty — run bootstrap first")
        ),
    )
    monkeypatch.setattr(verifier, "get_pool_symbols", lambda: ["AAPL"])
    monkeypatch.setattr(verifier, "get_extended_symbols", lambda: ["AAPL", "MSFT"])

    assert verifier.get_expected_buckets() == {
        "overlay": ["AAPL"], "base": ["MSFT"]
    }


def test_expected_buckets_propagates_database_corruption(monkeypatch):
    import scripts.verify_forward_coverage as verifier

    monkeypatch.setattr(
        "src.data.universe_resolver.current_base_universe",
        lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image malformed")),
    )

    with pytest.raises(sqlite3.DatabaseError):
        verifier.get_expected_buckets()


def test_expected_buckets_propagates_overlay_failure(monkeypatch):
    import scripts.verify_forward_coverage as verifier

    monkeypatch.setattr(
        "src.data.universe_resolver.current_base_universe", lambda: ["MSFT"]
    )
    monkeypatch.setattr(
        verifier, "load_overlay_tier",
        lambda: (_ for _ in ()).throw(RuntimeError("watchlist store unavailable")),
    )

    with pytest.raises(RuntimeError, match="watchlist store unavailable"):
        verifier.get_expected_buckets()
