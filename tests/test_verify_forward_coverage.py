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
    monkeypatch.setattr("scripts.verify_forward_coverage.get_pool_symbols", lambda: pool)
    monkeypatch.setattr("scripts.verify_forward_coverage.get_extended_only_symbols", lambda: ext)


def test_full_coverage_within_date_window(temp_db, monkeypatch):
    db, con = temp_db
    for sym in ["AAPL", "NVDA", "EXT1", "EXT2"]:
        con.execute("INSERT INTO forward_estimates VALUES (?, '2026-05-09', '0y', 1.0)", (sym,))
    con.commit()
    _patch_loaders(monkeypatch, db, ["AAPL", "NVDA"], ["EXT1", "EXT2"])

    from scripts.verify_forward_coverage import run
    rc, report = run(scope="all", min_core_pct=99, min_extended_pct=95, min_date="2026-05-01")
    assert rc == 0
    assert report["core"]["covered"] == 2
    assert report["extended"]["covered"] == 2


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
    assert "NVDA" in report["core"]["missing"]


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
    assert "extended" not in report
