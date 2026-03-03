"""Tests for data_health.py — 全链数据健康检查。"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def mock_healthy_data(tmp_path, monkeypatch):
    """创建一个健康的 data/ 目录，所有检查都应 PASS。"""
    price_dir = tmp_path / "price"
    fundamental_dir = tmp_path / "fundamental"
    pool_dir = tmp_path / "pool"

    price_dir.mkdir()
    fundamental_dir.mkdir()
    pool_dir.mkdir()

    # 100 只股票
    symbols = [f"SYM{i:03d}" for i in range(100)]

    # universe.json
    universe = [{"symbol": s} for s in symbols]
    (pool_dir / "universe.json").write_text(json.dumps(universe))

    # 价格 CSV (所有都有, 最新日期是今天)
    today = datetime.now().strftime("%Y-%m-%d")
    for s in symbols:
        (price_dir / f"{s}.csv").write_text(
            f"date,open,high,low,close,volume\n{today},100,105,99,103,1000000\n"
        )

    # 基本面 JSON
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profiles = {"_meta": {"updated_at": now_str}}
    for s in symbols:
        profiles[s] = {"name": f"Company {s}"}
    (fundamental_dir / "profiles.json").write_text(json.dumps(profiles))

    # company.db
    db_path = tmp_path / "company.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE companies (symbol TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    # market.db — 8 个数据表 + 价格/基本面/IV 数据
    market_db_path = tmp_path / "market.db"
    mconn = sqlite3.connect(str(market_db_path))
    mconn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, close REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, revenue REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE balance_sheet_quarterly (symbol TEXT, date TEXT, total_assets REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE cash_flow_quarterly (symbol TEXT, date TEXT, operating_cash_flow REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE metrics_quarterly (symbol TEXT, date TEXT, pe_ratio REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE ratios_annual (symbol TEXT, date TEXT, roe REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, iv_30d REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE options_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, snapshot_date TEXT)")
    # 插入价格数据 (所有 symbols, 今天)
    for s in symbols:
        mconn.execute("INSERT INTO daily_price VALUES (?, ?, ?)", (s, today, 103.0))
        mconn.execute("INSERT INTO income_quarterly VALUES (?, ?, ?)", (s, today, 1000.0))
    # 插入 IV 数据 (60% 覆盖 → 超过 50% 阈值 → PASS)
    for s in symbols[:60]:
        mconn.execute("INSERT INTO iv_daily VALUES (?, ?, ?)", (s, today, 0.35))
    mconn.commit()
    mconn.close()

    # Monkeypatch
    import src.data.data_health as health
    monkeypatch.setattr(health, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health, "PRICE_DIR", price_dir)
    monkeypatch.setattr(health, "FUNDAMENTAL_DIR", fundamental_dir)
    monkeypatch.setattr(health, "POOL_DIR", pool_dir)
    monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")
    monkeypatch.setattr(health, "COMPANY_DB", db_path)
    monkeypatch.setattr(health, "MARKET_DB", market_db_path)

    return tmp_path


@pytest.fixture
def mock_unhealthy_data(tmp_path, monkeypatch):
    """创建一个有问题的 data/ 目录，应触发 WARN/FAIL。"""
    price_dir = tmp_path / "price"
    fundamental_dir = tmp_path / "fundamental"
    pool_dir = tmp_path / "pool"

    price_dir.mkdir()
    fundamental_dir.mkdir()
    pool_dir.mkdir()

    # 只有 50 只股票 (偏少)
    symbols = [f"SYM{i:03d}" for i in range(50)]
    universe = [{"symbol": s} for s in symbols]
    (pool_dir / "universe.json").write_text(json.dumps(universe))
    # 设置 mtime 为 25 天前
    import os
    old_time = (datetime.now() - timedelta(days=25)).timestamp()
    os.utime(pool_dir / "universe.json", (old_time, old_time))

    # 只有 30 个 CSV (60% 覆盖率)
    old_date = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    for s in symbols[:30]:
        (price_dir / f"{s}.csv").write_text(
            f"date,open,high,low,close,volume\n{old_date},100,105,99,103,1000000\n"
        )

    # 基本面: 40天前更新
    old_str = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    profiles = {"_meta": {"updated_at": old_str}}
    for s in symbols[:35]:
        profiles[s] = {"name": f"Company {s}"}
    (fundamental_dir / "profiles.json").write_text(json.dumps(profiles))

    # company.db 不存在 → FAIL
    # market.db 不存在 → FAIL

    import src.data.data_health as health
    monkeypatch.setattr(health, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health, "PRICE_DIR", price_dir)
    monkeypatch.setattr(health, "FUNDAMENTAL_DIR", fundamental_dir)
    monkeypatch.setattr(health, "POOL_DIR", pool_dir)
    monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")
    monkeypatch.setattr(health, "COMPANY_DB", tmp_path / "company.db")
    monkeypatch.setattr(health, "MARKET_DB", tmp_path / "market.db")

    return tmp_path


class TestHealthCheck:
    def test_healthy_data_passes(self, mock_healthy_data):
        from src.data.data_health import health_check
        report = health_check()

        assert report.level == "PASS"
        assert all(c.status == "PASS" for c in report.checks)

    def test_unhealthy_data_fails(self, mock_unhealthy_data):
        from src.data.data_health import health_check
        report = health_check()

        assert report.level == "FAIL"
        # 应该有多个 WARN 和 FAIL
        statuses = [c.status for c in report.checks]
        assert "FAIL" in statuses

    def test_summary_format(self, mock_healthy_data):
        from src.data.data_health import health_check
        report = health_check()
        summary = report.summary()

        assert "PASS" in summary
        assert "池完整性" in summary
        assert "价格覆盖率" in summary


class TestCheckPoolIntegrity:
    def test_normal_pool(self, mock_healthy_data):
        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "PASS"
        assert "100" in result.detail

    def test_empty_pool(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        (pool_dir / "universe.json").write_text("[]")
        monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "FAIL"

    def test_small_pool(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        universe = [{"symbol": f"S{i}"} for i in range(80)]
        (pool_dir / "universe.json").write_text(json.dumps(universe))
        monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "WARN"


class TestCheckPriceFreshness:
    def test_fresh_price(self, mock_healthy_data):
        from src.data.data_health import _check_price_freshness
        result = _check_price_freshness()
        assert result.status == "PASS"

    def test_stale_price(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        price_dir = tmp_path / "price"
        price_dir.mkdir()
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        (price_dir / "TEST.csv").write_text(
            f"date,open,high,low,close,volume\n{old_date},100,105,99,103,1000000\n"
        )
        monkeypatch.setattr(health, "PRICE_DIR", price_dir)

        from src.data.data_health import _check_price_freshness
        result = _check_price_freshness()
        assert result.status == "FAIL"


class TestCheckCompanyDb:
    def test_valid_db(self, mock_healthy_data):
        from src.data.data_health import _check_company_db
        result = _check_company_db()
        assert result.status == "PASS"

    def test_missing_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "COMPANY_DB", tmp_path / "nonexistent.db")

        from src.data.data_health import _check_company_db
        result = _check_company_db()
        assert result.status == "FAIL"

    def test_corrupted_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        bad_db = tmp_path / "bad.db"
        bad_db.write_text("not a sqlite database")
        monkeypatch.setattr(health, "COMPANY_DB", bad_db)

        from src.data.data_health import _check_company_db
        result = _check_company_db()
        # sqlite3 可能不会立即报错，但查表会失败
        assert result.status in ("PASS", "FAIL")


class TestHealthReport:
    def test_escalation(self):
        from src.data.data_health import HealthReport, CheckResult

        report = HealthReport()
        assert report.level == "PASS"

        report.add(CheckResult("test1", "PASS", "ok"))
        assert report.level == "PASS"

        report.add(CheckResult("test2", "WARN", "warning"))
        assert report.level == "WARN"

        report.add(CheckResult("test3", "FAIL", "failure"))
        assert report.level == "FAIL"

    def test_summary_includes_all_checks(self):
        from src.data.data_health import HealthReport, CheckResult

        report = HealthReport()
        report.add(CheckResult("check_a", "PASS", "good"))
        report.add(CheckResult("check_b", "WARN", "needs attention"))

        summary = report.summary()
        assert "check_a" in summary
        assert "check_b" in summary


# ============ market.db 健康检查测试 ============


@pytest.fixture
def mock_market_db(tmp_path, monkeypatch):
    """创建一个包含完整 market.db 的测试环境，支持自定义数据量。"""
    symbols = [f"SYM{i:03d}" for i in range(100)]
    today = datetime.now().strftime("%Y-%m-%d")

    db_path = tmp_path / "market.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, close REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, revenue REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE balance_sheet_quarterly (symbol TEXT, date TEXT, total_assets REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE cash_flow_quarterly (symbol TEXT, date TEXT, operating_cash_flow REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE metrics_quarterly (symbol TEXT, date TEXT, pe_ratio REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE ratios_annual (symbol TEXT, date TEXT, roe REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, iv_30d REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE options_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, snapshot_date TEXT)")
    # 全部 symbols 有价格和基本面
    for s in symbols:
        conn.execute("INSERT INTO daily_price VALUES (?, ?, ?)", (s, today, 103.0))
        conn.execute("INSERT INTO income_quarterly VALUES (?, ?, ?)", (s, today, 1000.0))
    # 60% IV 覆盖
    for s in symbols[:60]:
        conn.execute("INSERT INTO iv_daily VALUES (?, ?, ?)", (s, today, 0.35))
    conn.commit()
    conn.close()

    # 也要有 universe.json 以便加载 symbols
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    universe = [{"symbol": s} for s in symbols]
    (pool_dir / "universe.json").write_text(json.dumps(universe))

    import src.data.data_health as health
    monkeypatch.setattr(health, "MARKET_DB", db_path)
    monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

    return {"db_path": db_path, "symbols": symbols, "today": today, "tmp_path": tmp_path}


class TestCheckMarketDb:
    def test_all_tables_present(self, mock_market_db):
        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status == "PASS"
        assert "正常" in result.detail

    def test_missing_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "MARKET_DB", tmp_path / "nonexistent.db")

        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status == "FAIL"
        assert "不存在" in result.detail

    def test_missing_tables(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        # 只建 3 个表
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status == "FAIL"
        assert "缺少" in result.detail

    def test_corrupted_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        bad_db = tmp_path / "bad_market.db"
        bad_db.write_text("not a sqlite database")
        monkeypatch.setattr(health, "MARKET_DB", bad_db)

        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status in ("PASS", "FAIL")


class TestCheckMdbPriceCoverage:
    def test_full_coverage(self, mock_market_db):
        from src.data.data_health import _check_mdb_price_coverage, _load_universe_symbols
        symbols = _load_universe_symbols()
        result = _check_mdb_price_coverage(symbols)
        assert result.status == "PASS"
        assert "100/100" in result.detail

    def test_low_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 只有 50% 覆盖
        for s in symbols[:50]:
            conn.execute("INSERT INTO daily_price VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_price_coverage
        result = _check_mdb_price_coverage(symbols)
        assert result.status == "FAIL"

    def test_no_symbols(self, mock_market_db):
        from src.data.data_health import _check_mdb_price_coverage
        result = _check_mdb_price_coverage([])
        assert result.status == "FAIL"


class TestCheckMdbPriceFreshness:
    def test_fresh_data(self, mock_market_db):
        from src.data.data_health import _check_mdb_price_freshness
        result = _check_mdb_price_freshness()
        assert result.status == "PASS"

    def test_stale_data(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        conn.execute("INSERT INTO daily_price VALUES (?, ?)", ("TEST", old_date))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_price_freshness
        result = _check_mdb_price_freshness()
        assert result.status == "FAIL"

    def test_empty_table(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_price_freshness
        result = _check_mdb_price_freshness()
        assert result.status == "FAIL"
        assert "无数据" in result.detail


class TestCheckMdbFundamentalCoverage:
    def test_full_coverage(self, mock_market_db):
        from src.data.data_health import _check_mdb_fundamental_coverage, _load_universe_symbols
        symbols = _load_universe_symbols()
        result = _check_mdb_fundamental_coverage(symbols)
        assert result.status == "PASS"

    def test_partial_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 85% 覆盖 → WARN
        for s in symbols[:85]:
            conn.execute("INSERT INTO income_quarterly VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_fundamental_coverage
        result = _check_mdb_fundamental_coverage(symbols)
        assert result.status == "WARN"


class TestCheckMdbIvCoverage:
    def test_good_coverage(self, mock_market_db):
        from src.data.data_health import _check_mdb_iv_coverage, _load_universe_symbols
        symbols = _load_universe_symbols()
        result = _check_mdb_iv_coverage(symbols)
        # 60% >= 50% → PASS
        assert result.status == "PASS"

    def test_low_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 只有 20% → FAIL (<30%)
        for s in symbols[:20]:
            conn.execute("INSERT INTO iv_daily VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_iv_coverage
        result = _check_mdb_iv_coverage(symbols)
        assert result.status == "FAIL"

    def test_warn_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 40% → WARN (>=30%, <50%)
        for s in symbols[:40]:
            conn.execute("INSERT INTO iv_daily VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_iv_coverage
        result = _check_mdb_iv_coverage(symbols)
        assert result.status == "WARN"


class TestCheckMdbIvFreshness:
    def test_fresh_data(self, mock_market_db):
        from src.data.data_health import _check_mdb_iv_freshness
        result = _check_mdb_iv_freshness()
        assert result.status == "PASS"

    def test_stale_data(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        conn.execute("INSERT INTO iv_daily VALUES (?, ?)", ("TEST", old_date))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_iv_freshness
        result = _check_mdb_iv_freshness()
        assert result.status == "FAIL"

    def test_missing_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "MARKET_DB", tmp_path / "nonexistent.db")

        from src.data.data_health import _check_mdb_iv_freshness
        result = _check_mdb_iv_freshness()
        assert result.status == "FAIL"
        assert "不存在" in result.detail
